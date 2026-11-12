import dataclasses
import queue
from unittest import mock

import numpy as np
import pytest

from medcomms.engine.config import Config
from medcomms.engine.transcriber import Transcriber
from medcomms.engine.worker import END


class ListQueue(list):
    put = list.append


@pytest.fixture(autouse=True)
def standard_models(monkeypatch):
    """No network: models are not custom-code ones unless a test says so."""
    monkeypatch.setattr(
        "medcomms.engine.transcriber.custom_asr.is_custom_asr", lambda model: False
    )


def make(cfg: Config, name: str) -> Transcriber:
    return Transcriber(queue.Queue(), ListQueue(), None, cfg, cfg.direction(name))


def test_vietnamese_uses_phowhisper_via_transformers():
    t = make(Config(), "vi-en")
    pipe = mock.Mock(return_value={"text": " Chào bác sĩ. "})
    with mock.patch(
        "medcomms.engine.transcriber.pipeline", return_value=pipe
    ) as factory:
        t.setup()
        t.process(np.zeros(16_000, dtype=np.float32))
    factory.assert_called_once_with(
        "automatic-speech-recognition", model="vinai/PhoWhisper-small", device=-1
    )
    assert t.outbox == ["Chào bác sĩ."]


def test_english_uses_faster_whisper_with_source_language():
    t = make(Config(), "en-vi")
    segments = [mock.Mock(text=" I have"), mock.Mock(text=" a headache.")]
    with mock.patch("medcomms.engine.transcriber.WhisperModel") as whisper:
        whisper.return_value.transcribe.return_value = (iter(segments), None)
        t.setup()
        t.process(np.zeros(16_000, dtype=np.float32))
    whisper.assert_called_once_with("small", device="cpu", compute_type="float32")
    assert whisper.return_value.transcribe.call_args.kwargs == {"language": "en"}
    assert t.outbox == ["I have  a headache."]


def test_overridden_hf_model_on_cuda():
    cfg = Config(asr_models={"en-vi": "openai/whisper-small"})
    with mock.patch("torch.cuda.is_available", return_value=True):
        cfg = dataclasses.replace(cfg, device="cuda")
    t = make(cfg, "en-vi")
    with mock.patch("medcomms.engine.transcriber.pipeline") as factory:
        t.setup()
    assert factory.call_args.kwargs == {
        "model": "openai/whisper-small",
        "device": 0,
    }


def test_blank_transcriptions_are_dropped():
    t = make(Config(), "vi-en")
    with mock.patch(
        "medcomms.engine.transcriber.pipeline", return_value=lambda a: {"text": "  "}
    ):
        t.setup()
        t.process(np.zeros(16_000, dtype=np.float32))
    assert t.outbox == []


def test_only_the_newest_segment_is_transcribed_and_end_passes_through():
    t = make(Config(), "vi-en")
    pipe = mock.Mock(side_effect=lambda audio: {"text": f"{len(audio)} samples"})
    with mock.patch("medcomms.engine.transcriber.pipeline", return_value=pipe):
        t.setup()
    # Three growing segments of one utterance wait in the inbox, then END,
    # then the start of the next utterance.
    for n in (200, 300):
        t.inbox.put(np.zeros(n, dtype=np.float32))
    t.inbox.put(END)
    t.inbox.put(np.zeros(50, dtype=np.float32))

    t.process(np.zeros(100, dtype=np.float32))
    assert t.outbox == ["300 samples", END]  # 100 and 200 skipped
    assert pipe.call_count == 1
    assert t.inbox.qsize() == 1  # the next utterance is left alone

    t.process(END)
    assert t.outbox[-1] is END


def test_models_with_their_own_code_use_the_custom_loader():
    cfg = Config(asr_models={"vi-en": "org/custom-ctc"})
    t = make(cfg, "vi-en")
    with (
        mock.patch(
            "medcomms.engine.transcriber.custom_asr.is_custom_asr", return_value=True
        ),
        mock.patch(
            "medcomms.engine.transcriber.custom_asr.load",
            return_value=lambda audio: " xin chào ",
        ) as load,
        mock.patch("medcomms.engine.transcriber.pipeline") as hf_pipeline,
    ):
        t.setup()
        t.process(np.zeros(16_000, dtype=np.float32))
    load.assert_called_once_with("org/custom-ctc", "cpu")
    hf_pipeline.assert_not_called()
    assert t.outbox == ["xin chào"]
