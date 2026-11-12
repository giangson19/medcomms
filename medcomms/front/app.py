"""The front door's HTTP server.

    GET  /                 the speaker page (files in medcomms/web)
    GET  /healthz          200 while the front door is up
    GET  /api/me           {"accounts": bool, "user": name or null,
                            "signup_open": bool}
    POST /api/signup       {"code", "username", "password", "direction"}
    POST /api/login        {"username", "password", "direction"}
    POST /api/logout
    GET  /api/history      the signed-in user's past sessions and sentences
    GET  /api/engine       ?direction=vi-en → {"state": "ready" | "starting" |
                           "unavailable"}, for the page's start-up message
    POST /api/feedback     ratings and corrections for one of their sentences
    WS   /ws/<direction>   relayed to that direction's engine

Signing up or in sets a cookie and wakes the selected direction's engine, so
it is likely ready by the time anyone speaks; nobody else can wake an engine.

A session on /ws/<direction>:

1. The front door connects to the engine (which may take a minute or two if
   the engine is waking up) and waits for its ``info`` message.
2. It sends ``{"type": "ready", "session": "<id>"}`` to the browser, which
   then starts the microphone.
3. Audio and results are relayed both ways and recorded; each final result
   gains an ``id`` that feedback refers to.

When an engine is not available the browser connection is closed with code
1013 and a short reason; the page shows it in its log only.
"""

import asyncio
import hmac
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
from aiohttp import WSMsgType, web

from .sessions import SessionStore
from .users import AccountError, UserStore

log = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
DIRECTIONS = {"vi-en": "Vietnamese → English", "en-vi": "English → Vietnamese"}
_CONTENT_TYPES = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".css": "text/css",
    ".wasm": "application/wasm",
    ".webp": "image/webp",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}
_COOKIE = "medcomms_session"
LOCAL_USER = "local"  # everyone, when accounts are off (local development)
UNAVAILABLE = 1013  # "try again later"
END_MESSAGE = '{"type": "end"}'  # from the page: finish the sentence in progress
UNAUTHORIZED = 4401
# A sleeping engine starts on its first request and then stays up until it
# has been idle for a while, so one wake-up per direction per minute is plenty.
WAKE_INTERVAL_S = 60
ENGINE_CHECK_TIMEOUT_S = 4


@dataclass(frozen=True)
class FrontConfig:
    """Settings for the front door.

    Args:
        engines: WebSocket address of each direction's engine.
        engine_headers: headers sent to the engines (e.g. Modal proxy auth).
        data_dir: user accounts and recorded sessions.
        accounts: require users to sign in; off only for local development.
        signup_code: lets new users create their own accounts; None closes
            sign-up (existing accounts keep working).
        record: record sessions (audio, results, feedback).
        max_session_minutes: sessions are closed after this long.
        engine_connect_timeout: seconds to wait for an engine to wake up.
        min_free_gb: stop recording audio below this much free disk space.
    """

    engines: dict[str, str] = field(default_factory=dict)
    engine_headers: dict[str, str] = field(default_factory=dict)
    data_dir: Path = Path("data")
    accounts: bool = True
    signup_code: str | None = None
    record: bool = True
    host: str = "127.0.0.1"
    port: int = 8000
    max_session_minutes: float = 60
    engine_connect_timeout: float = 300
    min_free_gb: float = 2.0


_CFG = web.AppKey("cfg", FrontConfig)
_USERS = web.AppKey("users", UserStore)
_STORE = web.AppKey("store", object)
_CLIENT = web.AppKey("client", aiohttp.ClientSession)
_WAKES = web.AppKey("wakes", dict)
_TASKS = web.AppKey("tasks", set)


