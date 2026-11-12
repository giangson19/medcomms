"""The engine's HTTP interface.

    GET /healthz          200 once every model is loaded, 503 before
    WS  /ws/<direction>   Opus audio in, JSON out (e.g. /ws/vi-en)

The first message on a session describes the models in use:
``{"type": "info", "direction": ..., "asr": ..., "mt": ...}``. Every later
message is a result: ``{"type", "timestamp", "transcription", "translation"}``
where ``type`` is ``partial`` while an utterance is in progress and ``final``
once it has ended. The client may send ``{"type": "end"}`` to finish the
sentence in progress (e.g. when the speaker presses Stop).

Each direction accepts one session at a time, because a pipeline keeps the
state of a single conversation. Different directions run in parallel.
"""

import asyncio
import contextlib
import json
import logging
import queue

import numpy as np
from aiohttp import WSMsgType, web

from .codec import OpusCodec
from .pipeline import Pipeline
from .worker import END

log = logging.getLogger(__name__)

# Close code 1013 ("try again later") when a direction is already in use.
BUSY = 1013
_PIPELINES = web.AppKey("pipelines", dict)
_LOCKS = web.AppKey("locks", dict)


READY_POLL_S = 0.5


def create_app(pipelines: dict[str, Pipeline]) -> web.Application:
    app = web.Application()
    app[_PIPELINES] = pipelines
    app[_LOCKS] = {name: asyncio.Lock() for name in pipelines}
    app.router.add_get("/healthz", _healthz)
    app.router.add_get("/ws/{direction}", _session)
    return app


async def _healthz(request: web.Request) -> web.Response:
    if all(p.is_ready() for p in request.app[_PIPELINES].values()):
        return web.Response(text="ok\n")
    return web.Response(status=503, text="loading models\n")


async def _session(request: web.Request) -> web.WebSocketResponse:
    name = request.match_info["direction"]
    pipeline = request.app[_PIPELINES].get(name)
    if pipeline is None:
        raise web.HTTPNotFound(text="Unknown direction\n")
    lock = request.app[_LOCKS][name]

    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    if lock.locked():
        log.info("%s: rejected a second session", name)
        await ws.close(code=BUSY, message=f"{name} is in use".encode())
        return ws

    async with lock:
        # Right after a wake-up the models may still be loading: hold the
        # session until they are, so no speech is lost. The front door tells
        # the page the server is starting until ``info`` arrives.
        while not pipeline.is_ready():
            try:  # listen meanwhile, to notice the client leaving
                message = await ws.receive(timeout=READY_POLL_S)
            except TimeoutError:
                continue
            if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                return ws
        log.info("%s: session started", name)
        d = pipeline.direction
        await ws.send_json(
            {"type": "info", "direction": d.name, "asr": d.asr_model, "mt": d.mt_model}
        )
        pipeline.flush()
        sender = asyncio.create_task(_send_results(ws, pipeline))
        try:
            await _receive_audio(ws, pipeline)
        finally:
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sender
            pipeline.end_session()
            log.info("%s: session ended", name)
    return ws


async def _receive_audio(ws: web.WebSocketResponse, pipeline: Pipeline):
    codec = OpusCodec()  # one decoder per stream: Opus decoding is stateful
    async for message in ws:
        if message.type == WSMsgType.TEXT:
            if _is_end(message.data):
                pipeline.audio.put(END)
            continue
        if message.type != WSMsgType.BINARY:
            continue
        try:
            pcm = codec.decode(message.data)
        except Exception:
            log.warning(
                "%s: dropped an undecodable audio frame", pipeline.direction.name
            )
            continue
        pipeline.audio.put(np.frombuffer(pcm, dtype=np.int16))


def _is_end(text: str) -> bool:
    try:
        return json.loads(text).get("type") == "end"
    except (ValueError, AttributeError):
        return False


async def _send_results(ws: web.WebSocketResponse, pipeline: Pipeline):
    while not ws.closed:
        try:
            result = pipeline.results.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.02)
            continue
        try:
            await ws.send_json(result)
        except ConnectionError:
            return
