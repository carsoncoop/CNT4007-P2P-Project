# test_pieces.py
#
# Tests for Carson's part: config parsing, Bitfield, and PieceStore (saving pieces,
# picking random pieces, keeping track of requests). None of this needs the
# network so these run fast.
#
# Run from the repo root:
#   python3 -m unittest discover -s tests -v
import os
import random
import sys
import tempfile
import threading
import unittest

# lets us import the project files from inside the tests folder
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bitfield import Bitfield
from config import CommonConfig, load_common, load_peers
from peiceStore import PieceStore


COMMON_CFG = """NumberOfPreferredNeighbors 2
UnchokingInterval 5
OptimisticUnchokingInterval 15
FileName TheFile.dat
FileSize 10000232
PieceSize 32768
"""

PEER_INFO_CFG = """1001 lin114-00.cise.ufl.edu 6008 1
1002 lin114-01.cise.ufl.edu 6008 0

1003 localhost 6010 0
"""


# writes some text to a file in the temp folder and gives back the path
def write_temp(folder, name, text):
    path = os.path.join(folder, name)
    with open(path, "w") as f:
        f.write(text)
    return path


class TestConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    # numbers straight from the project description: 306 pieces, last one is 5992 bytes
    def test_spec_example(self):
        c = load_common(write_temp(self.dir, "Common.cfg", COMMON_CFG))
        self.assertEqual(c.num_preferred_neighbors, 2)
        self.assertEqual(c.unchoking_interval, 5)
        self.assertEqual(c.optimistic_unchoking_interval, 15)
        self.assertEqual(c.file_name, "TheFile.dat")
        self.assertEqual(c.num_pieces, 306)
        self.assertEqual(c.piece_length(0), 32768)
        self.assertEqual(c.piece_length(304), 32768)
        self.assertEqual(c.piece_length(305), 5992)

    def test_missing_key_rejected(self):
        text = COMMON_CFG.replace("PieceSize 32768\n", "")
        with self.assertRaises(ValueError):
            load_common(write_temp(self.dir, "Common.cfg", text))

    # order matters here since it decides who connects to who.
    # the blank line in PEER_INFO_CFG should just get skipped
    def test_peers_keep_file_order(self):
        peers = load_peers(write_temp(self.dir, "PeerInfo.cfg", PEER_INFO_CFG))
        self.assertEqual([p.peer_id for p in peers], [1001, 1002, 1003])
        self.assertEqual(peers[0].host, "lin114-00.cise.ufl.edu")
        self.assertEqual(peers[2].port, 6010)
        self.assertEqual([p.has_file for p in peers], [True, False, False])


class TestBitfield(unittest.TestCase):
    # piece 0 should be the top bit of byte 0, piece 8 the top bit of byte 1
    def test_bit_order(self):
        bf = Bitfield(10)
        bf.set(0)
        self.assertEqual(bf.to_bytes(), b"\x80\x00")
        bf.set(9)
        self.assertEqual(bf.to_bytes(), b"\x80\x40")
        bf.set(7)
        self.assertEqual(bf.to_bytes(), b"\x81\x40")

    # 10 pieces = 2 bytes, but only the first 2 bits of byte 2 are real pieces
    def test_full_leaves_spare_bits_zero(self):
        self.assertEqual(Bitfield(10, full=True).to_bytes(), b"\xff\xc0")
        self.assertEqual(Bitfield(8, full=True).to_bytes(), b"\xff")

    # turning it into bytes and back should give the same pieces
    def test_bytes_roundtrip(self):
        bf = Bitfield(306)
        for i in (0, 5, 100, 305):
            bf.set(i)
        back = Bitfield.from_bytes(bf.to_bytes(), 306)
        self.assertEqual([i for i in range(306) if back.has(i)], [0, 5, 100, 305])

    # we have 0 and 2, they have 0 1 3, so we're missing 1 and 3 from them
    def test_missing_from(self):
        mine = Bitfield(5)
        mine.set(0)
        mine.set(2)
        theirs = Bitfield(5)
        for i in (0, 1, 3):
            theirs.set(i)
        self.assertEqual(mine.missing_from(theirs), [1, 3])


