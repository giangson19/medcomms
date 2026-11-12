"""Speech recognition with faster-whisper, a Hugging Face ASR model, or a
model that ships its own code (see ``custom_asr``)."""

import numpy as np
import torch
from faster_whisper import WhisperModel
from transformers import pipeline

from . import custom_asr
from .config import Direction
from .worker import END, Worker


class Transcriber(Worker):
    """Inbox: float32 speech segments and END. Outbox: text and END."""

    def __init__(self, inbox, outbox, stop_event, cfg, direction: Direction):
        super().__init__(inbox, outbox, stop_event, cfg.log_level)
        self._direction = direction
        self._device = cfg.device

    def setup(self):
        d = self._direction
        if d.asr_backend == "transformers" and custom_asr.is_custom_asr(d.asr_model):
            self._transcribe = custom_asr.load(d.asr_model, self._device)
        elif d.asr_backend == "transformers":
            self._pipe = pipeline(
                "automatic-speech-recognition",
                model=d.asr_model,
                device=0 if self._device == "cuda" else -1,
            )
            self._transcribe = lambda audio: self._pipe(audio)["text"]
        else:
            model = WhisperModel(
                d.asr_model, device=self._device, compute_type="float32"
            )

            def transcribe(audio):
                segments, _ = model.transcribe(audio, language=d.source)
                return " ".join(s.text for s in segments)

            self._transcribe = transcribe

    def process(self, segment):
        if segment is END:
            self.outbox.put(END)
            return
        # Each segment holds the whole utterance so far: when several are
        # waiting, only the newest needs transcribing.
        segment, ended = self.latest(segment)
        with torch.inference_mode():
            text = self._transcribe(segment.astype(np.float32)).strip()
        if text:
            self.outbox.put(text)
        if ended:
            self.outbox.put(END)
