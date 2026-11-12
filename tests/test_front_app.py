import asyncio
import json

import pytest
import pytest_asyncio
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from medcomms.front import app as front_app
from medcomms.front.app import UNAUTHORIZED, UNAVAILABLE, FrontConfig, create_app
from medcomms.front.users import UserStore


class FakeEngine:
    """Stands in for an engine: answers each audio frame with a partial and a
    final result, and counts health checks (wake-ups)."""

    def __init__(self):
        self.audio: list[bytes] = []
        self.mode = "normal"  # or "busy", "hang-up"
        self.health = 200  # or 503 (loading models), or "hang" (machine starting)
        self.headers = None
        self.health_checks = 0
        self.texts: list[str] = []

    def app(self):
        app = web.Application()
        app.router.add_get("/ws/{direction}", self.session)
        app.router.add_get("/healthz", self.healthz)
        return app

    async def healthz(self, request):
        self.health_checks += 1
        self.headers = dict(request.headers)
        if self.health == "hang":
            await asyncio.Event().wait()  # never answers
        return web.Response(
            text="ok", status=200 if self.health == "hang" else self.health
        )

    async def session(self, request):
        self.headers = dict(request.headers)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        if self.mode == "busy":
            await ws.close(code=1013, message=b"vi-en is in use")
            return ws
        await ws.send_json({"type": "info", "asr": "asr-model", "mt": "mt-model"})
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:  # Stop: finish the sentence
                self.texts.append(msg.data)
                await ws.send_json(
                    {"type": "final", "transcription": "last", "translation": "cuối"}
                )
                continue
            self.audio.append(msg.data)
            if self.mode == "hang-up":
                await ws.close(code=1011, message=b"engine error")
                break
            n = len(self.audio)
            await ws.send_json({"type": "partial", "transcription": f"heard {n}"})
            await ws.send_json(
                {"type": "final", "timestamp": "2026-01-01T00:00:00+00:00",
                 "transcription": f"heard {n}", "translation": f"nghe {n}"}
            )  # fmt: skip
        return ws


@pytest_asyncio.fixture
async def engine():
    fake = FakeEngine()
    async with TestServer(fake.app()) as server:
        fake.url = str(server.make_url("/ws/vi-en")).replace("http", "ws", 1)
        yield fake


def front(**kwargs):
    @pytest_asyncio.fixture
    async def fixture(engine, tmp_path):
        options = {
            "engines": {"vi-en": engine.url},
            "engine_headers": {"Modal-Key": "k", "Modal-Secret": "s"},
            "data_dir": tmp_path,
            "signup_code": "test-code",
            **kwargs,
        }
        async with TestClient(TestServer(create_app(FrontConfig(**options)))) as c:
            yield c

    return fixture


client = front()  # accounts on, sign-up open
closed = front(signup_code=None)
open_access = front(accounts=False)
unrecorded = front(accounts=False, record=False)
short = front(accounts=False, max_session_minutes=0.01)


@pytest.fixture(autouse=True)
def fast_failures(monkeypatch):
    """Wrong passwords and codes are slowed down; not in tests."""
    real_sleep = asyncio.sleep

    async def sleep(seconds, *args):
        await real_sleep(0 if seconds >= 1 else seconds, *args)

    monkeypatch.setattr(front_app.asyncio, "sleep", sleep)


async def signup(client, username="tester01", password="password1", **extra):
    body = {"code": "test-code", "username": username, "password": password, **extra}
    return await client.post("/api/signup", json=body)


async def start(client, direction="vi-en"):
    ws = await client.ws_connect(f"/ws/{direction}")
    return ws, await ws.receive(timeout=5)


async def converse(client, frames=2):
    """A short session; returns (session id, messages received)."""
    ws, ready = await start(client)
    session_id = json.loads(ready.data)["session"]
    messages = []
    for i in range(frames):
        await ws.send_bytes(b"\xf8audio-%d" % i)
        messages += [await ws.receive_json(timeout=5) for _ in range(2)]
    await ws.close()
    await asyncio.sleep(0.1)  # the front door finishes the session
    return session_id, messages


# Page -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_serves_the_speaker_page(client):
    page = await client.get("/")
    assert page.status == 200
    assert page.headers["Content-Type"].startswith("text/html")
    assert "MedComms" in await page.text()
    wasm = await client.get("/libopus.wasm")
    assert wasm.headers["Content-Type"] == "application/wasm"
    assert (
        (await client.get("/app.js"))
        .headers["Content-Type"]
        .startswith("text/javascript")
    )
    assert (await client.get("/missing.js")).status == 404
    assert (await client.get("/healthz")).status == 200
    assert (await client.get("/vinuni-mark.webp")).headers[
        "Content-Type"
    ] == "image/webp"


