import os
import signal
import threading
from unittest import mock

import pytest

from medcomms.engine.config import Config
from medcomms.engine.server import Engine


class FakePipeline:
    def __init__(self, direction, cfg, stop_event):
        self.direction = direction
        self.alive = True
        self.started = self.joined = False

    def start(self):
        self.started = True

    def is_alive(self):
        return self.alive

    def is_ready(self):
        return True

    def join(self, timeout=5):
        self.joined = True


@pytest.fixture
def server():
    with mock.patch("medcomms.engine.server.Pipeline", FakePipeline):
        yield Engine(Config(port=0))


def test_one_pipeline_per_direction():
    with mock.patch("medcomms.engine.server.Pipeline", FakePipeline):
        server = Engine(Config(directions=("en-vi",)))
    assert list(server.pipelines) == ["en-vi"]


def test_sigterm_stops_cleanly(server):
    threading.Timer(0.5, os.kill, (os.getpid(), signal.SIGTERM)).start()
    assert server.run() == 0
    assert all(p.started and p.joined for p in server.pipelines.values())
    assert server._stop_event.is_set()


def test_exits_with_error_when_a_worker_dies(server):
    server.pipelines["en-vi"].alive = False
    assert server.run() == 1
    assert all(p.joined for p in server.pipelines.values())
