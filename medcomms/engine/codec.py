"""Opus decoding for audio frames received from clients."""

import opuslib

from .config import CHANNELS, CHUNK_SIZE, SAMPLE_RATE


class OpusCodec:
    def __init__(self):
        self._encoder = opuslib.Encoder(SAMPLE_RATE, CHANNELS, opuslib.APPLICATION_VOIP)
        self._decoder = opuslib.Decoder(SAMPLE_RATE, CHANNELS)

    def encode(self, pcm: bytes) -> bytes:
        """Encode exactly one 40 ms frame of 16-bit PCM."""
        return self._encoder.encode(pcm, CHUNK_SIZE)

    def decode(self, packet: bytes) -> bytes:
        """Decode one Opus packet to 16-bit PCM."""
        return self._decoder.decode(packet, CHUNK_SIZE)
