"""Running the translation engine on Modal (https://modal.com).

One Modal app, ``medcomms-engine``, runs every direction that is switched on,
on one shared GPU. Its web address is fixed, so the front door can find it
with only the workspace name. Importing this module does not import Modal.
"""

DIRECTIONS = ("vi-en", "en-vi")
APP_NAME = "medcomms-engine"

# Engine settings, as kept by ``medcomms cloud`` and baked into the Modal app.
DEFAULTS = {
    "directions": {},  # {"vi-en": {"asr": "", "mt": ""}, ...}; "" = default
    "model_size": "large",
    # In order of preference: Modal sometimes has no L4 free. (Not T4: no
    # bfloat16, which some models need.)
    "gpu": "L4,A10",
    "cpu": 2.0,
    "memory_gb": 16,
    "idle_minutes": 5,
}


def gpu_request(gpu: str) -> str | list[str]:
    """``"L4,A10"`` → ``["L4", "A10"]`` (fallbacks); a single type as is."""
    types = [g.strip() for g in gpu.split(",") if g.strip()]
    return types if len(types) > 1 else types[0]


def engine_url(workspace: str, direction: str) -> str:
    """The WebSocket address of a direction in ``workspace``."""
    return f"wss://{workspace}--{APP_NAME}.modal.run/ws/{direction}"
