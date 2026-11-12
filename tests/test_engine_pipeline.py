import time
from unittest import mock

import numpy as np

from medcomms.engine.config import Config
from medcomms.engine.pipeline import Pipeline
from medcomms.engine.segmenter import SpeechSegmenter
from medcomms.engine.transcriber import Transcriber
from medcomms.engine.translator import Translator
from medcomms.engine.worker import END


def make(ctx, name="vi-en") -> Pipeline:
    cfg = Config()
    return Pipeline(cfg.direction(name), cfg, ctx.Event())


def test_workers_are_chained_through_queues(ctx):
    p = make(ctx)
    seg, asr, mt = p.workers
    assert (type(seg), type(asr), type(mt)) == (
        SpeechSegmenter,
        Transcriber,
        Translator,
    )
    assert seg.inbox is p.audio
    assert seg.outbox is asr.inbox
    assert asr.outbox is mt.inbox
    assert mt.outbox is p.results
    assert [w.name for w in p.workers] == [
        "SpeechSegmenter[vi-en]",
        "Transcriber[vi-en]",
        "Translator[vi-en]",
    ]


def test_ready_and_alive_reflect_every_worker(ctx):
    p = make(ctx)
    assert not p.is_ready()
    for w in p.workers:
        w.ready.set()
    assert p.is_ready()

    with mock.patch.object(Transcriber, "is_alive", return_value=False):
        assert not p.is_alive()


def test_start_and_join(ctx):
    p = make(ctx)
    with (
        mock.patch.object(SpeechSegmenter, "start") as start,
        mock.patch.object(SpeechSegmenter, "join") as join,
        mock.patch.object(SpeechSegmenter, "is_alive", return_value=True),
        mock.patch.object(SpeechSegmenter, "terminate") as terminate,
        mock.patch.object(Transcriber, "start"),
        mock.patch.object(Transcriber, "join"),
        mock.patch.object(Translator, "start"),
        mock.patch.object(Translator, "join"),
    ):
        p.start()
        p.join(timeout=1)
    start.assert_called_once()
    assert 0 < join.call_args.args[0] <= 1  # shares one deadline
    terminate.assert_called_once()  # still alive after join


def test_ending_a_session_flushes_and_closes_the_utterance(ctx):
    p = make(ctx)
    p.results.put({"translation": "stale"})
    time.sleep(0.2)
    p.end_session()
    time.sleep(0.2)
    assert p.results.empty()
    assert p.audio.get(timeout=1) is END


def test_flush_empties_audio_and_results(ctx):
    p = make(ctx)
    p.audio.put(np.zeros(640, dtype=np.int16))
    p.results.put({"translation": "stale"})
    time.sleep(0.2)  # let the queue feeder threads deliver
    p.flush()
    assert p.audio.empty() and p.results.empty()
