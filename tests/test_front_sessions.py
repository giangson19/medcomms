import csv
import io
import json
import zipfile
from collections import namedtuple
from unittest import mock

import pytest

from medcomms.front.sessions import SessionStore, export, read_session, summary

Usage = namedtuple("Usage", "total used free")


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def record(store, user="tester01", direction="vi-en", sentences=("Chào", "Đau")):
    """A finished session with partial and final results."""
    session = store.open(direction, user=user)
    session.add_message({"type": "info", "asr": "asr-model", "mt": "mt-model"})
    session.add_audio(bytes([0b11111000]) + b"x" * 50)
    sent = []
    for text in sentences:
        session.add_message({"type": "partial", "transcription": text[:1]})
        sent.append(
            session.add_message(
                {"type": "final", "timestamp": "t", "transcription": text,
                 "translation": text.upper()}
            )
        )  # fmt: skip
    session.close()
    return session, sent


def test_a_session_records_everything_and_numbers_its_sentences(tmp_path):
    store = SessionStore(tmp_path)
    session, sent = record(store)

    assert [m["id"] for m in sent] == ["1", "2"]  # what the page receives
    folder = session.folder
    assert folder.parent.parent == tmp_path / "sessions"
    assert folder.name.endswith(f"-vi-en-{session.id}")
    meta = json.loads((folder / "session.json").read_text())
    assert meta["user"] == "tester01" and meta["direction"] == "vi-en"
    assert meta["models"] == {"asr": "asr-model", "mt": "mt-model"}
    assert meta["audio"] == "audio.opus" and meta["ended"]
    assert (folder / "audio.opus").read_bytes().startswith(b"OggS")
    results = read_jsonl(folder / "results.jsonl")
    assert [r["type"] for r in results] == ["partial", "final", "partial", "final"]
    assert session.add_message({"type": "ready"}) == {"type": "ready"}  # ignored


def test_audio_is_skipped_when_the_disk_is_nearly_full(tmp_path):
    store = SessionStore(tmp_path, min_free_gb=2)
    with mock.patch("shutil.disk_usage", return_value=Usage(10, 9, 1024**3)):
        session = store.open("en-vi")
    session.add_audio(b"\xf8abc")
    session.close()
    assert not (session.folder / "audio.opus").exists()
    assert json.loads((session.folder / "session.json").read_text())["audio"] is None


def test_feedback_is_kept_with_the_sentence_latest_wins(tmp_path):
    store = SessionStore(tmp_path)
    session, _ = record(store)
    assert store.add_feedback(session.id, {"item": "2", "translation_rating": 4})
    assert store.add_feedback(
        session.id, {"item": "2", "translation_rating": 5, "correction": "Pain"}
    )
    sentences = read_session(session.folder)["sentences"]
    assert sentences[0]["translation_rating"] == 0
    assert sentences[1]["translation_rating"] == 5
    assert sentences[1]["correction"] == "Pain"


def test_feedback_only_on_your_own_sessions(tmp_path):
    store = SessionStore(tmp_path)
    session, _ = record(store, user="tester01")
    assert not store.add_feedback(session.id, {"item": "1"}, user="tester02")
    assert store.add_feedback(session.id, {"item": "1"}, user="tester01")


def test_feedback_finds_sessions_from_before_a_restart(tmp_path):
    session, _ = record(SessionStore(tmp_path))
    assert SessionStore(tmp_path).add_feedback(session.id, {"item": "1"})


@pytest.mark.parametrize(
    "session_id", ["000000000000", "../../etc", "ABCDEF123456", ""]
)
def test_unknown_or_malformed_sessions_are_refused(tmp_path, session_id):
    assert not SessionStore(tmp_path).add_feedback(session_id, {})


def test_history_is_per_user_newest_first(tmp_path):
    store = SessionStore(tmp_path)
    first, _ = record(store, user="tester01", sentences=("một",))
    record(store, user="tester02", sentences=("hai",))
    empty, _ = record(store, user="tester01", sentences=())
    last, _ = record(store, user="tester01", direction="en-vi", sentences=("three",))
    # Folder names sort by time; make the order unambiguous for the test.
    for i, session in enumerate((first, empty, last)):
        session.folder.rename(session.folder.with_name(f"0{i}{session.folder.name}"))
    store = SessionStore(tmp_path)

    history = store.history("tester01")
    assert [h["session"] for h in history] == [last.id, first.id]  # empty skipped
    assert history[0]["direction"] == "en-vi"
    assert history[0]["sentences"][0]["transcription"] == "three"
    assert store.history("nobody") == []


def test_history_stops_at_the_limit(tmp_path):
    store = SessionStore(tmp_path)
    for _ in range(3):
        record(store, sentences=("a", "b"))
    assert len(store.history("tester01", limit=3)) == 2


def test_listing_filters_summaries_and_delete(tmp_path):
    store = SessionStore(tmp_path)
    session, _ = record(store, user="tester01")
    store.add_feedback(session.id, {"item": "1", "translation_rating": 4})
    store.add_feedback(session.id, {"item": "2", "translation_rating": 2,
                                    "correction": "x"})  # fmt: skip
    record(store, user="tester02")

    day = session.folder.parent.name
    assert len(store.sessions()) == 2
    assert len(store.sessions(user="tester02")) == 1
    assert store.sessions(since="2999-01-01") == []
    assert len(store.sessions(since=day, until=day)) == 2

    row = summary(read_session(session.folder))
    assert row["user"] == "tester01" and row["sentences"] == 2
    assert row["rated"] == 2 and row["avg_translation_rating"] == 3.0
    assert row["corrections"] == 1
    assert row["asr"] == "asr-model" and row["duration_s"] is not None

    assert store.delete(session.id)
    assert not session.folder.exists()
    assert not store.delete(session.id)
    assert len(store.sessions()) == 1


def test_export_writes_folders_and_csv_tables(tmp_path):
    store = SessionStore(tmp_path)
    session, _ = record(store)
    store.add_feedback(session.id, {"item": "1", "translation_rating": 5})
    out = export(store.sessions(), tmp_path / "out.zip")

    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        sessions_csv = list(
            csv.DictReader(io.StringIO(z.read("sessions.csv").decode()))
        )
        sentences_csv = list(
            csv.DictReader(io.StringIO(z.read("sentences.csv").decode()))
        )
    assert any(n.endswith("/audio.opus") for n in names)
    assert sessions_csv[0]["session"] == session.id
    assert [r["transcription"] for r in sentences_csv] == ["Chào", "Đau"]
    assert sentences_csv[0]["translation_rating"] == "5"
