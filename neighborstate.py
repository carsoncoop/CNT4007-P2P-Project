# neighborstate.py
#
# Everything we keep track of about one neighbor: which pieces they have,
# who is choking whom, who is interested in whom, and how fast they’ve been
# sending to us.
#
# The Peer class (peerProcess.py) creates one NeighborState per connection
# and it's kept up to date as messages come in.

#
# The ChokeManager (chokeManager.py) also uses these objects. It needs these
# exact names, so don’t rename them without
# changing chokeManager.py too:
#   peer_id
#   take_bytes_received_this_interval()
#   wants_our_pieces()
#   we_are_choking_them()
#   choke()
#   unchoke()
#
# The interest tracking and download-rate counter parts are on Eddy's task
# list, so they're left as TODO stubs below.
import threading

from bitfield import Bitfield
from message import Message


class NeighborState:
    # peer_id: the neighbor's ID
    # connection: their NeighborConnection, used to send them messages
    # num_pieces: how many pieces the file has (so we can size their bitfield)
    def __init__(self, peer_id, connection, num_pieces):
        self.peer_id = peer_id
        self.connection = connection
        # Which pieces THEY have. It starts empty and gets filled in by their
        # BITFIELD message and then each HAVE message that follows.
        self.bitfield = Bitfield(num_pieces)
        # Two threads touch this object: the neighbor's reader thread and the
        # ChokeManager's timer threads. The lock keeps the choke flags
        # consistent between them.
        self.lock = threading.Lock()

        # Per the spec, every connection starts out choked in both directions.
        # they_are_choking_us: if True, we can't request pieces from them
        self.they_are_choking_us = True
        # we_choke_them: if True, we ignore their requests
        self.we_choke_them = True
        # The piece we asked them for and are still waiting on.
        # None means we aren't waiting on anything from them.
        self.pending_request = None

    # Used by the ChokeManager to see if we're already choking them.
    def we_are_choking_them(self):
        return self.we_choke_them

    # Choke them: stop sending them pieces. Sends a CHOKE message only if they
    # weren't already choked, so they never get the same message twice in a row.
    def choke(self):
        with self.lock:
            if self.we_choke_them:
                return
            self.we_choke_them = True
        # send outside the lock; send() just adds it to a queue
        self.connection.send(Message.choke())

    # Unchoke them: they can now request pieces from us. Same idea as choke().
    def unchoke(self):
        with self.lock:
            if not self.we_choke_them:
                return
            self.we_choke_them = False
        self.connection.send(Message.unchoke())

    # ----- TODO (Eddy): interested / not-interested tracking -----

    # Called when they send INTERESTED (True) or NOT_INTERESTED (False).
    def set_they_are_interested(self, interested):
        raise NotImplementedError("TODO (Eddy): interested tracking")

    # Used by the ChokeManager: True if they want pieces we have.
    def wants_our_pieces(self):
        raise NotImplementedError("TODO (Eddy): interested tracking")

    # Decide whether we want any of their pieces (compare self.bitfield with
    # our_bitfield, our own Bitfield object), and send them INTERESTED or
    # NOT_INTERESTED. Called after every HAVE, BITFIELD, and piece we
    # download, so it should only send a message when the answer changes.
    def update_our_interest(self, our_bitfield):
        raise NotImplementedError("TODO (Eddy): interested tracking")

    # ----- TODO (Eddy): download-rate counter -----

    # Called when a piece arrives from them, with the number of data bytes.
    def add_bytes_received(self, n):
        raise NotImplementedError("TODO (Eddy): download-rate counter")

    # Called by the ChokeManager each unchoking interval. Should return the
    # bytes they sent since the last call, and reset the count to 0.
    def take_bytes_received_this_interval(self):
        raise NotImplementedError("TODO (Eddy): download-rate counter")
