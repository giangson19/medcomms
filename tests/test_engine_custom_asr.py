import sys
import textwrap
from unittest import mock

import numpy as np
import pytest
import torch

from medcomms.engine import custom_asr

FAKE_MODEL = """
    import torch


    class ModelConfig:
        def __init__(self, encoder_id):
            self.encoder_id = encoder_id

        @classmethod
        def from_dict(cls, d):
            return cls(**d)


    class CTCModel(torch.nn.Module):
        def __init__(self, cfg, gemma_ids):
            super().__init__()
            self.scale = torch.nn.Parameter(torch.zeros(1))
            self.gemma_ids = gemma_ids

        def forward(self, wav, wav_lens, streaming=False):
            assert not streaming
            frames = wav.shape[1] // 160
            return torch.zeros(1, frames, len(self.gemma_ids)), wav_lens // 160


    def greedy_decode(logits, lens, blank=0):
        return [[1, 2]]
"""

FAKE_TEXT = """
    class Units:
        def __init__(self, gemma_ids):
            self.gemma_ids = gemma_ids

        def decode(self, tokenizer, units):
            return " ".join(tokenizer.words[u] for u in units)
"""


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "asr").mkdir()
    (tmp_path / "asr" / "__init__.py").write_text("")
    (tmp_path / "asr" / "model.py").write_text(textwrap.dedent(FAKE_MODEL))
    (tmp_path / "asr" / "text.py").write_text(textwrap.dedent(FAKE_TEXT))
    torch.save(
        {
            "model_config": {"encoder_id": "org/encoder"},
            "gemma_ids": [0, 1, 2],
            "model": {"scale": torch.ones(1)},
        },
        tmp_path / "custom_asr.pt",
    )
    yield tmp_path
    for name in [m for m in sys.modules if m.startswith(custom_asr._PACKAGE)]:
        del sys.modules[name]


def test_detects_local_and_hub_models(repo, tmp_path):
    assert custom_asr.is_custom_asr(str(repo))
    assert not custom_asr.is_custom_asr(str(tmp_path / "asr"))  # no asr/ inside
    with mock.patch("medcomms.engine.custom_asr.file_exists", return_value=True) as f:
        assert custom_asr.is_custom_asr("org/model")
    f.assert_called_once_with("org/model", "asr/model.py")
    with mock.patch(
        "medcomms.engine.custom_asr.file_exists", side_effect=OSError("offline")
    ):
        assert not custom_asr.is_custom_asr("org/model")


def test_loads_the_repo_code_and_transcribes(repo):
    tokenizer = mock.Mock(words={1: "xin", 2: "chào"})
    with mock.patch(
        "medcomms.engine.custom_asr.AutoTokenizer.from_pretrained",
        return_value=tokenizer,
    ) as load_tokenizer:
        transcribe = custom_asr.load(str(repo), "cpu")
    load_tokenizer.assert_called_once_with("org/encoder")
    assert transcribe(np.zeros(16_000, dtype=np.float32)) == "xin chào"
    # The repo's package is imported under a private name, not as "asr".
    assert "asr" not in sys.modules
    assert f"{custom_asr._PACKAGE}.model" in sys.modules


def test_hub_models_are_downloaded_without_extras(repo):
    with (
        mock.patch(
            "medcomms.engine.custom_asr.snapshot_download", return_value=str(repo)
        ) as download,
        mock.patch("medcomms.engine.custom_asr.AutoTokenizer.from_pretrained"),
    ):
        custom_asr.load("org/model", "cpu")
    assert download.call_args.kwargs["allow_patterns"] == ["asr/*", "*.pt"]


def test_a_repo_without_weights_is_an_error(repo):
    (repo / "custom_asr.pt").unlink()
    with pytest.raises(FileNotFoundError, match="no .pt checkpoint"):
        custom_asr.load(str(repo), "cpu")
