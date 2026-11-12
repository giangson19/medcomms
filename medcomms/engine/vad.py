"""Voice activity detection with Silero VAD."""

import numpy as np
import torch

from .config import SAMPLE_RATE

# Silero VAD accepts exactly 512-sample windows at 16 kHz.
_WINDOW = 512


class VoiceActivityDetector:
    """Decides whether an audio frame contains speech.

    The model runs on the CPU: it is fast enough there, and moving 40 ms
    frames to a GPU would cost more than it saves.
    """

    def __init__(self, aggressiveness: int):
        self._model, _ = torch.hub.load(
            repo_or_dir="snakers4/silero-vad", model="silero_vad", trust_repo=True
        )
        self._threshold = aggressiveness / 10

    def is_speech(self, audio: np.ndarray) -> bool:
        if audio.dtype != np.float32:
            raise ValueError("audio must be float32")
        with torch.inference_mode():
            for window in self._windows(audio):
                confidence = self._model(torch.from_numpy(window), SAMPLE_RATE).item()
                if confidence > self._threshold:
                    return True
        return False

    @staticmethod
    def _windows(audio: np.ndarray) -> list[np.ndarray]:
        """Split audio into 512-sample windows, padding or overlapping the tail."""
        if len(audio) < _WINDOW:
            return [np.pad(audio, (0, _WINDOW - len(audio)))]
        windows = [
            audio[i : i + _WINDOW] for i in range(0, len(audio) - _WINDOW + 1, _WINDOW)
        ]
        if len(audio) % _WINDOW:
            windows.append(audio[-_WINDOW:])
        return windows
