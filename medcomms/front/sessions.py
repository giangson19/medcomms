"""Recording of translation sessions.

Each session (from Start to Stop in the speaker page) gets its own folder::

    <data_dir>/sessions/2026-11-12/093015-vi-en-3f9a1c2b7d4e/
        session.json    user, direction, start/end time, models used
        audio.opus      what was said (as received from the browser)
        results.jsonl   every partial and final result, in order
        feedback.jsonl  ratings and corrections from the page (latest wins)

Each final result (a finished sentence) gets an ``id`` within its session,
which feedback refers to. No IP addresses or browser details are stored.
"""

import csv
import io
import json
import logging
import re
import secrets
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from .ogg import OggOpusWriter

log = logging.getLogger(__name__)

SESSION_ID = re.compile(r"^[0-9a-f]{12}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _append_jsonl(path: Path, record: dict):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a line cut short by a crash
    return records


class Session:
    def __init__(
        self, folder: Path, session_id: str, direction: str, audio: bool, user
    ):
        self.id = session_id
        self.folder = folder
        self._sentences = 0
        self._meta = {
            "id": session_id,
            "user": user,
            "direction": direction,
            "started": _now(),
            "ended": None,
            "models": None,
            "audio": "audio.opus" if audio else None,
            "app_version": __version__,
        }
        self._audio = OggOpusWriter(folder / "audio.opus") if audio else None
        self._save_meta()

    def add_audio(self, packet: bytes):
        if self._audio:
            self._audio.write(packet)

    def add_message(self, message: dict) -> dict:
        """Record a message from the engine; returns it as sent to the page
        (final results gain an ``id``)."""
        if message.get("type") == "info":
            self._meta["models"] = {"asr": message.get("asr"), "mt": message.get("mt")}
            self._save_meta()
        elif "translation" in message or "transcription" in message:
            if message.get("type") == "final":
                self._sentences += 1
                message = {**message, "id": str(self._sentences)}
            _append_jsonl(self.folder / "results.jsonl", message)
        return message

    def close(self):
        if self._audio:
            self._audio.close()
        self._meta["ended"] = _now()
        self._save_meta()

    def _save_meta(self):
        path = self.folder / "session.json"
        path.write_text(json.dumps(self._meta, ensure_ascii=False, indent=2) + "\n")


def read_session(folder: Path) -> dict:
    """A session's metadata, finished sentences and latest feedback."""
    meta = json.loads((folder / "session.json").read_text())
    feedback = {}
    for record in _read_jsonl(folder / "feedback.jsonl"):
        feedback[str(record.get("item"))] = record  # latest wins
    sentences = []
    for result in _read_jsonl(folder / "results.jsonl"):
        if result.get("type") != "final":
            continue
        fb = feedback.get(result.get("id"), {})
        sentences.append(
            {
                "id": result.get("id"),
                "timestamp": result.get("timestamp"),
                "transcription": result.get("transcription", ""),
                "translation": result.get("translation", ""),
                "transcription_rating": fb.get("transcription_rating", 0),
                "translation_rating": fb.get("translation_rating", 0),
                "correction": fb.get("correction", ""),
            }
        )
    audio = folder / "audio.opus"
    return {
        **meta,
        "folder": folder,
        "sentences": sentences,
        "audio_bytes": audio.stat().st_size if audio.exists() else 0,
    }


class SessionStore:
    """Creates, finds and reads the recorded sessions under ``data_dir``.

    Audio is skipped (text is still kept) when free disk space falls below
    ``min_free_gb``.
    """

    def __init__(self, data_dir: Path, min_free_gb: float = 2.0):
        self.root = Path(data_dir) / "sessions"
        self.root.mkdir(parents=True, exist_ok=True)
        self._min_free_bytes = min_free_gb * 1024**3
        self._folders: dict[str, Path] = {}

    def open(self, direction: str, user: str | None = None) -> Session:
        session_id = secrets.token_hex(6)
        now = datetime.now()
        folder = (
            self.root
            / now.strftime("%Y-%m-%d")
            / f"{now:%H%M%S}-{direction}-{session_id}"
        )
        folder.mkdir(parents=True)
        audio = shutil.disk_usage(self.root).free >= self._min_free_bytes
        if not audio:
            log.warning(
                "Less than %.0f GB free: not recording audio",
                self._min_free_bytes / 1024**3,
            )
        self._folders[session_id] = folder
        return Session(folder, session_id, direction, audio, user)

    def add_feedback(self, session_id: str, feedback: dict, user=None) -> bool:
        """File feedback; with ``user``, only on that user's own sessions."""
        folder = self.find(session_id)
        if folder is None:
            return False
        if user is not None:
            meta = json.loads((folder / "session.json").read_text())
            if meta.get("user") != user:
                return False
        _append_jsonl(folder / "feedback.jsonl", {"time": _now(), **feedback})
        return True

    def find(self, session_id: str) -> Path | None:
        if not SESSION_ID.match(session_id):
            return None
        if session_id not in self._folders:
            # Sessions from before a restart.
            matches = list(self.root.glob(f"*/*-{session_id}"))
            if not matches:
                return None
            self._folders[session_id] = matches[0]
        return self._folders[session_id]

    def folders(self) -> list[Path]:
        """Every session folder, newest first."""
        return sorted(
            (p for p in self.root.glob("*/*") if (p / "session.json").exists()),
            key=lambda p: (p.parent.name, p.name),
            reverse=True,
        )

    def sessions(self, user=None, since=None, until=None) -> list[dict]:
        """Sessions (newest first), optionally for one user or a date range
        (``YYYY-MM-DD``, inclusive)."""
        found = []
        for folder in self.folders():
            day = folder.parent.name
            if (since and day < since) or (until and day > until):
                continue
            try:
                session = read_session(folder)
            except (OSError, json.JSONDecodeError):
                continue
            if user is None or session.get("user") == user:
                found.append(session)
        return found

    def history(self, user: str, limit: int = 300) -> list[dict]:
        """A user's sessions with their finished sentences, newest first,
        up to ``limit`` sentences in all."""
        result, count = [], 0
        for session in self.sessions(user=user):
            if not session["sentences"]:
                continue
            result.append(
                {
                    "session": session["id"],
                    "direction": session["direction"],
                    "started": session["started"],
                    "sentences": session["sentences"],
                }
            )
            count += len(session["sentences"])
            if count >= limit:
                break
        return result

    def delete(self, session_id: str) -> bool:
        folder = self.find(session_id)
        if folder is None:
            return False
        shutil.rmtree(folder)
        self._folders.pop(session_id, None)
        return True


def summary(session: dict) -> dict:
    """One row describing a session, for listings and exports."""
    sentences = session["sentences"]
    rated = [s["translation_rating"] for s in sentences if s["translation_rating"]]
    duration = None
    if session.get("started") and session.get("ended"):
        start = datetime.fromisoformat(session["started"])
        duration = round(
            (datetime.fromisoformat(session["ended"]) - start).total_seconds()
        )
    models = session.get("models") or {}
    return {
        "session": session["id"],
        "user": session.get("user") or "",
        "direction": session["direction"],
        "started": session["started"],
        "duration_s": duration,
        "asr": models.get("asr", ""),
        "mt": models.get("mt", ""),
        "sentences": len(sentences),
        "rated": len(rated),
        "avg_translation_rating": round(sum(rated) / len(rated), 2) if rated else "",
        "corrections": sum(1 for s in sentences if s["correction"]),
        "audio_kb": round(session["audio_bytes"] / 1024),
    }


def _csv(rows: list[dict]) -> str:
    out = io.StringIO()
    if rows:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return out.getvalue()


def export(sessions: list[dict], path: Path) -> Path:
    """Write sessions to a zip: their folders, plus ``sessions.csv`` (one row
    per session) and ``sentences.csv`` (one row per finished sentence, with
    its ratings and correction)."""
    sentence_rows = [
        {
            "session": s["id"],
            "user": s.get("user") or "",
            "direction": s["direction"],
            "sentence": sentence["id"],
            **{k: v for k, v in sentence.items() if k != "id"},
        }
        for s in sessions
        for sentence in s["sentences"]
    ]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("sessions.csv", _csv([summary(s) for s in sessions]))
        z.writestr("sentences.csv", _csv(sentence_rows))
        for s in sessions:
            folder = s["folder"]
            for file in sorted(folder.iterdir()):
                z.write(
                    file, f"sessions/{folder.parent.name}/{folder.name}/{file.name}"
                )
    return path