def create_app(cfg: FrontConfig, web_root: Path = WEB_ROOT) -> web.Application:
    app = web.Application()
    app[_CFG] = cfg
    app[_USERS] = UserStore(cfg.data_dir)
    app[_STORE] = (
        SessionStore(cfg.data_dir, min_free_gb=cfg.min_free_gb) if cfg.record else None
    )
    app[_WAKES] = {}
    app[_TASKS] = set()
    app.cleanup_ctx.append(_client_session)

    app.router.add_get("/healthz", _healthz)
    app.router.add_get("/api/me", _me)
    app.router.add_post("/api/signup", _signup)
    app.router.add_post("/api/login", _login)
    app.router.add_post("/api/logout", _logout)
    app.router.add_get("/api/history", _history)
    app.router.add_get("/api/engine", _engine_state)
    app.router.add_post("/api/feedback", _feedback)
    app.router.add_get("/ws/{direction}", _session)

    files = {p.name: p for p in web_root.iterdir() if p.is_file()}

    async def static(request: web.Request) -> web.StreamResponse:
        path = files.get(request.match_info["name"] or "index.html")
        if path is None:
            raise web.HTTPNotFound()
        return web.FileResponse(
            path,
            headers={
                "Content-Type": _CONTENT_TYPES.get(
                    path.suffix, "application/octet-stream"
                ),
                "Cache-Control": "no-cache",
            },
        )

    app.router.add_get("/{name:[^/]*}", static)
    return app


async def _client_session(app: web.Application):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as s:
        app[_CLIENT] = s
        yield


async def _healthz(request: web.Request) -> web.Response:
    return web.Response(text="ok\n")


# Accounts -------------------------------------------------------------------


def current_user(request: web.Request) -> str | None:
    if not request.app[_CFG].accounts:
        return LOCAL_USER
    cookie = request.cookies.get(_COOKIE)
    return request.app[_USERS].user_from_cookie(cookie) if cookie else None


async def _json(request: web.Request) -> dict:
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _signed_in_response(request: web.Request, name: str, status: int) -> web.Response:
    users = request.app[_USERS]
    users.touch(name)
    response = web.json_response({"user": name}, status=status)
    https = request.secure or request.headers.get("X-Forwarded-Proto") == "https"
    response.set_cookie(
        _COOKIE,
        users.cookie_for(name),
        max_age=30 * 24 * 3600,
        httponly=True,
        samesite="Strict",
        secure=https,
    )
    return response


async def _me(request: web.Request) -> web.Response:
    cfg = request.app[_CFG]
    return web.json_response(
        {
            "accounts": cfg.accounts,
            "user": current_user(request),
            "signup_open": bool(cfg.accounts and cfg.signup_code),
        }
    )


async def _signup(request: web.Request) -> web.Response:
    cfg = request.app[_CFG]
    data = await _json(request)
    code = str(data.get("code", ""))
    if not cfg.signup_code or not hmac.compare_digest(
        code.encode(), cfg.signup_code.encode()
    ):
        await asyncio.sleep(1)  # slow down guessing
        raise web.HTTPForbidden(text="That sign-up code is not valid.")
    try:
        request.app[_USERS].create(
            str(data.get("username", "")), str(data.get("password", ""))
        )
    except AccountError as e:
        raise web.HTTPBadRequest(text=str(e))
    name = str(data["username"]).strip().lower()
    log.info("New account: %s", name)
    _wake_from(request, data)
    return _signed_in_response(request, name, status=201)


async def _login(request: web.Request) -> web.Response:
    data = await _json(request)
    try:
        name = request.app[_USERS].authenticate(
            str(data.get("username", "")), str(data.get("password", ""))
        )
    except AccountError as e:
        await asyncio.sleep(1)  # slow down guessing
        raise web.HTTPUnauthorized(text=str(e))
    _wake_from(request, data)
    return _signed_in_response(request, name, status=200)


async def _logout(request: web.Request) -> web.Response:
    response = web.Response(status=204)
    response.del_cookie(_COOKIE)
    return response


# Waking engines -------------------------------------------------------------


def _wake_from(request: web.Request, data: dict):
    # Signing in is the signal that a demo is about to start.
    if data.get("direction") in DIRECTIONS:
        _wake(request.app, data["direction"])


