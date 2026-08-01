from __future__ import annotations

from dataclasses import dataclass


class BencodeError(ValueError):
    pass


@dataclass
class DecodedTorrent:
    value: dict
    info_bytes: bytes


class Decoder:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def _peek(self) -> int:
        if self.pos >= len(self.data):
            raise BencodeError("Unexpected end of bencoded data")
        return self.data[self.pos]

    def parse(self):
        token = self._peek()
        if token == ord("i"):
            return self._parse_int()
        if token == ord("l"):
            return self._parse_list()
        if token == ord("d"):
            return self._parse_dict()
        if ord("0") <= token <= ord("9"):
            return self._parse_bytes()
        raise BencodeError(f"Invalid bencode token at byte {self.pos}")

    def _parse_int(self) -> int:
        self.pos += 1
        end = self.data.find(b"e", self.pos)
        if end < 0:
            raise BencodeError("Unterminated integer")
        raw = self.data[self.pos:end]
        if not raw:
            raise BencodeError("Empty integer")
        if raw == b"-0" or (raw.startswith(b"0") and len(raw) > 1) or (raw.startswith(b"-0") and len(raw) > 2):
            raise BencodeError("Invalid integer encoding")
        try:
            value = int(raw)
        except ValueError as exc:
            raise BencodeError("Invalid integer") from exc
        self.pos = end + 1
        return value

    def _parse_bytes(self) -> bytes:
        colon = self.data.find(b":", self.pos)
        if colon < 0:
            raise BencodeError("Invalid byte string")
        raw_length = self.data[self.pos:colon]
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise BencodeError("Invalid byte string length") from exc
        if length < 0:
            raise BencodeError("Negative byte string length")
        start = colon + 1
        end = start + length
        if end > len(self.data):
            raise BencodeError("Byte string extends beyond input")
        self.pos = end
        return self.data[start:end]

    def _parse_list(self) -> list:
        self.pos += 1
        result = []
        while self._peek() != ord("e"):
            result.append(self.parse())
        self.pos += 1
        return result

    def _parse_dict(self) -> dict:
        self.pos += 1
        result = {}
        while self._peek() != ord("e"):
            key = self._parse_bytes()
            result[key] = self.parse()
        self.pos += 1
        return result


def decode_torrent(data: bytes) -> DecodedTorrent:
    decoder = Decoder(data)
    if decoder._peek() != ord("d"):
        raise BencodeError("Torrent metainfo must be a dictionary")

    decoder.pos += 1
    result = {}
    info_bytes: bytes | None = None

    while decoder._peek() != ord("e"):
        key = decoder._parse_bytes()
        value_start = decoder.pos
        value = decoder.parse()
        value_end = decoder.pos
        result[key] = value
        if key == b"info":
            info_bytes = data[value_start:value_end]

    decoder.pos += 1
    if decoder.pos != len(data):
        raise BencodeError("Trailing data after torrent metainfo")
    if info_bytes is None:
        raise BencodeError("Torrent has no info dictionary")
    return DecodedTorrent(value=result, info_bytes=info_bytes)
