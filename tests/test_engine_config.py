from unittest import mock

import pytest

from medcomms.engine.config import Config


def test_defaults_serve_both_directions():
    cfg = Config()
    assert cfg.directions == ("vi-en", "en-vi")
    assert cfg.port == 8000


@pytest.mark.parametrize(
    ("size", "name", "asr", "backend", "mt"),
    [
        ("small", "vi-en", "vinai/PhoWhisper-small", "transformers",
         "vinai/vinai-translate-vi2en-v2"),
        ("small", "en-vi", "small", "faster-whisper",
         "vinai/vinai-translate-en2vi-v2"),
        ("large", "vi-en", "vinai/PhoWhisper-large", "transformers",
         "vinai/vinai-translate-vi2en-v2"),
        ("large", "en-vi", "large-v3", "faster-whisper",
         "vinai/vinai-translate-en2vi-v2"),
    ],
)  # fmt: skip
def test_default_models(size, name, asr, backend, mt):
    d = Config(model_size=size).direction(name)
    assert (d.source, d.target) == tuple(name.split("-"))
    assert d.asr_model == asr
    assert d.asr_backend == backend
    assert d.mt_model == mt


def test_model_overrides_apply_per_direction():
    cfg = Config(
        asr_models={"vi-en": "openai/whisper-large-v3"},
        mt_models={"en-vi": "Helsinki-NLP/opus-mt-en-vi"},
    )
    vi_en, en_vi = cfg.direction("vi-en"), cfg.direction("en-vi")
    assert vi_en.asr_model == "openai/whisper-large-v3"
    assert vi_en.asr_backend == "transformers"
    assert vi_en.mt_model == "vinai/vinai-translate-vi2en-v2"
    assert en_vi.asr_model == "small"
    assert en_vi.mt_model == "Helsinki-NLP/opus-mt-en-vi"


def test_ctranslate2_whisper_conversions_use_faster_whisper():
    cfg = Config(asr_models={"en-vi": "Systran/faster-whisper-medium"})
    assert cfg.direction("en-vi").asr_backend == "faster-whisper"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"device": "tpu"}, "device"),
        ({"model_size": "medium"}, "model_size"),
        ({"directions": ()}, "at least one direction"),
        ({"directions": ("vi-fr",)}, "unknown direction"),
        ({"directions": ("vi-en",), "asr_models": {"en-vi": "tiny"}}, "not one of"),
        ({"directions": ("vi-en",), "mt_models": {"en-vi": "x"}}, "not one of"),
        ({"silence_threshold": 1.0}, "silence_threshold"),
        ({"vad_aggressiveness": 10}, "vad_aggressiveness"),
        ({"max_buffer_duration": 4}, "max_buffer_duration"),
    ],
)
def test_invalid_settings_are_rejected(kwargs, message):
    with pytest.raises(ValueError, match=message):
        Config(**kwargs)


def test_cuda_requires_a_gpu():
    with mock.patch("torch.cuda.is_available", return_value=False):
        with pytest.raises(ValueError, match="no CUDA device"):
            Config(device="cuda")
    with mock.patch("torch.cuda.is_available", return_value=True):
        assert Config(device="cuda").device == "cuda"
