"""Logging setup shared by the main process and the worker processes.

Workers are started with the "spawn" method on CUDA (and by default on macOS),
so they do not inherit the parent's logging configuration; each one calls
``setup_logging`` itself.

Transcripts and translations are never logged: they contain patient speech.
"""

import logging


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-7s %(processName)s %(name)s: %(message)s",
        force=True,
    )
    # Both libraries log routine events at INFO: websockets every plain HTTP
    # request (page loads, health checks), faster-whisper every segment.
    for name in ("websockets", "faster_whisper"):
        logging.getLogger(name).setLevel(logging.WARNING)
