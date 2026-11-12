"""Base class for the pipeline's worker processes."""

import logging
import queue
import signal
from multiprocessing.context import SpawnProcess

from ..log import setup_logging

log = logging.getLogger(__name__)

# Passed down the pipeline when an utterance is over.
END = None


class Worker(SpawnProcess):
    """A process that takes items from an inbox queue, one at a time.

    Workers always use the "spawn" start method so CUDA can be initialised
    safely in each of them. Subclasses load their models in ``setup`` (which
    runs inside the worker process) and handle each item in ``process``.
    """

    def __init__(self, inbox, outbox, stop_event, log_level: str):
        super().__init__(daemon=True)
        self.inbox = inbox
        self.outbox = outbox
        self.ready = self._ctx().Event()
        self._stop_event = stop_event
        self._log_level = log_level

    @staticmethod
    def _ctx():
        import multiprocessing

        return multiprocessing.get_context("spawn")

    def setup(self) -> None:
        """Load models. Runs once, inside the worker process."""

    def process(self, item) -> None:
        raise NotImplementedError

    def latest(self, item):
        """Skip ahead to the newest queued item of the current utterance.

        Returns ``(newest, ended)``: ``ended`` is True if an END marker
        followed, in which case the marker has been taken from the inbox.
        Items after the marker (the next utterance) are left in the inbox.
        """
        while True:
            try:
                following = self.inbox.get_nowait()
            except queue.Empty:
                return item, False
            if following is END:
                return item, True
            item = following

    def run(self):
        setup_logging(self._log_level)
        # The main process handles Ctrl+C and tells workers to stop.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            self.setup()
            self.ready.set()
            log.info("%s ready", self.name)
            while not self._stop_event.is_set():
                try:
                    item = self.inbox.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    self.process(item)
                except Exception:
                    log.exception("%s failed to process an item", self.name)
        except Exception:
            log.exception("%s crashed", self.name)
            raise SystemExit(1)
        finally:
            self.outbox.cancel_join_thread()
            log.info("%s stopped", self.name)
