import queue
import time

import pytest

from medcomms.engine.worker import Worker


class Echo(Worker):
    """Doubles numbers; fails on negative ones; stops after `limit` items."""

    def __init__(self, inbox, outbox, stop_event, limit=None, fail_setup=False):
        super().__init__(inbox, outbox, stop_event, "INFO")
        self.limit = limit
        self.fail_setup = fail_setup
        self.seen = 0

    def setup(self):
        if self.fail_setup:
            raise RuntimeError("model download failed")

    def process(self, item):
        self.seen += 1
        if self.limit is not None and self.seen >= self.limit:
            self._stop_event.set()
        if item < 0:
            raise ValueError("negative")
        self.outbox.put(item * 2)


@pytest.mark.usefixtures("no_worker_side_effects")
def test_run_processes_items_and_survives_bad_ones(ctx, caplog):
    inbox, outbox, stop = ctx.Queue(), ctx.Queue(), ctx.Event()
    for item in (1, -1, 3):
        inbox.put(item)
    worker = Echo(inbox, outbox, stop, limit=3)

    worker.run()

    assert worker.ready.is_set()
    assert [outbox.get(timeout=1), outbox.get(timeout=1)] == [2, 6]
    assert "failed to process an item" in caplog.text


@pytest.mark.usefixtures("no_worker_side_effects")
def test_failed_setup_exits_with_error(ctx, caplog):
    worker = Echo(ctx.Queue(), ctx.Queue(), ctx.Event(), fail_setup=True)
    with pytest.raises(SystemExit) as exc:
        worker.run()
    assert exc.value.code == 1
    assert not worker.ready.is_set()
    assert "crashed" in caplog.text


def test_runs_in_a_separate_process(ctx):
    inbox, outbox, stop = ctx.Queue(), ctx.Queue(), ctx.Event()
    worker = Echo(inbox, outbox, stop)
    worker.start()
    try:
        inbox.put(21)
        assert outbox.get(timeout=30) == 42
        assert worker.ready.is_set()
    finally:
        stop.set()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert worker.exitcode == 0


def test_worker_waits_on_an_empty_inbox(ctx):
    inbox, outbox, stop = ctx.Queue(), ctx.Queue(), ctx.Event()
    worker = Echo(inbox, outbox, stop)
    worker.start()
    try:
        assert worker.ready.wait(timeout=30)
        time.sleep(0.7)  # at least one inbox timeout
        assert worker.is_alive()
        with pytest.raises(queue.Empty):
            outbox.get_nowait()
    finally:
        stop.set()
        worker.join(timeout=5)


def test_latest_skips_to_the_newest_item_before_end(ctx):
    from medcomms.engine.worker import END

    inbox = ctx.Queue()
    worker = Echo(inbox, ctx.Queue(), ctx.Event())
    for item in (2, 3, END, 9):
        inbox.put(item)
    time.sleep(0.2)  # let the queue feeder thread deliver
    assert worker.latest(1) == (3, True)
    assert inbox.get(timeout=1) == 9  # the next utterance is untouched
    assert worker.latest(4) == (4, False)