def _wake(app: web.Application, name: str):
    """Ask a direction's engine to start, without waiting for it."""
    url = app[_CFG].engines.get(name)
    now = asyncio.get_running_loop().time()
    if not url or now - app[_WAKES].get(name, -WAKE_INTERVAL_S) < WAKE_INTERVAL_S:
        return
    app[_WAKES][name] = now
    base = url.replace("wss://", "https://", 1).replace("ws://", "http://", 1)
    health = base.rsplit("/ws/", 1)[0] + "/healthz"
    task = asyncio.create_task(_ping(app, name, health))
    app[_TASKS].add(task)  # keep a reference until it finishes
    task.add_done_callback(app[_TASKS].discard)


async def _ping(app: web.Application, name: str, url: str):
    try:
        async with app[_CLIENT].get(
            url,
            headers=app[_CFG].engine_headers,
            timeout=aiohttp.ClientTimeout(total=app[_CFG].engine_connect_timeout),
        ) as response:
            log.info("%s: woke the engine (HTTP %d)", name, response.status)
    except (aiohttp.ClientError, OSError, TimeoutError) as e:
        log.info("%s: wake-up request failed: %s", name, e or type(e).__name__)


async def _engine_state(request: web.Request) -> web.Response:
    """Whether a direction's engine is ready, still starting, or unavailable.

    Asking also keeps a starting engine coming up; only signed-in users can
    ask, so strangers cannot wake engines.
    """
    if current_user(request) is None:
        raise web.HTTPUnauthorized()
    name = request.query.get("direction", "")
    cfg = request.app[_CFG]
    url = cfg.engines.get(name) if name in DIRECTIONS else None
    if not url:
        return web.json_response({"state": "unavailable"})
    base = url.replace("wss://", "https://", 1).replace("ws://", "http://", 1)
    health = base.rsplit("/ws/", 1)[0] + "/healthz"
    try:
        async with request.app[_CLIENT].get(
            health,
            headers=cfg.engine_headers,
            timeout=aiohttp.ClientTimeout(total=ENGINE_CHECK_TIMEOUT_S),
        ) as response:
            status = response.status
    except TimeoutError:
        status = None  # the engine's machine is still being started
    except (aiohttp.ClientError, OSError):
        return web.json_response({"state": "unavailable"})
    if status == 200:
        state = "ready"
    elif status in (None, 503):  # 503: up, still loading models
        state = "starting"
    else:
        state = "unavailable"
    return web.json_response({"state": state})


# History and feedback -------------------------------------------------------


async def _history(request: web.Request) -> web.Response:
    user = current_user(request)
    if user is None:
        raise web.HTTPUnauthorized()
    store = request.app[_STORE]
    sessions = await asyncio.to_thread(store.history, user) if store else []
    return web.json_response({"sessions": sessions})


def _text(value, limit=5000) -> str:
    return str(value or "")[:limit]


def _rating(value) -> int:
    return value if isinstance(value, int) and 0 <= value <= 5 else 0


async def _feedback(request: web.Request) -> web.Response:
    user = current_user(request)
    if user is None:
        raise web.HTTPUnauthorized()
    store = request.app[_STORE]
    if store is None:
        return web.Response(status=204)  # recording is off
    data = await _json(request)
    if "session" not in data:
        raise web.HTTPBadRequest(text="expected JSON with a session id\n")
    feedback = {
        "item": _text(data.get("item"), 64),
        "source": _text(data.get("source")),
        "translation": _text(data.get("translation")),
        "transcription_rating": _rating(data.get("transcription_rating")),
        "translation_rating": _rating(data.get("translation_rating")),
        "correction": _text(data.get("correction")),
    }
    if not store.add_feedback(str(data["session"]), feedback, user=user):
        raise web.HTTPNotFound(text="unknown session\n")
    return web.Response(status=204)


# Sessions -------------------------------------------------------------------


