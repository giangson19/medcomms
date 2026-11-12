import asyncio
import queue

import numpy as np
import pytest
import pytest_asyncio
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from medcomms.engine.codec import OpusCodec
from medcomms.engine.config import Config
from medcomms.engine.http import BUSY, create_app


class FakePipeline:
    def __init__(self, name):
        self.direction = Config().direction(name)
        self.audio = queue.Queue()
        self.results = queue.Queue()
        self.ready = True
        self.flushes = 0
        self.ends = 0

    def is_ready(self):
        return self.ready

    def flush(self):
        self.flushes += 1

    def end_session(self):
        self.ends += 1


@pytest.fixture
def pipelines():
    return {name: FakePipeline(name) for name in ("vi-en", "en-vi")}


@pytest_asyncio.fixture
async def client(pipelines):
    async with TestClient(TestServer(create_app(pipelines))) as client:
        yield client


async def wait_for(predicate, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_healthz_reports_model_loading(client, pipelines):
    for p in pipelines.values():
        p.ready = False
    assert (await client.get("/healthz")).status == 503
    for p in pipelines.values():
        p.ready = True
    assert (await client.get("/healthz")).status == 200


@pytest.mark.asyncio
async def test_unknown_direction_is_404(client):
    assert (await client.get("/ws/vi-fr")).status == 404


@pytest.mark.asyncio
async def test_session_streams_audio_in_and_results_out(client, pipelines, frames):
    vi_en = pipelines["vi-en"]
    codec = OpusCodec()
    async with client.ws_connect("/ws/vi-en") as ws:
        info = await ws.receive_json(timeout=2)
        assert info == {
            "type": "info",
            "direction": "vi-en",
            "asr": "vinai/PhoWhisper-small",
            "mt": "vinai/vinai-translate-vi2en-v2",
        }
        await ws.send_bytes(codec.encode(frames[0].tobytes()))
        await ws.send_bytes(b"not opus")  # dropped
        await ws.send_str("text frames are ignored")
        await ws.send_bytes(codec.encode(frames[1].tobytes()))
        await wait_for(lambda: vi_en.audio.qsize() == 2)
        received = vi_en.audio.get_nowait()
        assert received.dtype == np.int16 and len(received) == 640

        vi_en.results.put({"transcription": "Tôi đau đầu", "translation": "x"})
        assert (await ws.receive_json(timeout=2))["transcription"] == "Tôi đau đầu"
    await wait_for(lambda: vi_en.ends == 1)
    assert vi_en.flushes == 1  # on start; end_session clears the rest
    assert pipelines["en-vi"].audio.empty()


@pytest.mark.asyncio
async def test_directions_run_in_parallel_but_each_takes_one_session(client, pipelines):
    async with (
        client.ws_connect("/ws/vi-en") as first,
        client.ws_connect("/ws/en-vi") as other,
    ):
        await first.receive_json(timeout=2)
        await other.receive_json(timeout=2)

        async with client.ws_connect("/ws/vi-en") as second:
            msg = await second.receive(timeout=2)
            assert msg.type == WSMsgType.CLOSE
            assert msg.data == BUSY
            assert "in use" in msg.extra

        pipelines["en-vi"].results.put({"translation": "still works"})
        assert (await other.receive_json(timeout=2))["translation"] == "still works"


@pytest.mark.asyncio
async def test_end_message_finishes_the_sentence(client, pipelines):
    from medcomms.engine.worker import END

    vi_en = pipelines["vi-en"]
    async with client.ws_connect("/ws/vi-en") as ws:
        await ws.receive_json(timeout=2)
        await ws.send_str("not json")
        await ws.send_str("[1, 2]")
        await ws.send_str('{"type": "something else"}')
        await ws.send_str('{"type": "end"}')
        await wait_for(lambda: vi_en.audio.qsize() == 1)
        assert vi_en.audio.get_nowait() is END  # only the end message counts


@pytest.mark.asyncio
async def test_sessions_wait_until_the_models_are_loaded(
    client, pipelines, monkeypatch
):
    monkeypatch.setattr("medcomms.engine.http.READY_POLL_S", 0.01)
    vi_en = pipelines["vi-en"]
    vi_en.ready = False
    async with client.ws_connect("/ws/vi-en") as ws:
        with pytest.raises(asyncio.TimeoutError):
            await ws.receive(timeout=0.2)  # nothing until the models are ready
        assert vi_en.flushes == 0
        vi_en.ready = True
        assert (await ws.receive_json(timeout=2))["type"] == "info"


@pytest.mark.asyncio
async def test_a_client_leaving_while_models_load_frees_the_direction(
    client, pipelines, monkeypatch
):
    monkeypatch.setattr("medcomms.engine.http.READY_POLL_S", 0.01)
    pipelines["vi-en"].ready = False
    ws = await client.ws_connect("/ws/vi-en")
    await ws.close()
    await asyncio.sleep(0.1)
    pipelines["vi-en"].ready = True
    async with client.ws_connect("/ws/vi-en") as again:
        assert (await again.receive_json(timeout=2))["type"] == "info"
