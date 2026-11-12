from pathlib import Path
from unittest import mock

import pytest

import medcomms.cloud.control  # noqa: F401  (so it can be patched)
from medcomms import __version__
from medcomms.cli import build_parser, main


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"medcomms {__version__}"


def test_a_command_is_required():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


# engine ---------------------------------------------------------------------


def test_engine_defaults():
    with (
        mock.patch("medcomms.log.setup_logging"),
        mock.patch("medcomms.engine.server.Engine") as engine,
    ):
        engine.return_value.run.return_value = 0
        assert main(["engine"]) == 0
    cfg = engine.call_args.args[0]
    assert cfg.directions == ("vi-en", "en-vi")
    assert cfg.port == 8100
    assert cfg.asr_models == {} and cfg.mt_models == {}


def test_engine_with_overrides():
    argv = [
        "engine",
        "--device", "cpu",
        "--port", "9000",
        "--model-size", "large",
        "--direction", "en-vi",
        "--asr", "en-vi=medium",
        "--mt", "en-vi=Helsinki-NLP/opus-mt-en-vi",
        "--silence-threshold", "3",
        "--vad-aggressiveness", "5",
        "--max-buffer-duration", "9",
        "--log-level", "DEBUG",
    ]  # fmt: skip
    with (
        mock.patch("medcomms.log.setup_logging") as setup_logging,
        mock.patch("medcomms.engine.server.Engine") as engine,
    ):
        engine.return_value.run.return_value = 1
        assert main(argv) == 1
    setup_logging.assert_called_once_with("DEBUG")
    cfg = engine.call_args.args[0]
    assert (cfg.port, cfg.model_size, cfg.directions) == (9000, "large", ("en-vi",))
    assert cfg.asr_models == {"en-vi": "medium"}
    assert cfg.mt_models == {"en-vi": "Helsinki-NLP/opus-mt-en-vi"}
    assert (cfg.silence_threshold, cfg.vad_aggressiveness) == (3.0, 5)
    assert cfg.max_buffer_duration == 9


@pytest.mark.parametrize("value", ["vi-en", "vi-fr=x", "vi-en=", "=tiny"])
def test_bad_model_override(value, capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["engine", "--asr", value])
    assert "DIRECTION=MODEL" in capsys.readouterr().err


# front ----------------------------------------------------------------------


def run_front(argv):
    with (
        mock.patch("medcomms.log.setup_logging"),
        mock.patch("medcomms.front.app.run") as run,
    ):
        assert main(["front", *argv]) == 0
    return run.call_args.args[0]


def test_front_defaults(monkeypatch):
    for var in ("PORT", "MEDCOMMS_SIGNUP_CODE", "MEDCOMMS_DATA_DIR", "MEDCOMMS_RECORD"):
        monkeypatch.delenv(var, raising=False)
    cfg = run_front([])
    assert (cfg.host, cfg.port) == ("127.0.0.1", 8000)
    assert cfg.engines == {} and cfg.signup_code is None
    assert cfg.accounts and cfg.record
    assert cfg.data_dir == Path("data")


def test_front_from_environment(monkeypatch):
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("MEDCOMMS_SIGNUP_CODE", "1234")
    monkeypatch.setenv("MEDCOMMS_DATA_DIR", "/var/lib/medcomms")
    monkeypatch.setenv("MEDCOMMS_MODAL_WORKSPACE", "vinuni")
    monkeypatch.setenv("MODAL_KEY", "wk-1")
    monkeypatch.setenv("MODAL_SECRET", "ws-2")
    cfg = run_front(["--engine", "en-vi=ws://localhost:8100/ws/en-vi"])
    assert cfg.port == 9000 and cfg.signup_code == "1234"
    assert cfg.data_dir == Path("/var/lib/medcomms")
    assert cfg.engines == {
        "vi-en": "wss://vinuni--medcomms-engine.modal.run/ws/vi-en",
        "en-vi": "ws://localhost:8100/ws/en-vi",
    }
    assert cfg.engine_headers == {"Modal-Key": "wk-1", "Modal-Secret": "ws-2"}


def test_front_modal_needs_a_token(monkeypatch):
    monkeypatch.delenv("MODAL_KEY", raising=False)
    with pytest.raises(SystemExit, match="MODAL_KEY"):
        run_front(["--modal-workspace", "vinuni"])


def test_front_recording_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("MEDCOMMS_RECORD", "0")
    assert run_front([]).record is False


# dev ------------------------------------------------------------------------


def test_dev_runs_an_engine_and_the_front_door():
    with (
        mock.patch("medcomms.log.setup_logging"),
        mock.patch("medcomms.cli.subprocess.Popen") as popen,
        mock.patch("medcomms.front.app.run") as run,
    ):
        assert main(["dev", "--light", "--mt", "vi-en=custom/model"]) == 0
    cmd = popen.call_args.args[0]
    assert cmd[1:4] == ["-m", "medcomms", "engine"]
    assert "en-vi=base" in cmd and "vi-en=custom/model" in cmd
    cfg = run.call_args.args[0]
    assert cfg.engines["vi-en"] == "ws://127.0.0.1:8100/ws/vi-en"
    popen.return_value.terminate.assert_called_once()