# shared setup for the PieceStore tests below
class PieceStoreTestCase(unittest.TestCase):
    # small fake file: 25 bytes split into 10 byte pieces, so pieces 0 and 1
    # are 10 bytes and piece 2 is only 5
    CONTENT = bytes(range(25))

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.common = CommonConfig(2, 5, 15, "thefile.dat", 25, 10)

    def tearDown(self):
        self._tmp.cleanup()

    # the real bytes for piece `index` of CONTENT
    def piece(self, index):
        start = index * self.common.piece_size
        return self.CONTENT[start:start + self.common.piece_length(index)]

    # a peer that starts with nothing
    def empty_store(self, **kwargs):
        return PieceStore(1002, self.common, False, self.dir, **kwargs)

    # a neighbor bitfield that has every piece
    def all_pieces(self):
        return Bitfield(self.common.num_pieces, full=True)


class TestPieceStorage(PieceStoreTestCase):
    def test_write_then_read(self):
        store = self.empty_store()
        self.assertEqual(store.write_piece(2, self.piece(2)), (1, False))
        self.assertTrue(store.has_piece(2))
        self.assertFalse(store.has_piece(0))
        self.assertEqual(store.read_piece(2), self.piece(2))

    # getting the same piece twice shouldn't count it twice
    def test_duplicate_returns_none(self):
        store = self.empty_store()
        store.write_piece(0, self.piece(0))
        self.assertIsNone(store.write_piece(0, self.piece(0)))
        self.assertEqual(store.piece_count(), 1)

    def test_wrong_size_rejected(self):
        store = self.empty_store()
        with self.assertRaises(ValueError):
            store.write_piece(0, b"x" * 5)   # piece 0 is supposed to be 10 bytes
        with self.assertRaises(ValueError):
            store.write_piece(2, b"x" * 10)  # last piece is supposed to be 5
        self.assertEqual(store.piece_count(), 0)

    # pieces come in random order, the file should still come out exactly right
    def test_assembles_whole_file(self):
        store = self.empty_store()
        results = [store.write_piece(i, self.piece(i)) for i in (2, 0, 1)]
        self.assertEqual(results, [(1, False), (2, False), (3, True)])
        self.assertTrue(store.is_complete())
        with open(store.path, "rb") as f:
            self.assertEqual(f.read(), self.CONTENT)


class TestPickRequest(PieceStoreTestCase):
    def test_never_picks_a_piece_we_have(self):
        store = self.empty_store()
        store.write_piece(0, self.piece(0))
        store.write_piece(2, self.piece(2))
        self.assertEqual(store.pick_request(self.all_pieces()), 1)
        self.assertIsNone(store.pick_request(self.all_pieces()))

    # each piece only gets handed out once, then there's nothing left
    def test_same_piece_not_picked_twice(self):
        store = self.empty_store()
        picks = [store.pick_request(self.all_pieces()) for _ in range(3)]
        self.assertEqual(sorted(picks), [0, 1, 2])
        self.assertIsNone(store.pick_request(self.all_pieces()))

    # like when a neighbor chokes us before sending the piece
    def test_release_makes_piece_pickable_again(self):
        store = self.empty_store()
        theirs = Bitfield(3)
        theirs.set(1)
        self.assertEqual(store.pick_request(theirs), 1)
        self.assertIsNone(store.pick_request(theirs))
        store.release_request(1)
        self.assertEqual(store.pick_request(theirs), 1)

    # the spec wants random picks (not just lowest index first), so with a
    # bunch of different seeds we should see every piece get picked first at some point
    def test_selection_is_random(self):
        seen = set()
        for seed in range(30):
            store = PieceStore(1000 + seed, self.common, False, self.dir,
                               random_generator=random.Random(seed))
            seen.add(store.pick_request(self.all_pieces()))
        self.assertEqual(seen, {0, 1, 2})

    # a bunch of threads picking at the same time (like a bunch of neighbors
    # unchoking us at once) should never get the same piece
    def test_concurrent_picks_are_unique(self):
        common = CommonConfig(2, 5, 15, "big.dat", 2000, 10)  # 200 pieces
        store = PieceStore(1002, common, False, self.dir)
        theirs = Bitfield(common.num_pieces, full=True)
        picks = []
        picks_lock = threading.Lock()

        def worker():
            while True:
                index = store.pick_request(theirs)
                if index is None:
                    return
                with picks_lock:
                    picks.append(index)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(picks), list(range(common.num_pieces)))


if __name__ == "__main__":
    unittest.main()
