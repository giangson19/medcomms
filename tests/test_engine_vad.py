import numpy as np
import pytest

from medcomms.engine.vad import VoiceActivityDetector


@pytest.fixture(scope="module")
def vad():
    return VoiceActivityDetector(aggressiveness=8)


def test_detects_speech(vad, frames):
    assert any(vad.is_speech(f.astype(np.float32) / 32767) for f in frames)


def test_silence_is_not_speech(vad):
    assert vad.is_speech(np.zeros(640, dtype=np.float32)) is False


def test_requires_float32(vad):
    with pytest.raises(ValueError, match="float32"):
        vad.is_speech(np.zeros(512, dtype=np.int16))


@pytest.mark.parametrize(
    ("length", "windows"),
    [(400, 1), (512, 1), (640, 2), (1024, 2), (1100, 3)],
)
def test_windows_are_512_samples(length, windows):
    result = VoiceActivityDetector._windows(np.arange(length, dtype=np.float32))
    assert len(result) == windows
    assert all(len(w) == 512 for w in result)
