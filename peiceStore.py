# Handles the actual file on disk for one peer (peer_<id>/<FileName>), our
# own bitfield, and which pieces we've already asked neighbors for.
import os
import random
import threading

from bitfield import Bitfield


class PieceStore:
    # random_generator is only there for tests, so they can pass a seeded
    # Random and get the same picks every time. Normally it's None.
    def __init__(self, peer_id, common, has_file, base_dir=".",
                 random_generator=None):
        self.common = common
        self.dir = os.path.join(base_dir, f"peer_{peer_id}")
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, common.file_name)
        self._lock = threading.Lock()
        self._random = random_generator or random.Random()
        # Pieces we asked someone for but haven't gotten yet. A piece comes
        # out of here when it shows up (write_piece) or when we give up on
        # it (release_request).
        self._requested = set()

        # If we're supposed to start with the file, it had better actually be
        # there and be the right size. Other wise make an empty file that's
        # already the full size, so each piece can just get written into its
        # spot as it comes in.
        if has_file:
            if (not os.path.isfile(self.path)
                    or os.path.getsize(self.path) != common.file_size):
                raise FileNotFoundError(
                    f"{self.path} must exist with {common.file_size} bytes "
                    f"before starting peer {peer_id}")
            self.bitfield = Bitfield(common.num_pieces, full=True)
        else:
            with open(self.path, "wb") as f:
                f.truncate(common.file_size)  # makes the file full size, all zeros
            self.bitfield = Bitfield(common.num_pieces)

    # ----- picking pieces + keeping track of requests (peerProcess.py uses these) -----

    # Picks a random piece to ask this neighbor for. It has to be one they
    # have, we don't, and we haven't already asked somebody else for.
    # Returns None if there isn't one.
    #
    # The check and the add are inside the same lock on purpose. Otherwise
    # two neighbor threads could both grab the same piece at the same time.
    def pick_request(self, their_bitfield):
        with self._lock:
            candidates = [i for i in self.bitfield.missing_from(their_bitfield)
                          if i not in self._requested]
            if not candidates:
                return None
            index = self._random.choice(candidates)
            self._requested.add(index)
            return index

    # We're not getting this piece after all (the neighbor choked us or
    # disconnected first), so take it off the list so someone else can be
    # asked for it. If it wasn't on the list, nothing happens.
    def release_request(self, index):
        with self._lock:
            self._requested.discard(index)

    # do we have the whole file yet?
    def is_complete(self):
        with self._lock:
            return self.bitfield.is_complete()

    # ----- small helpers used by peerProcess.py -----

    # how many pieces we have (used to decide whether to send a bitfield)
    def piece_count(self):
        with self._lock:
            return self.bitfield.count()

    # our bitfield as bytes, ready to put in a BITFIELD message
    def bitfield_bytes(self):
        with self._lock:
            return self.bitfield.to_bytes()

    def has_piece(self, index):
        with self._lock:
            return self.bitfield.has(index)

    # every piece starts at index * piece_size in the file, so jump there and
    # read that piece's length
    def read_piece(self, index):
        with self._lock:
            if not self.bitfield.has(index):
                raise KeyError(f"piece {index} not available")
            with open(self.path, "rb") as f:
                f.seek(index * self.common.piece_size)
                return f.read(self.common.piece_length(index))

    # Saves a piece into its spot in the file. Gives back (how many pieces we
    # have now, whether the file is done), or None if we already had it.
    def write_piece(self, index, data):
        if len(data) != self.common.piece_length(index):
            raise ValueError(f"piece {index} is {len(data)} bytes, "
                             f"expected {self.common.piece_length(index)}")
        with self._lock:
            # new or duplicate, either way we're not waiting on it anymore
            self._requested.discard(index)
            if self.bitfield.has(index):
                return None
            with open(self.path, "r+b") as f:
                f.seek(index * self.common.piece_size)
                f.write(data)
            self.bitfield.set(index)
            return self.bitfield.count(), self.bitfield.is_complete()