async def _session(request: web.Request) -> web.WebSocketResponse:
    name = request.match_info["direction"]
    if name not in DIRECTIONS:
        raise web.HTTPNotFound()
    cfg = request.app[_CFG]
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)

    user = current_user(request)
    if user is None:
        await ws.close(code=UNAUTHORIZED, message=b"please sign in")
        return ws

    unavailable = f"{DIRECTIONS[name]} is not available right now".encode()
    url = cfg.engines.get(name)
    if not url:
        await ws.close(code=UNAVAILABLE, message=unavailable)
        return ws

    try:
        engine, info = await asyncio.wait_for(
            _connect(request.app[_CLIENT], url, cfg.engine_headers),
            cfg.engine_connect_timeout,
        )
    except EngineClosed as closed:
        await ws.close(code=closed.code, message=closed.reason.encode())
        return ws
    except (aiohttp.ClientError, OSError, TimeoutError) as e:
        log.warning("%s: engine unavailable: %s", name, e or type(e).__name__)
        await ws.close(code=UNAVAILABLE, message=unavailable)
        return ws

    store = request.app[_STORE]
    session = store.open(name, user=user) if store else None
    if session:
        session.add_message(info)
    label = f"{name}: session {session.id if session else '-'} ({user})"
    log.info("%s started", label)
    await ws.send_json({"type": "ready", "session": session.id if session else None})

    code, reason = 1000, b""
    try:
        async with asyncio.timeout(cfg.max_session_minutes * 60):
            code, reason = await _relay(ws, engine, session)
    except TimeoutError:
        code, reason = 1000, b"session time limit reached"
    finally:
        if session:
            session.close()
        await engine.close()
        await ws.close(code=code, message=reason)
        log.info("%s ended", label)
    return ws


def _is_end(text: str) -> bool:
    try:
        return json.loads(text).get("type") == "end"
    except (ValueError, AttributeError):
        return False


class EngineClosed(Exception):
    def __init__(self, code: int, reason: str):
        super().__init__(reason)
        self.code, self.reason = code, reason


async def _connect(client: aiohttp.ClientSession, url: str, headers: dict):
    """Open the engine connection and read its ``info`` message."""
    engine = await client.ws_connect(url, headers=headers, heartbeat=20)
    msg = await engine.receive()
    if msg.type == WSMsgType.TEXT:
        return engine, json.loads(msg.data)
    await engine.close()
    if msg.type == WSMsgType.CLOSE:
        raise EngineClosed(msg.data, msg.extra or "engine closed the session")
    raise EngineClosed(UNAVAILABLE, "engine closed the session")


async def _relay(ws: web.WebSocketResponse, engine, session) -> tuple[int, bytes]:
    """Relay until either side closes; return the close code and reason."""
    outcome = (1000, b"")

    async def browser_to_engine():
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                if session:
                    session.add_audio(msg.data)
                await engine.send_bytes(msg.data)
            elif msg.type == WSMsgType.TEXT and _is_end(msg.data):
                await engine.send_str(END_MESSAGE)  # finish the sentence

    async def engine_to_browser():
        nonlocal outcome
        while True:
            msg = await engine.receive()
            if msg.type == WSMsgType.TEXT:
                text = msg.data
                if session:
                    try:
                        text = json.dumps(
                            session.add_message(json.loads(text)), ensure_ascii=False
                        )
                    except json.JSONDecodeError:
                        pass
                await ws.send_str(text)
            elif msg.type == WSMsgType.CLOSE:
                outcome = (msg.data, (msg.extra or "").encode())
                return
            elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                outcome = (UNAVAILABLE, b"lost connection to the engine")
                return

    tasks = [
        asyncio.create_task(browser_to_engine()),
        asyncio.create_task(engine_to_browser()),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return outcome


def run(cfg: FrontConfig):
    log.info("Front door on http://%s:%d", cfg.host, cfg.port)
    for name in DIRECTIONS:
        log.info("%s engine: %s", name, cfg.engines.get(name, "not configured"))
    log.info("Data in %s", Path(cfg.data_dir).resolve())
    if not cfg.accounts:
        log.warning("Accounts are off: anyone who can open the page can use it")
    elif not cfg.signup_code:
        log.info("Sign-up is closed (no sign-up code set)")
    web.run_app(create_app(cfg), host=cfg.host, port=cfg.port, print=None)
