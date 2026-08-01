import hashlib
import tempfile
import unittest
from pathlib import Path

from app.bencode import decode_torrent


def bstr(value: bytes) -> bytes:
    return str(len(value)).encode() + b":" + value


def bdict(items: list[tuple[bytes, bytes]]) -> bytes:
    return b"d" + b"".join(bstr(key) + value for key, value in sorted(items)) + b"e"


def bint(value: int) -> bytes:
    return b"i" + str(value).encode() + b"e"


def blist(items: list[bytes]) -> bytes:
    return b"l" + b"".join(items) + b"e"


class BencodeTests(unittest.TestCase):
    def test_exact_info_slice(self):
        info = bdict([
            (b"length", bint(123)),
            (b"name", bstr(b"part.stl")),
            (b"piece length", bint(16384)),
            (b"pieces", bstr(b"x" * 20)),
        ])
        torrent = bdict([(b"announce", bstr(b"udp://tracker.invalid")), (b"info", info)])
        decoded = decode_torrent(torrent)
        self.assertEqual(decoded.info_bytes, info)
        self.assertEqual(hashlib.sha1(decoded.info_bytes).hexdigest(), hashlib.sha1(info).hexdigest())


class ParserTests(unittest.TestCase):
    def test_single_stl_torrent_shape(self):
        # Import here so config can be provided by test environment if dependencies are installed.
        from app.torrent_parser import parse_torrent_file

        info = bdict([
            (b"length", bint(4096)),
            (b"name", bstr(b"benchy.stl")),
            (b"piece length", bint(16384)),
            (b"pieces", bstr(b"x" * 20)),
        ])
        torrent = bdict([(b"info", info)])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "benchy.torrent"
            path.write_bytes(torrent)
            parsed = parse_torrent_file(path)
        self.assertEqual(parsed.name, "benchy.stl")
        self.assertEqual(parsed.model_file_count, 1)
        self.assertEqual(parsed.formats, ["stl"])
        self.assertEqual(parsed.total_size, 4096)


if __name__ == "__main__":
    unittest.main()
