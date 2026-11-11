# test_peer.py
#
# Tests for the protocol rules in peerProcess.py, without any real network.
#
# Instead of real sockets, each neighbor gets a FakeConnection that just
# records every message the Peer tries to send. We feed messages straight
# into peer.on_message() and then check exactly what came out. This lets us
# test rules that the end-to-end test can't see, because breaking them makes
# the transfer wasteful or wrong but the file still arrives in the end.
#
# Run from the repo root:
#   python3 -m unittest discover -s tests -v
import os
import sys
import tempfile
import unittest

# so we can import the project files from the tests folder
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bitfield import Bitfield
from config import CommonConfig, PeerInfo
from message import Message, REQUEST, PIECE
from peerProcess import Peer

MY_ID = 1001
FILE_NAME = "thefile.dat"

# These tests need the TODO stubs in peiceStore.py, neighborstate.py and
# termination.py (Carson's and Eddy's tasks). Remove the @unittest.skip lines
# below once those are filled in.
NEEDS_STUBS = "waiting on Carson's and Eddy's TODO stubs"


# Stands in for a NeighborConnection. Peer only uses these three things on a
# connection, so that's all the fake needs.
class FakeConnection:
    def __init__(self, neighbor_id):
        self.neighbor_id = neighbor_id
        self.outgoing = True
        # every message the peer "sent" to this neighbor, in order
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)

    # just the REQUEST messages, as piece indexes
    def requests(self):
        indexes = []
        for msg in self.sent:
            if msg.type == REQUEST:
                indexes.append(msg.piece_index())
        return indexes

    # just the PIECE messages, as piece indexes
    def pieces(self):
        indexes = []
        for msg in self.sent:
            if msg.type == PIECE:
                indexes.append(msg.piece_index())
        return indexes


class PeerTestCase(unittest.TestCase):
    # Make a Peer in a temporary folder. The ConnectionManager and ChokeManager
    # get created but never started, so no sockets or timers are involved.
    #
    # num_pieces: how many pieces the file has (each piece is 10 bytes)
    # have_file: whether we start with the whole file
    def make_peer(self, num_pieces, have_file):
        self.work = tempfile.TemporaryDirectory()
        common = CommonConfig(num_preferred_neighbors=2, unchoking_interval=5,
                              optimistic_unchoking_interval=10, file_name=FILE_NAME,
                              file_size=num_pieces * 10, piece_size=10)
        peers = [PeerInfo(MY_ID, "127.0.0.1", 6001, have_file),
                 PeerInfo(1002, "127.0.0.1", 6002, False),
                 PeerInfo(1003, "127.0.0.1", 6003, False)]
        if have_file:
            os.makedirs(os.path.join(self.work.name, f"peer_{MY_ID}"))
            with open(os.path.join(self.work.name, f"peer_{MY_ID}", FILE_NAME), "wb") as f:
                f.write(bytes(range(num_pieces * 10)))
        self.peer = Peer(MY_ID, common, peers, base_dir=self.work.name)
        return self.peer

    def tearDown(self):
        self.peer.logger.close()
        self.work.cleanup()

    # Pretend a neighbor just connected and told us it has every piece.
    def connect_seed(self, neighbor_id, num_pieces):
        conn = FakeConnection(neighbor_id)
        self.peer.on_connected(conn)
        # a full bitfield: every piece set, spare bits at the end left at 0
        bits = Bitfield(num_pieces, full=True).to_bytes()
        self.peer.on_message(neighbor_id, Message.bitfield(bits))
        return conn


@unittest.skip(NEEDS_STUBS)
class TestRequests(PeerTestCase):
    # Two neighbors have the only piece we need. When both unchoke us, only
    # the first should get a REQUEST. Asking both would download the same
    # piece twice and waste bandwidth.
    def test_same_piece_not_requested_from_two_neighbors(self):
        self.make_peer(num_pieces=1, have_file=False)
        a = self.connect_seed(1002, 1)
        b = self.connect_seed(1003, 1)

        self.peer.on_message(1002, Message.unchoke())
        self.peer.on_message(1003, Message.unchoke())

        self.assertEqual(a.requests(), [0])
        self.assertEqual(b.requests(), [])

    # A asks for piece 0, then chokes us before sending it. That piece is never
    # coming from A, so it has to be freed up. When B unchokes us, we should
    # ask B for it. If it weren't freed, nobody would ever get asked for piece
    # 0 again and we'd never finish the file.
    def test_choke_frees_the_pending_request(self):
        self.make_peer(num_pieces=1, have_file=False)
        a = self.connect_seed(1002, 1)
        b = self.connect_seed(1003, 1)

        self.peer.on_message(1002, Message.unchoke())
        self.assertEqual(a.requests(), [0])
        self.peer.on_message(1002, Message.choke())
        self.peer.on_message(1003, Message.unchoke())

        self.assertEqual(b.requests(), [0])

    # Same idea, but A disconnects instead of choking us.
    def test_disconnect_frees_the_pending_request(self):
        self.make_peer(num_pieces=1, have_file=False)
        a = self.connect_seed(1002, 1)
        b = self.connect_seed(1003, 1)

        self.peer.on_message(1002, Message.unchoke())
        self.assertEqual(a.requests(), [0])
        self.peer.on_disconnected(1002)
        self.peer.on_message(1003, Message.unchoke())

        self.assertEqual(b.requests(), [0])

    # After a piece arrives we should ask the same neighbor for the next one,
    # and we should never ask for a piece we already have.
    def test_keeps_requesting_until_done(self):
        self.make_peer(num_pieces=3, have_file=False)
        a = self.connect_seed(1002, 3)
        self.peer.on_message(1002, Message.unchoke())
        # Play the part of A: answer each request with the piece. Every answer
        # should trigger the next request, until there's nothing left to ask for.
        answered = 0
        while answered < len(a.requests()):
            index = a.requests()[answered]
            answered += 1
            self.peer.on_message(1002, Message.piece(index, b"x" * 10))

        self.assertEqual(sorted(a.requests()), [0, 1, 2])
        self.assertTrue(self.peer.store.is_complete())


@unittest.skip(NEEDS_STUBS)
class TestChoking(PeerTestCase):
    # A neighbor we're choking asks for a piece. The spec says we ignore it.
    # Once we unchoke them, the same request should get the piece.
    def test_no_pieces_sent_while_choking(self):
        self.make_peer(num_pieces=2, have_file=True)
        conn = FakeConnection(1002)
        self.peer.on_connected(conn)
        n = self.peer.get_neighbor(1002)

        self.peer.on_message(1002, Message.request(1))
        self.assertEqual(conn.pieces(), [])

        n.unchoke()
        self.peer.on_message(1002, Message.request(1))
        self.assertEqual(conn.pieces(), [1])
        # and it's the right 10 bytes of the file (the last message sent)
        piece = conn.sent[-1]
        self.assertEqual(piece.type, PIECE)
        self.assertEqual(piece.piece_data(), bytes(range(10, 20)))

        # choke them again, and requests should be ignored again
        n.choke()
        self.peer.on_message(1002, Message.request(0))
        self.assertEqual(conn.pieces(), [1])


if __name__ == "__main__":
    unittest.main()
