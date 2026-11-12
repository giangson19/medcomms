import multiprocessing
import wave
from pathlib import Path

import numpy as np
import pytest

from medcomms.engine.config import CHUNK_SIZE, SAMPLE_RATE

SAMPLE_WAV = Path(__file__).parent / "audio_samples" / "sample.wav"


@pytest.fixture(scope="session")
def speech() -> np.ndarray:
    """About 17 s of 16 kHz mono int16 speech, ending in silence."""
    with wave.open(str(SAMPLE_WAV), "rb") as wf:
        assert wf.getframerate() == SAMPLE_RATE
        assert wf.getnchannels() == 1
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)


@pytest.fixture(scope="session")
def frames(speech) -> list[np.ndarray]:
    """The sample split into 40 ms frames, as a client would send it."""
    return [
        speech[i : i + CHUNK_SIZE]
        for i in range(0, len(speech) - CHUNK_SIZE + 1, CHUNK_SIZE)
    ]


@pytest.fixture
def ctx():
    return multiprocessing.get_context("spawn")


@pytest.fixture
def no_worker_side_effects(monkeypatch):
    """Let tests call Worker.run() in-process without touching logging or signals."""
    monkeypatch.setattr("medcomms.engine.worker.setup_logging", lambda level: None)
    monkeypatch.setattr("medcomms.engine.worker.signal.signal", lambda *args: None)
