"""``medcomms cloud start|stop|status|redeploy``: manage the engine on Modal.

All directions run in one Modal app on one GPU. Starting or stopping a
direction updates the app's list of directions and redeploys it (which
restarts the engine, ending any conversation in progress); stopping the last
direction stops the app. The settings are kept in a Modal Dict
(``medcomms-settings``) so ``redeploy`` (run by the deploy workflow after a
code change) restarts the engine exactly as it was.
"""

import json
import os
import subprocess
import sys

from . import APP_NAME, DEFAULTS, DIRECTIONS

SETTINGS_DICT = "medcomms-settings"
SETTINGS_KEY = "engine"
# Apps from before the directions shared one GPU.
LEGACY_APPS = frozenset(f"medcomms-{d}" for d in DIRECTIONS)


def _dict():
    import modal

    return modal.Dict.from_name(SETTINGS_DICT, create_if_missing=True)


def _load() -> dict:
    stored = _dict().get(SETTINGS_KEY) or {}
    return {**DEFAULTS, **stored, "directions": dict(stored.get("directions", {}))}


def _save(settings: dict):
    _dict()[SETTINGS_KEY] = settings


def _modal(*args: str, env: dict | None = None, capture=False, check=True):
    return subprocess.run(
        [sys.executable, "-m", "modal", *args],
        env={**os.environ, **(env or {})},
        check=check,
        text=True,
        capture_output=capture,
    )


def _deploy(settings: dict):
    _modal(
        "deploy",
        "-m",
        "medcomms.cloud.modal_app",
        env={"MEDCOMMS_ENGINE": json.dumps(settings)},
    )


def _stop_app(name: str):
    result = _modal("app", "stop", name, "--yes", capture=True, check=False)
    if result.returncode != 0 and "not found" not in (result.stderr or "").lower():
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)


def _deployed() -> set[str]:
    out = _modal("app", "list", "--json", capture=True).stdout
    names = set()
    for app in json.loads(out or "[]"):
        state = str(app.get("state") or app.get("State") or "").lower()
        name = app.get("description") or app.get("Description") or app.get("name")
        if state == "deployed":
            names.add(name)
    return names


def _describe(settings: dict) -> str:
    first, *fallbacks = settings["gpu"].split(",")
    gpu = f"{first} GPU" + (f" (or {', '.join(fallbacks)})" if fallbacks else "")
    return (
        f"{gpu}, {settings['cpu']:g} CPU, {settings['memory_gb']} GiB, "
        f"sleeps after {settings['idle_minutes']:g} idle minutes"
    )


def start(
    direction: str,
    asr: str = "",
    mt: str = "",
    model_size=None,
    gpu=None,
    cpu=None,
    memory_gb=None,
    idle_minutes=None,
):
    """Turn a direction on (or change its models or the machine)."""
    settings = _load()
    settings["directions"][direction] = {"asr": asr, "mt": mt}
    for key, value in (
        ("model_size", model_size),
        ("gpu", gpu),
        ("cpu", cpu),
        ("memory_gb", memory_gb),
        ("idle_minutes", idle_minutes),
    ):
        if value is not None:
            settings[key] = value
    on = ", ".join(settings["directions"])
    print(f"Deploying the engine with {on} ({_describe(settings)})…")
    _deploy(settings)
    _save(settings)
    for legacy in LEGACY_APPS & _deployed():
        _stop_app(legacy)
    print(
        f"{direction} is on. The engine wakes up when someone signs in or starts "
        "a conversation (allow a few minutes) and sleeps when idle."
    )


def stop(direction: str):
    """Turn a direction off ("all" for every direction)."""
    settings = _load()
    names = list(DIRECTIONS) if direction == "all" else [direction]
    for name in names:
        settings["directions"].pop(name, None)
    _save(settings)
    if settings["directions"]:
        print(f"Redeploying the engine with {', '.join(settings['directions'])}…")
        _deploy(settings)
    else:
        _stop_app(APP_NAME)
    for legacy in LEGACY_APPS & _deployed():
        _stop_app(legacy)
    print(f"{' and '.join(names)} {'is' if len(names) == 1 else 'are'} off.")


def status():
    settings = _load()
    on = APP_NAME in _deployed() and settings["directions"]
    print(f"Engine: {'on' if on else 'off'} ({_describe(settings)})")
    for direction in DIRECTIONS:
        models = settings["directions"].get(direction)
        if not on or models is None:
            print(f"  {direction}   off")
            continue
        size = settings["model_size"]
        asr = models.get("asr") or f"default ({size})"
        mt = models.get("mt") or "default"
        print(f"  {direction}   on    ASR {asr}   MT {mt}")


def redeploy():
    """Update the engine to this code, if it is on, with its last settings."""
    settings = _load()
    deployed = _deployed()
    if APP_NAME in deployed and settings["directions"]:
        print(f"Updating the engine ({', '.join(settings['directions'])})…")
        _deploy(settings)
    else:
        print("The engine is off; leaving it off.")
    for legacy in LEGACY_APPS & deployed:
        print(f"Stopping {legacy} (replaced by {APP_NAME})")
        _stop_app(legacy)