# Accounts -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_up_then_sign_in(client):
    me = await (await client.get("/api/me")).json()
    assert me == {"accounts": True, "user": None, "signup_open": True}

    response = await signup(client, username="Tester01")
    assert response.status == 201
    assert (await response.json()) == {"user": "tester01"}
    assert (await (await client.get("/api/me")).json())["user"] == "tester01"

    assert (await client.post("/api/logout")).status == 204
    assert (await (await client.get("/api/me")).json())["user"] is None

    wrong = await client.post(
        "/api/login", json={"username": "tester01", "password": "nope"}
    )
    assert wrong.status == 401
    assert "Wrong username or password" in await wrong.text()
    ok = await client.post(
        "/api/login", json={"username": "TESTER01", "password": "password1"}
    )
    assert ok.status == 200
    assert (await (await client.get("/api/me")).json())["user"] == "tester01"


@pytest.mark.asyncio
async def test_sign_up_problems_are_explained(client):
    assert (await signup(client, code="wrong")).status == 403
    bad = await signup(client, username="x")
    assert bad.status == 400 and "Usernames" in await bad.text()
    assert (await signup(client)).status == 201
    taken = await signup(client)
    assert taken.status == 400 and "taken" in await taken.text()
    assert (await client.post("/api/signup", data="not json")).status == 403


@pytest.mark.asyncio
async def test_sign_up_closed_without_a_code(closed, tmp_path):
    assert (await (await closed.get("/api/me")).json())["signup_open"] is False
    assert (await signup(closed)).status == 403
    # Accounts created by an operator still work.
    UserStore(tmp_path).create("tester01", "password1")
    response = await closed.post(
        "/api/login", json={"username": "tester01", "password": "password1"}
    )
    assert response.status == 200


