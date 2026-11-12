"""Speech recognition models that ship their own code.

Some Hugging Face ASR repositories are not loadable with ``transformers``
alone: they contain an ``asr/`` Python package and a ``.pt`` checkpoint
(for example a CTC head on a custom encoder). This module
downloads such a repository, imports its package under a private name (so it
cannot clash with anything else called ``asr``), and wraps its documented
usage in a ``transcribe(audio) -> str`` function.
"""

import importlib.util
import logging
import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np
import torch
from huggingface_hub import file_exists, snapshot_download
from transformers import AutoTokenizer

log = logging.getLogger(__name__)

_PACKAGE = "medcomms_custom_asr"


def is_custom_asr(model: str) -> bool:
    """True for a local folder or Hugging Face repo with an ``asr/model.py``."""
    local = Path(model)
    if local.is_dir():
        return (local / "asr" / "model.py").exists()
    try:
        return file_exists(model, "asr/model.py")
    except Exception:
        return False


def _import_package(root: Path):
    if _PACKAGE in sys.modules:
        return sys.modules[_PACKAGE]
    spec = importlib.util.spec_from_file_location(
        _PACKAGE,
        root / "asr" / "__init__.py",
        submodule_search_locations=[str(root / "asr")],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[_PACKAGE] = package
    spec.loader.exec_module(package)
    return package


def load(model: str, device: str) -> Callable[[np.ndarray], str]:
    root = Path(model)
    if not root.is_dir():
        # Only the code and the weights; skip demos and language models.
        root = Path(snapshot_download(model, allow_patterns=["asr/*", "*.pt"]))
    (checkpoint,) = sorted(root.glob("*.pt"))[:1] or [None]
    if checkpoint is None:
        raise FileNotFoundError(f"no .pt checkpoint in {model}")

    _import_package(root)
    ctc = importlib.import_module(f"{_PACKAGE}.model")
    text = importlib.import_module(f"{_PACKAGE}.text")

    ck = torch.load(checkpoint, map_location="cpu")
    cfg = ctc.ModelConfig.from_dict(ck["model_config"])
    units = text.Units(ck["gemma_ids"])
    tokenizer = AutoTokenizer.from_pretrained(cfg.encoder_id)
    net = ctc.CTCModel(cfg, units.gemma_ids)
    net.load_state_dict(ck["model"], strict=False)
    net = net.to(device).eval()
    log.info("Loaded %s (custom CTC model, encoder %s)", model, cfg.encoder_id)

    def transcribe(audio: np.ndarray) -> str:
        x = torch.from_numpy(audio.astype(np.float32))[None].to(device)
        lengths = torch.tensor([x.shape[1]], device=device)
        # The encoder runs in bfloat16, as the model card prescribes.
        with torch.no_grad(), torch.autocast(device, dtype=torch.bfloat16):
            logits, lengths = net(x, lengths, streaming=False)
        return units.decode(tokenizer, ctc.greedy_decode(logits, lengths)[0])

    return transcribe
