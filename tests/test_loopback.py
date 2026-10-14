import unittest

from message import Message
from handshake import build_handshake, parse_handshake


class TestHandshake(unittest.TestCase):
    def test_length_and_roundtrip(self):
        # TODO: build_handshake(1001) is 32 bytes and parses back to 1001
        pass

    def test_bad_header_rejected(self):
        # TODO: parse_handshake should raise ValueError
        pass


class TestMessageEncoding(unittest.TestCase):
    def test_every_type_roundtrips(self):
        # TODO: encode each of the 8 types, check length field and contents
        pass


class TestSocketLoopback(unittest.TestCase):
    def test_send_all_types_over_real_sockets(self):
        # TODO: ServerSocket + client socket, handshake both ways, then send
        #       every message type and check read_message returns the same
        pass


if __name__ == "__main__":
    unittest.main()