@pytest.mark.asyncio
async def test_cookie_settings(client):
    response = await signup(client, **{})
    cookie = response.cookies["medcomms_session"]
    assert cookie["httponly"] and cookie["samesite"] == "Strict"
    assert not cookie["secure"]  # the test server is plain http
    await client.post("/api/logout")
    response = await client.post(
        "/api/login",
        json={"username": "tester01", "password": "password1"},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert response.cookies["medcomms_session"]["secure"]


@pytest.mark.asyncio
async def test_disabled_accounts_are_signed_out(client, tmp_path):
    await signup(client)
    UserStore(tmp_path).update("tester01", disabled=True)
    assert (await (await client.get("/api/me")).json())["user"] is None
    _, msg = await start(client)
    assert (msg.data, msg.extra) == (UNAUTHORIZED, "please sign in")


@pytest.mark.asyncio
async def test_signed_out_people_can_do_nothing(client):
    _, msg = await start(client)
    assert (msg.data, msg.extra) == (UNAUTHORIZED, "please sign in")
    assert (await client.get("/api/history")).status == 401
    assert (await client.post("/api/feedback", json={})).status == 401


@pytest.mark.asyncio
async def test_signing_in_wakes_the_selected_direction_once(client, engine):
    await signup(client, direction="vi-en")
    await client.post("/api/logout")
    await client.post(
        "/api/login",
        json={"username": "tester01", "password": "password1", "direction": "vi-en"},
    )
    await client.post(  # a direction without an engine: nothing to wake
        "/api/login",
        json={"username": "tester01", "password": "password1", "direction": "en-vi"},
    )
    await asyncio.sleep(0.2)
    assert engine.health_checks == 1  # the second wake-up came too soon
    assert engine.headers["Modal-Key"] == "k"


# Sessions -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_is_relayed_and_recorded_for_the_user(client, engine, tmp_path):
    await signup(client)
    session_id, messages = await converse(client)

    assert engine.headers["Modal-Key"] == "k"
    assert engine.audio == [b"\xf8audio-0", b"\xf8audio-1"]
    finals = [m for m in messages if m["type"] == "final"]
    assert [m["id"] for m in finals] == ["1", "2"]  # added by the front door
    assert all("id" not in m for m in messages if m["type"] == "partial")

    (folder,) = (tmp_path / "sessions").glob("*/*")
    meta = json.loads((folder / "session.json").read_text())
    assert meta["id"] == session_id and meta["user"] == "tester01"
    assert meta["models"] == {"asr": "asr-model", "mt": "mt-model"}
    assert meta["ended"]
    assert len((folder / "results.jsonl").read_text().splitlines()) == 4
    assert (folder / "audio.opus").stat().st_size > 0


@pytest.mark.asyncio
async def test_unconfigured_direction_is_unavailable(open_access):
    _, msg = await start(open_access, "en-vi")
    assert (msg.type, msg.data) == (WSMsgType.CLOSE, UNAVAILABLE)
    assert msg.extra == "English → Vietnamese is not available right now"
    assert (await open_access.get("/ws/fr-vi")).status == 404


@pytest.mark.asyncio
async def test_unreachable_engine_is_unavailable(tmp_path):
    cfg = FrontConfig(
        engines={"vi-en": "ws://127.0.0.1:9/ws/vi-en"},
        data_dir=tmp_path,
        accounts=False,
    )
    async with TestClient(TestServer(create_app(cfg))) as c:
        _, msg = await start(c)
    assert (msg.data, msg.extra) == (
        UNAVAILABLE,
        "Vietnamese → English is not available right now",
    )
    assert not list((tmp_path / "sessions").glob("*/*"))  # nothing recorded


@pytest.mark.asyncio
async def test_busy_engine_reason_reaches_the_page(open_access, engine):
    engine.mode = "busy"
    _, msg = await start(open_access)
    assert (msg.data, msg.extra) == (1013, "vi-en is in use")


@pytest.mark.asyncio
async def test_engine_hanging_up_ends_the_session(open_access, engine):
    engine.mode = "hang-up"
    ws, _ = await start(open_access)
    await ws.send_bytes(b"\xf8x")
    msg = await ws.receive(timeout=5)
    assert (msg.type, msg.data, msg.extra) == (WSMsgType.CLOSE, 1011, "engine error")


@pytest.mark.asyncio
async def test_sessions_have_a_time_limit(short):
    ws, _ = await start(short)
    msg = await ws.receive(timeout=5)
    assert (msg.type, msg.extra) == (WSMsgType.CLOSE, "session time limit reached")


@pytest.mark.asyncio
async def test_without_recording_there_is_no_session_or_history(unrecorded):
    assert (await (await unrecorded.get("/api/me")).json())["user"] == "local"
    ws, ready = await start(unrecorded)
    assert json.loads(ready.data) == {"type": "ready", "session": None}
    await ws.close()
    response = await unrecorded.post("/api/feedback", json={"session": "x"})
    assert response.status == 204
    assert await (await unrecorded.get("/api/history")).json() == {"sessions": []}


# History and feedback -------------------------------------------------------


@pytest.mark.asyncio
async def test_history_and_feedback_are_per_user(client, engine):
    await signup(client, username="tester01")
    session_id, _ = await converse(client)

    feedback = {
        "session": session_id,
        "item": "2",
        "source": "heard 2",
        "translation": "nghe 2",
        "transcription_rating": 5,
        "translation_rating": 9,  # out of range → 0
        "correction": "đã nghe 2",
    }
    assert (await client.post("/api/feedback", json=feedback)).status == 204

    history = (await (await client.get("/api/history")).json())["sessions"]
    assert [h["session"] for h in history] == [session_id]
    second = history[0]["sentences"][1]
    assert second["id"] == "2" and second["transcription"] == "heard 2"
    assert second["transcription_rating"] == 5 and second["translation_rating"] == 0
    assert second["correction"] == "đã nghe 2"

    # Another tester sees nothing of it and cannot rate it.
    await client.post("/api/logout")
    await signup(client, username="tester02")
    assert (await (await client.get("/api/history")).json()) == {"sessions": []}
    assert (await client.post("/api/feedback", json=feedback)).status == 404


@pytest.mark.asyncio
async def test_bad_feedback_is_rejected(open_access):
    assert (await open_access.post("/api/feedback", data="nope")).status == 400
    assert (await open_access.post("/api/feedback", json={"item": 1})).status == 400
    unknown = await open_access.post("/api/feedback", json={"session": "0" * 12})
    assert unknown.status == 404


# Engine status ----------------------------------------------------------------


async def engine_state(client, direction="vi-en"):
    response = await client.get(f"/api/engine?direction={direction}")
    return (await response.json())["state"]


@pytest.mark.asyncio
async def test_engine_status_needs_an_account(client):
    assert (await client.get("/api/engine?direction=vi-en")).status == 401


@pytest.mark.asyncio
async def test_engine_status_follows_the_engine(client, engine, monkeypatch):
    await signup(client)
    assert await engine_state(client) == "ready"
    engine.health = 503  # up, still loading models
    assert await engine_state(client) == "starting"
    monkeypatch.setattr(front_app, "ENGINE_CHECK_TIMEOUT_S", 0.2)
    engine.health = "hang"  # its machine is still being started
    assert await engine_state(client) == "starting"
    engine.health = 500
    assert await engine_state(client) == "unavailable"
    assert await engine_state(client, "en-vi") == "unavailable"  # not configured
    assert await engine_state(client, "fr-vi") == "unavailable"


@pytest.mark.asyncio
async def test_engine_status_of_an_unreachable_engine(tmp_path):
    cfg = FrontConfig(
        engines={"vi-en": "ws://127.0.0.1:9/ws/vi-en"},
        data_dir=tmp_path,
        accounts=False,
    )
    async with TestClient(TestServer(create_app(cfg))) as c:
        assert await engine_state(c) == "unavailable"


# Stop -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_finishes_the_sentence(open_access, engine):
    ws, _ = await start(open_access)
    await ws.send_str("hello")  # other text is not passed on
    await ws.send_str('{"type": "end"}')
    final = await ws.receive_json(timeout=5)
    assert (final["transcription"], final["id"]) == ("last", "1")
    assert engine.texts == ['{"type": "end"}']
    await ws.close()
