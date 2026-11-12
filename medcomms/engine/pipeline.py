"""One translation direction: audio in, transcription and translation out."""

import multiprocessing
import queue
import time

from .config import Config, Direction
from .segmenter import SpeechSegmenter
from .transcriber import Transcriber
from .translator import Translator
from .worker import END

_ctx = multiprocessing.get_context("spawn")


class Pipeline:
    """Segmenter → Transcriber → Translator, each in its own process.

    ``audio`` takes int16 frames from the client; ``results`` yields dicts with
    ``timestamp``, ``transcription`` and ``translation``.
    """

    def __init__(self, direction: Direction, cfg: Config, stop_event):
        self.direction = direction
        self.audio = _ctx.Queue()
        self.results = _ctx.Queue()
        segments = _ctx.Queue()
        texts = _ctx.Queue()
        self.workers = [
            SpeechSegmenter(self.audio, segments, stop_event, cfg),
            Transcriber(segments, texts, stop_event, cfg, direction),
            Translator(texts, self.results, stop_event, cfg, direction),
        ]
        for worker in self.workers:
            worker.name = f"{type(worker).__name__}[{direction.name}]"

    def start(self):
        for worker in self.workers:
            worker.start()

    def is_ready(self) -> bool:
        return all(w.ready.is_set() for w in self.workers)

    def is_alive(self) -> bool:
        return all(w.is_alive() for w in self.workers)

    def join(self, timeout: float = 5):
        """Wait up to ``timeout`` seconds in total, then terminate stragglers
        (e.g. a worker still downloading a model)."""
        deadline = time.monotonic() + timeout
        for worker in self.workers:
            worker.join(max(0.0, deadline - time.monotonic()))
        for worker in self.workers:
            if worker.is_alive():
                worker.terminate()

    def end_session(self):
        """Close off any utterance in progress, so none of it reaches the next
        session (its final result is flushed when that session starts)."""
        self.flush()
        self.audio.put(END)

    def flush(self):
        """Drop queued audio and results, e.g. when a client disconnects."""
        for q in (self.audio, self.results):
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
