"""The translation engine as a Modal app.

Deployed by ``medcomms cloud start|stop`` (not by hand): they put the
settings in ``MEDCOMMS_ENGINE`` (JSON) and run
``modal deploy -m medcomms.cloud.modal_app``. The settings are baked into the
image, so the module reads the same values when Modal imports it again
inside the container.

- One container runs every switched-on direction, on one GPU.
- Reachable only with a Modal proxy auth token (the front door has one), at
  https://<workspace>--medcomms-engine.modal.run.
- At most one container; it shuts down after ``idle_minutes`` without a
  conversation.
- Model downloads are kept in the ``medcomms-cache`` volume.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import modal

from medcomms.cloud import APP_NAME, DEFAULTS, gpu_request

SETTINGS = {**DEFAULTS, **json.loads(os.environ.get("MEDCOMMS_ENGINE") or "{}")}
MAX_SESSION_HOURS = 4
PORT = 8000

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libopus0")
    .pip_install_from_pyproject(str(_PYPROJECT), optional_dependencies=["engine"])
    .env(
        {
            "MEDCOMMS_ENGINE": json.dumps(SETTINGS),
            "HF_HOME": "/cache/huggingface",
            "TORCH_HOME": "/cache/torch",
        }
    )
    .add_local_python_source("medcomms")
)

app = modal.App(APP_NAME, image=image)
cache = modal.Volume.from_name("medcomms-cache", create_if_missing=True)


def engine_command(settings: dict = SETTINGS) -> list[str]:
    cmd = [sys.executable, "-m", "medcomms", "engine", "--device", "cuda"]
    cmd += ["--model-size", settings["model_size"], "--port", str(PORT)]
    for direction, models in settings["directions"].items():
        cmd += ["--direction", direction]
        if models.get("asr"):
            cmd += ["--asr", f"{direction}={models['asr']}"]
        if models.get("mt"):
            cmd += ["--mt", f"{direction}={models['mt']}"]
    return cmd


@app.function(
    gpu=gpu_request(SETTINGS["gpu"]),
    cpu=float(SETTINGS["cpu"]),
    memory=int(SETTINGS["memory_gb"] * 1024),
    volumes={"/cache": cache},
    max_containers=1,
    scaledown_window=int(SETTINGS["idle_minutes"] * 60),
    timeout=MAX_SESSION_HOURS * 3600,
)
@modal.concurrent(max_inputs=8)
@modal.web_server(PORT, startup_timeout=300, label=APP_NAME, requires_proxy_auth=True)
def engine():
    subprocess.Popen(engine_command())
