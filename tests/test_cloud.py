import importlib
import json
import subprocess
from unittest import mock

import pytest

from medcomms.cloud import APP_NAME, DEFAULTS, control, engine_url, gpu_request


def test_one_app_one_address():
    assert APP_NAME == "medcomms-engine"
    assert engine_url("vinuni", "en-vi") == (
        "wss://vinuni--medcomms-engine.modal.run/ws/en-vi"
    )


@pytest.fixture
def modal_cli():
    """Fake `modal` CLI calls and the settings Dict."""
    store = {}
    apps = []
    calls = []

    def run(cmd, **kwargs):
        args = cmd[3:]  # drop: python -m modal
        calls.append((args, kwargs.get("env", {})))
        if args[:2] == ["app", "list"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(apps))
        if args[:2] == ["app", "stop"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0)

    with (
        mock.patch("medcomms.cloud.control.subprocess.run", side_effect=run),
        mock.patch("medcomms.cloud.control._dict", return_value=store),
    ):
        yield calls, store, apps


def deploys(calls):
    return [
        json.loads(env["MEDCOMMS_ENGINE"]) for args, env in calls if args[0] == "deploy"
    ]


def stops(calls):
    return [args[2] for args, _ in calls if args[:2] == ["app", "stop"]]


def test_start_adds_a_direction_and_keeps_the_others(modal_cli, capsys):
    calls, store, _ = modal_cli
    control.start("vi-en", asr="org/vi-asr", mt="org/vi-mt")
    control.start("en-vi", asr="small.en", gpu="T4", idle_minutes=10)

    first, second = deploys(calls)
    assert first["directions"] == {"vi-en": {"asr": "org/vi-asr", "mt": "org/vi-mt"}}
    assert first["gpu"] == DEFAULTS["gpu"] == "L4,A10"
    assert second["directions"] == {
        "vi-en": {"asr": "org/vi-asr", "mt": "org/vi-mt"},
        "en-vi": {"asr": "small.en", "mt": ""},
    }
    assert (second["gpu"], second["idle_minutes"], second["cpu"]) == ("T4", 10, 2.0)
    assert store["engine"] == second
    assert "en-vi is on" in capsys.readouterr().out


def test_starting_stops_the_old_per_direction_apps(modal_cli):
    calls, _, apps = modal_cli
    apps.append({"description": "medcomms-en-vi", "state": "deployed"})
    apps.append({"description": "medcomms-vi-en", "state": "stopped"})
    control.start("en-vi")
    assert stops(calls) == ["medcomms-en-vi"]


def test_stop_redeploys_or_stops_the_app(modal_cli, capsys):
    calls, store, _ = modal_cli
    store["engine"] = {
        **DEFAULTS,
        "directions": {"vi-en": {"asr": "", "mt": ""}, "en-vi": {"asr": "", "mt": ""}},
    }
    control.stop("vi-en")
    assert deploys(calls)[-1]["directions"] == {"en-vi": {"asr": "", "mt": ""}}
    assert stops(calls) == []

    control.stop("all")
    assert stops(calls) == [APP_NAME]
    assert store["engine"]["directions"] == {}
    assert "vi-en and en-vi are off" in capsys.readouterr().out


def test_stop_reports_real_failures(capsys):
    failed = subprocess.CompletedProcess([], 1, stdout="", stderr="auth error\n")
    with (
        mock.patch("medcomms.cloud.control.subprocess.run", return_value=failed),
        mock.patch("medcomms.cloud.control._dict", return_value={}),
        pytest.raises(SystemExit),
    ):
        control.stop("vi-en")
    assert "auth error" in capsys.readouterr().err


def test_status(modal_cli, capsys):
    _, store, apps = modal_cli
    control.status()
    assert capsys.readouterr().out.splitlines()[0].startswith("Engine: off")

    apps.append({"description": APP_NAME, "state": "deployed"})
    store["engine"] = {**DEFAULTS, "directions": {"vi-en": {"asr": "x/asr", "mt": ""}}}
    control.status()
    out = capsys.readouterr().out.splitlines()
    assert out[0] == (
        "Engine: on (L4 GPU (or A10), 2 CPU, 16 GiB, sleeps after 5 idle minutes)"
    )
    assert out[1].split()[:4] == ["vi-en", "on", "ASR", "x/asr"]
    assert "MT default" in out[1]
    assert out[2].split() == ["en-vi", "off"]


def test_redeploy_only_when_on(modal_cli, capsys):
    calls, store, apps = modal_cli
    store["engine"] = {**DEFAULTS, "directions": {"en-vi": {"asr": "", "mt": "m"}}}
    control.redeploy()
    assert deploys(calls) == []
    assert "off; leaving it off" in capsys.readouterr().out

    apps.append({"description": APP_NAME, "state": "deployed"})
    apps.append({"description": "medcomms-en-vi", "state": "deployed"})
    control.redeploy()
    assert deploys(calls)[0]["directions"] == {"en-vi": {"asr": "", "mt": "m"}}
    assert stops(calls) == ["medcomms-en-vi"]  # the old per-direction app


def test_modal_app_runs_every_direction_with_its_models(monkeypatch):
    settings = {
        "directions": {
            "vi-en": {"asr": "org/vi-asr", "mt": ""},
            "en-vi": {"asr": "", "mt": "org/en-mt"},
        },
        "idle_minutes": 5,
        "memory_gb": 16,
    }
    monkeypatch.setenv("MEDCOMMS_ENGINE", json.dumps(settings))
    import medcomms.cloud.modal_app as modal_app

    modal_app = importlib.reload(modal_app)
    assert modal_app.app.name == APP_NAME
    assert modal_app.SETTINGS["gpu"] == "L4,A10"  # defaults fill the gaps
    cmd = modal_app.engine_command()
    assert cmd[1:4] == ["-m", "medcomms", "engine"]
    assert cmd[cmd.index("--device") + 1] == "cuda"
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "--direction"] == [
        "vi-en",
        "en-vi",
    ]
    assert "vi-en=org/vi-asr" in cmd and "en-vi=org/en-mt" in cmd
    assert cmd.count("--asr") == 1 and cmd.count("--mt") == 1


def test_gpu_fallbacks():
    assert gpu_request("L4") == "L4"
    assert gpu_request("L4, A10 ,") == ["L4", "A10"]
