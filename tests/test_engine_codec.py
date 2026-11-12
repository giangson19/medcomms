import numpy as np

from medcomms.engine.codec import OpusCodec


def test_roundtrip_keeps_frame_length_and_compresses(frames):
    codec = OpusCodec()
    encoded_bytes = 0
    for frame in frames:
        packet = codec.encode(frame.tobytes())
        encoded_bytes += len(packet)
        decoded = np.frombuffer(codec.decode(packet), dtype=np.int16)
        assert len(decoded) == len(frame)

    raw_bytes = sum(f.nbytes for f in frames)
    assert encoded_bytes < 0.1 * raw_bytes
