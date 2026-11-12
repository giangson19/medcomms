import struct

import pytest

from medcomms.engine.codec import OpusCodec
from medcomms.front.ogg import OggOpusWriter, ogg_crc, packet_samples


def read_pages(data: bytes):
    """Parse an Ogg stream, checking every page's CRC."""
    pages, pos = [], 0
    while pos < len(data):
        assert data[pos : pos + 4] == b"OggS"
        flags, granule, _, seq, crc, nseg = struct.unpack_from(
            "<xBqIIIB", data, pos + 4
        )
        lacing = data[pos + 27 : pos + 27 + nseg]
        size = 27 + nseg + sum(lacing)
        page = data[pos : pos + size]
        assert ogg_crc(page[:22] + b"\0\0\0\0" + page[26:]) == crc
        packets, offset, current = [], pos + 27 + nseg, b""
        for lace in lacing:
            current += data[offset : offset + lace]
            offset += lace
            if lace < 255:
                packets.append(current)
                current = b""
        pages.append(
            {"flags": flags, "granule": granule, "seq": seq, "packets": packets}
        )
        pos += size
    return pages


def test_writes_a_valid_ogg_opus_stream(tmp_path, frames):
    codec = OpusCodec()
    packets = [codec.encode(f.tobytes()) for f in frames[:60]]
    writer = OggOpusWriter(tmp_path / "a.opus")
    for p in packets:
        writer.write(p)
    writer.close()
    writer.close()  # idempotent

    pages = read_pages((tmp_path / "a.opus").read_bytes())
    assert pages[0]["flags"] == 0x02 and pages[0]["packets"][0].startswith(b"OpusHead")
    assert pages[1]["packets"][0].startswith(b"OpusTags")
    assert pages[-1]["flags"] == 0x04
    assert [p["seq"] for p in pages] == list(range(len(pages)))
    audio = [pkt for page in pages[2:] for pkt in page["packets"]]
    assert audio == packets
    assert pages[-1]["granule"] == 60 * 1920  # 60 packets of 40 ms at 48 kHz


def test_large_packets_use_255_lacing(tmp_path):
    writer = OggOpusWriter(tmp_path / "b.opus")
    big = bytes([0b11111000]) + b"\x01" * 600  # CELT 20 ms, one frame
    writer.write(big)
    writer.close()
    pages = read_pages((tmp_path / "b.opus").read_bytes())
    assert pages[-1]["packets"] == [big]


@pytest.mark.parametrize(
    ("packet", "samples"),
    [
        (b"", 0),
        (bytes([0b00000000]), 480),  # config 0: SILK 10 ms, 1 frame
        (bytes([0b00010000]), 1920),  # config 2: SILK 40 ms, 1 frame
        (bytes([0b00011001]), 5760),  # config 3: SILK 60 ms, 2 frames
        (bytes([0b11111001]), 1920),  # CELT FB 20 ms, 2 frames
        (bytes([0b11111011, 3]), 2880),  # CELT FB 20 ms, code 3 with 3 frames
        (bytes([0b11111011]), 0),  # truncated code-3 packet
    ],
)
def test_packet_durations(packet, samples):
    assert packet_samples(packet) == samples
