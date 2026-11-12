"""Turns a stream of audio frames into utterances to transcribe."""

import numpy as np

from .config import CHUNK_SIZE, ENQUEUE_THRESHOLD_S, SAMPLE_RATE, SOFT_SILENCE_S
from .worker import END, Worker

_FRAME_S = CHUNK_SIZE / SAMPLE_RATE


def _frames(seconds: float) -> int:
    return int(round(seconds / _FRAME_S))


class SpeechSegmenter(Worker):
    """Buffers speech frames and emits growing segments of the current utterance.

    - Speech frames are appended to a buffer. After every
      ``ENQUEUE_THRESHOLD_S`` of new speech, the whole buffer is emitted, so the
      transcription of a long utterance keeps improving as it grows.
    - After ``SOFT_SILENCE_S`` of silence, any speech not yet emitted is
      emitted (this catches short answers such as "yes").
    - The utterance ends, and ``END`` is emitted, after ``silence_threshold``
      of silence, or when it grows past ``max_buffer_duration``.

    Inbox: int16 numpy frames, and ``END`` when a session closes.
    Outbox: float32 numpy segments and ``END``.
    """

    def __init__(self, inbox, outbox, stop_event, cfg):
        super().__init__(inbox, outbox, stop_event, cfg.log_level)
        self._aggressiveness = cfg.vad_aggressiveness
        self._max_frames = _frames(cfg.max_buffer_duration)
        self._soft_silence_frames = _frames(SOFT_SILENCE_S)
        self._silence_frames = _frames(cfg.silence_threshold)

    def setup(self):
        from .vad import VoiceActivityDetector

        self._vad = VoiceActivityDetector(self._aggressiveness)
        self._reset()

    def _reset(self):
        self._buffer = []
        self._sent = 0  # frames of the buffer already emitted
        self._silent = 0  # consecutive silent frames

    def process(self, frame):
        if frame is END:  # the session closed mid-utterance
            self._end()
            return
        audio = frame.astype(np.float32) / np.iinfo(np.int16).max

        if self._vad.is_speech(audio):
            self._silent = 0
            self._buffer.append(audio)
            if len(self._buffer) >= self._max_frames:
                self._end()
            elif (len(self._buffer) - self._sent) * _FRAME_S >= ENQUEUE_THRESHOLD_S:
                self._emit()
        else:
            self._silent += 1
            if self._silent == self._soft_silence_frames and self._buffer:
                self._emit()
            if self._silent >= self._silence_frames:
                self._end()

    def _emit(self):
        self.outbox.put(np.concatenate(self._buffer))
        self._sent = len(self._buffer)

    def _end(self):
        """Finish the utterance: emit what is left, then END."""
        if self._buffer:
            if self._sent < len(self._buffer):
                self._emit()
            self.outbox.put(END)
        self._reset()
