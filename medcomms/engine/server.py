"""The translation engine: one pipeline per direction behind an HTTP server."""

import asyncio
import contextlib
import logging
import multiprocessing
import signal
import time

from aiohttp import web

from .config import Config
from .http import create_app
from .pipeline import Pipeline

log = logging.getLogger(__name__)


class Engine:
    """Runs one pipeline per configured direction and serves them on one port.

    ``run()`` blocks until SIGINT/SIGTERM, or until a worker process dies
    (e.g. a model failed to load), and returns a process exit code.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._stop_event = multiprocessing.get_context("spawn").Event()
        self.pipelines = {
            name: Pipeline(cfg.direction(name), cfg, self._stop_event)
            for name in cfg.directions
        }

    def run(self) -> int:
        for pipeline in self.pipelines.values():
            d = pipeline.direction
            log.info(
                "%s: ASR %s (%s), MT %s", d.name, d.asr_model, d.asr_backend, d.mt_model
            )
            pipeline.start()
        try:
            return asyncio.run(self._serve())
        finally:
            self.stop()

    def stop(self, grace: float = 5):
        """Ask every worker to stop; give them ``grace`` seconds in total."""
        self._stop_event.set()
        deadline = time.monotonic() + grace
        for pipeline in self.pipelines.values():
            pipeline.join(max(0.0, deadline - time.monotonic()))

    async def _serve(self) -> int:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

        runner = web.AppRunner(create_app(self.pipelines), handle_signals=False)
        await runner.setup()
        try:
            await web.TCPSite(runner, self.cfg.host, self.cfg.port).start()
            log.info("Engine listening on %s:%d", self.cfg.host, self.cfg.port)
            while not stop.is_set():
                dead = [n for n, p in self.pipelines.items() if not p.is_alive()]
                if dead:
                    log.error(
                        "Worker process for %s exited; shutting down", ", ".join(dead)
                    )
                    return 1
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=1)
            log.info("Shutting down")
            return 0
        finally:
            await runner.cleanup()