# cloud ----------------------------------------------------------------------


def test_cloud_start_passes_only_what_was_given():
    with mock.patch("medcomms.cloud.control") as control:
        assert main(["cloud", "start", "vi-en"]) == 0
        assert main(
            ["cloud", "start", "en-vi", "--asr", "small.en", "--mt", "org/mt",
             "--gpu", "T4", "--cpu", "4", "--memory", "8", "--idle-minutes", "10",
             "--model-size", "small"]
        ) == 0  # fmt: skip
    first, second = control.start.call_args_list
    assert first.args == ("vi-en",)
    assert first.kwargs == {
        "asr": "", "mt": "", "model_size": None, "gpu": None, "cpu": None,
        "memory_gb": None, "idle_minutes": None,
    }  # fmt: skip
    assert second.kwargs == {
        "asr": "small.en", "mt": "org/mt", "model_size": "small", "gpu": "T4",
        "cpu": 4.0, "memory_gb": 8, "idle_minutes": 10.0,
    }  # fmt: skip


@pytest.mark.parametrize(
    ("argv", "name", "args"),
    [
        (["stop", "all"], "stop", ("all",)),
        (["stop", "vi-en"], "stop", ("vi-en",)),
        (["status"], "status", ()),
        (["redeploy"], "redeploy", ()),
    ],
)
def test_cloud_other_commands(argv, name, args):
    with mock.patch("medcomms.cloud.control") as control:
        assert main(["cloud", *argv]) == 0
    getattr(control, name).assert_called_once_with(*args)


def test_dev_with_accounts():
    with (
        mock.patch("medcomms.log.setup_logging"),
        mock.patch("medcomms.cli.subprocess.Popen"),
        mock.patch("medcomms.front.app.run") as run,
    ):
        main(["dev"])
        main(["dev", "--accounts"])
    off, on = (c.args[0] for c in run.call_args_list)
    assert not off.accounts and off.signup_code is None
    assert on.accounts and on.signup_code == "dev"


# users and sessions ---------------------------------------------------------


def run_cli(argv, tmp_path, capsys, stdin=None):
    with mock.patch("builtins.input", return_value=stdin or ""):
        code = main([*argv, "--data-dir", str(tmp_path)])
    out = capsys.readouterr()
    return code, out.out + out.err


def test_users_commands(tmp_path, capsys):
    assert run_cli(["users", "list"], tmp_path, capsys) == (0, "No accounts yet.\n")
    code, out = run_cli(["users", "add", "Tester01"], tmp_path, capsys)
    assert code == 0 and out.startswith("Created tester01. Password: ")
    password = out.split("Password: ")[1].strip()

    from medcomms.front.users import UserStore

    assert UserStore(tmp_path).authenticate("tester01", password) == "tester01"
    assert run_cli(["users", "add", "tester01"], tmp_path, capsys)[0] == 1
    assert run_cli(["users", "add", "x"], tmp_path, capsys)[0] == 1

    code, out = run_cli(["users", "reset", "tester01"], tmp_path, capsys)
    assert code == 0 and "New password for tester01" in out
    assert run_cli(["users", "disable", "tester01"], tmp_path, capsys)[0] == 0
    code, out = run_cli(["users", "list"], tmp_path, capsys)
    assert "tester01" in out and "disabled" in out
    assert run_cli(["users", "enable", "tester01"], tmp_path, capsys)[0] == 0
    assert run_cli(["users", "remove", "tester01"], tmp_path, capsys)[0] == 0
    assert run_cli(["users", "reset", "nobody"], tmp_path, capsys)[0] == 1


def test_sessions_commands(tmp_path, capsys):
    from medcomms.front.sessions import SessionStore

    assert run_cli(["sessions", "list"], tmp_path, capsys) == (0, "No sessions.\n")
    store = SessionStore(tmp_path)
    session = store.open("vi-en", user="tester01")
    session.add_message({"type": "info", "asr": "a", "mt": "m"})
    session.add_message({"type": "final", "transcription": "x", "translation": "y"})
    session.close()

    code, out = run_cli(["sessions", "list", "--user", "tester01"], tmp_path, capsys)
    assert code == 0 and session.id in out and "1 session(s)" in out
    assert run_cli(["sessions", "list", "--user", "other"], tmp_path, capsys)[1] == (
        "No sessions.\n"
    )

    zip_path = tmp_path / "out.zip"
    code, out = run_cli(["sessions", "export", str(zip_path)], tmp_path, capsys)
    assert code == 0 and zip_path.exists()
    assert (
        run_cli(
            ["sessions", "export", str(zip_path), "--since", "2999-01-01"],
            tmp_path,
            capsys,
        )[0]
        == 1
    )

    assert run_cli(["sessions", "delete", "0" * 12], tmp_path, capsys)[0] == 1
    code, out = run_cli(["sessions", "delete", session.id], tmp_path, capsys, "n")
    assert code == 1 and "Nothing deleted" in out
    code, out = run_cli(["sessions", "delete", session.id], tmp_path, capsys, "y")
    assert code == 0 and not session.folder.exists()
