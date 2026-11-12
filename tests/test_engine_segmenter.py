import numpy as np
import pytest

from medcomms.engine.config import CHUNK_SIZE, Config
from medcomms.engine.segmenter import SpeechSegmenter
from medcomms.engine.worker import END


class ListQueue(list):
    put = list.append


@pytest.fixture(scope="module")
def loaded():
    """A segmenter whose VAD model is loaded once for the module."""
    seg = SpeechSegmenter(None, ListQueue(), None, Config())
    seg.setup()
    return seg


@pytest.fixture
def segmenter(loaded):
    vad = loaded._vad
    loaded.outbox.clear()
    loaded._reset()
    yield loaded
    loaded._vad = vad


def test_speech_produces_float32_segments(segmenter, frames):
    for frame in frames:
        segmenter.process(frame)
    segments = [s for s in segmenter.outbox if s is not END]
    assert segments
    assert ends(segmenter.outbox)  # the sample ends in silence
    assert all(s.dtype == np.float32 for s in segments)
    assert all(np.abs(s).max() <= 1.0 for s in segments)


def test_long_speech_is_trimmed_to_max_buffer(segmenter, frames):
    for frame in frames:
        segmenter.process(frame)
    longest_s = max(len(s) for s in segmenter.outbox if s is not END) / 16_000
    assert longest_s <= Config().max_buffer_duration


def test_silence_alone_produces_nothing(segmenter):
    for _ in range(100):
        segmenter.process(np.zeros(CHUNK_SIZE, dtype=np.int16))
    assert segmenter.outbox == []


def test_short_utterance_is_flushed_after_a_pause(segmenter):
    segmenter._vad = FakeVad([True] * 5 + [False] * 20)
    for _ in range(25):
        segmenter.process(np.ones(CHUNK_SIZE, dtype=np.int16))
    # 5 frames (0.2 s) is below the 1 s threshold, so only the soft-silence
    # flush emits it, once.
    assert len(segmenter.outbox) == 1
    assert len(segmenter.outbox[0]) == 5 * CHUNK_SIZE


def test_long_silence_resets_the_utterance(segmenter):
    segmenter._vad = FakeVad([True] * 5 + [False] * 50 + [True] * 30)
    for _ in range(85):
        segmenter.process(np.ones(CHUNK_SIZE, dtype=np.int16))
    # The first utterance ends with END; the second starts from an empty buffer.
    first, end, second = segmenter.outbox
    assert len(first) == 5 * CHUNK_SIZE and end is END
    assert len(second) == 25 * CHUNK_SIZE


def test_closing_a_session_ends_the_utterance(segmenter):
    segmenter._vad = FakeVad([True] * 30)
    for _ in range(30):
        segmenter.process(np.ones(CHUNK_SIZE, dtype=np.int16))
    segmenter.process(END)
    # Emitted after 25 frames, then the rest and END when the session closed.
    assert [len(s) if s is not END else END for s in segmenter.outbox] == [
        25 * CHUNK_SIZE,
        30 * CHUNK_SIZE,
        END,
    ]
    segmenter.process(END)  # nothing in progress: no extra END
    assert len(ends(segmenter.outbox)) == 1


def test_long_utterances_are_split(segmenter):
    max_frames = Config().max_buffer_duration * 25  # in 40 ms frames
    segmenter._vad = FakeVad([True] * (max_frames + 10))
    for _ in range(max_frames + 10):
        segmenter.process(np.ones(CHUNK_SIZE, dtype=np.int16))
    end = ends(segmenter.outbox)[0]
    assert len(segmenter.outbox[end - 1]) == max_frames * CHUNK_SIZE
    assert all(len(s) < max_frames * CHUNK_SIZE for s in segmenter.outbox[end + 1 :])


def ends(outbox) -> list[int]:
    """Positions of END markers (outboxes also hold numpy arrays)."""
    return [i for i, item in enumerate(outbox) if item is END]


class FakeVad:
    def __init__(self, decisions):
        self._decisions = iter(decisions)

    def is_speech(self, audio):
        return next(self._decisions)
