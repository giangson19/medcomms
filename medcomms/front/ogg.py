"""Write Opus packets to an Ogg Opus file (RFC 7845) without re-encoding.

The browser already sends Opus packets, so a session is stored as received:
about 14 MB per hour, playable in any media player.
"""

import struct
from pathlib import Path

_INPUT_SAMPLE_RATE = 16_000
_PRE_SKIP = 312  # libopus encoder lookahead at 48 kHz
_PACKETS_PER_PAGE = 25  # ~1 s per page with 40 ms packets


def _crc_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) if crc & 0x80000000 else crc << 1
        table.append(crc & 0xFFFFFFFF)
    return table


_CRC = _crc_table()


def ogg_crc(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC[((crc >> 24) ^ byte) & 0xFF]
    return crc


# Frame duration in 48 kHz samples for each TOC configuration (RFC 6716 §3.1).
_FRAME_SAMPLES = (
    [480, 960, 1920, 2880] * 3  # SILK NB/MB/WB: 10, 20, 40, 60 ms
    + [480, 960] * 2  # Hybrid SWB/FB: 10, 20 ms
    + [120, 240, 480, 960] * 4  # CELT NB/WB/SWB/FB: 2.5, 5, 10, 20 ms
)


def packet_samples(packet: bytes) -> int:
    """Duration of an Opus packet in 48 kHz samples."""
    if not packet:
        return 0
    toc = packet[0]
    frame = _FRAME_SAMPLES[toc >> 3]
    code = toc & 0x03
    if code == 0:
        frames = 1
    elif code in (1, 2):
        frames = 2
    else:
        frames = packet[1] & 0x3F if len(packet) > 1 else 0
    return frame * frames


class OggOpusWriter:
    """Append Opus packets to ``path``; call ``close()`` to finish the file."""

    def __init__(self, path: Path, serial: int = 0x4D434F4D):
        self._file = open(path, "wb")
        self._serial = serial
        self._sequence = 0
        self._granule = 0
        self._pending: list[bytes] = []
        head = b"OpusHead" + struct.pack(
            "<BBHIhB", 1, 1, _PRE_SKIP, _INPUT_SAMPLE_RATE, 0, 0
        )
        vendor = b"medcomms"
        tags = (
            b"OpusTags" + struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", 0)
        )
        self._write_page([head], granule=0, flags=0x02)
        self._write_page([tags], granule=0)

    def write(self, packet: bytes):
        self._pending.append(packet)
        self._granule += packet_samples(packet)
        if len(self._pending) >= _PACKETS_PER_PAGE:
            self._flush()

    def close(self):
        if self._file.closed:
            return
        # The last page carries the end-of-stream flag, even if it is empty.
        self._write_page(self._pending, granule=self._granule, flags=0x04)
        self._pending = []
        self._file.close()

    def _flush(self):
        if self._pending:
            self._write_page(self._pending, granule=self._granule)
            self._pending = []

    def _write_page(self, packets: list[bytes], granule: int, flags: int = 0):
        lacing = bytearray()
        for packet in packets:
            lacing += b"\xff" * (len(packet) // 255) + bytes([len(packet) % 255])
        header = struct.pack(
            "<4sBBqIIIB",
            b"OggS",
            0,
            flags,
            granule,
            self._serial,
            self._sequence,
            0,
            len(lacing),
        )
        page = header + bytes(lacing) + b"".join(packets)
        crc = ogg_crc(page)
        page = page[:22] + struct.pack("<I", crc) + page[26:]
        self._file.write(page)
        self._file.flush()
        self._sequence += 1
