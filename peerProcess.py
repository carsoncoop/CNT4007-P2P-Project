# peerProcess.py
#
# This is the program you actually run. Each peer in the network is one copy
# of this program.
#
# How to run it:
#   python3 peerProcess.py <peerID>
#
#   - Run it from the folder that has Common.cfg and PeerInfo.cfg.
#   - Peers marked with a 1 in PeerInfo.cfg (they start with the file) need
#     the file at peer_<peerID>/<FileName> before starting.
#   - Start the peers in the same order they appear in PeerInfo.cfg.
#   - Each peer writes its log to log_peer_<peerID>.log in the same folder.
#
# How the pieces fit together:
#
#   ConnectionManager (network.py)  - sets up sockets and handshakes
#   PieceStore (peiceStore.py)      - reads and writes pieces on disk
#   ChokeManager (chokeManager.py)  - decides who we upload to, on a timer
#   PeerLogger (peerLogger.py)      - writes the required log lines
#   NeighborState (neighborstate.py) - what we know about each neighbor
#   TerminationTracker (termination.py) - knows when everyone is done
#
# NOTE: some of these calls go to TODO stubs that Carson and Eddy still need
# to fill in (marked "TODO (Carson)" / "TODO (Eddy)" in peiceStore.py,
# neighborstate.py and termination.py). Until then this program won't run.
#
# The Peer class below connects all of them. It's the "listener" for the
# network code, so every message from every neighbor ends up in
# Peer.on_message(), which decides what to do with it.
#
# A typical download, from our side, looks like this:
#   1. We connect to a neighbor and send our bitfield (if we have pieces).
#   2. They send their bitfield. If they have something we don't, we send
#      INTERESTED.
#   3. At some point their ChokeManager picks us and sends UNCHOKE.
#   4. We send REQUEST for a random piece they have and we don't.
#   5. They send the PIECE. We save it, tell everyone with HAVE, and send
#      another REQUEST. This repeats until they choke us or we have
#      everything we can get from them.
#   6. Once every peer has the whole file, everybody shuts down.
import sys
import threading

from config import load_common, load_peers, get_peer
from listener import MessageListener
from message import (Message, CHOKE, UNCHOKE, INTERESTED, NOT_INTERESTED,
                     HAVE, BITFIELD, REQUEST, PIECE)
from bitfield import Bitfield
from peiceStore import PieceStore
from peerLogger import PeerLogger
from chokeManager import ChokeManager
from neighborstate import NeighborState
from termination import TerminationTracker
from network import ConnectionManager


# One running peer.
#
# Threads to keep in mind: each neighbor has its own reader thread, and that
# thread is the one that calls on_message() for messages from that neighbor.
# So all messages from neighbor X are handled one at a time, in order, on X's
# thread. Messages from different neighbors can be handled at the same time,
# which is why the shared parts (the neighbor table, the piece store, the
# termination tracker) all use locks.
class Peer(MessageListener):
    # peer_id: our own ID
    # common: the settings from Common.cfg
    # peers: everyone from PeerInfo.cfg, in file order
    # base_dir: where peer_<id>/ and the log file go ("." = current folder)
    def __init__(self, peer_id, common, peers, base_dir="."):
        me = get_peer(peers, peer_id)
        self.peer_id = peer_id
        self.common = common
        # opens (or creates) peer_<id>/<FileName> and our own bitfield
        self.store = PieceStore(peer_id, common, me.has_file, base_dir)
        self.logger = PeerLogger(peer_id, folder=base_dir)
        # starts out waiting on every peer, including us
        self.termination = TerminationTracker([p.peer_id for p in peers])

        # peer_id -> NeighborState, for every neighbor we're connected to
        self.neighbors = {}
        # reader threads add and remove neighbors at the same time, and the
        # ChokeManager timers read the table, so it needs a lock
        self.neighbors_lock = threading.Lock()

        # The ChokeManager doesn't know about sockets or messages. It just asks
        # us for the neighbor list and calls choke()/unchoke() on them.
        self.choker = ChokeManager(
            common.num_preferred_neighbors, common.unchoking_interval,
            common.optimistic_unchoking_interval,
            get_connected_neighbors=self.get_neighbors,
            we_have_whole_file=self.store.is_complete,
            logger=self.logger)
        # we pass self as the listener, so all network events come to us
        self.network = ConnectionManager(peer_id, peers, self)

        # if we started with the file, we're already done downloading
        if self.store.is_complete():
            self.termination.mark_complete(peer_id)

    # A copy of the neighbor list. The ChokeManager calls this on every timer
    # tick, so it has to be a copy that won't change while it's being used.
    def get_neighbors(self):
        with self.neighbors_lock:
            return list(self.neighbors.values())

    # Look up one neighbor, or None if they aren't connected.
    def get_neighbor(self, neighbor_id):
        with self.neighbors_lock:
            return self.neighbors.get(neighbor_id)

    # Run the peer until every peer has the whole file.
    def run(self):
        try:
            # connect to the earlier peers and start accepting the later ones
            self.network.start()
            # start the preferred and optimistic unchoke timers
            self.choker.start_timers()
            # The main thread has nothing else to do, so it just waits here.
            # The real work happens on the reader threads. all_done gets set
            # by the TerminationTracker once every peer is finished.
            self.termination.all_done.wait()
        finally:
            # "finally" means we clean up even if something above crashed
            self.shutdown()

    # Stop everything, in an order that doesn't leave anything half-running.
    def shutdown(self):
        # stop the timers first so they don't try to choke/unchoke closed connections
        self.choker.stop_timers()
        # close every connection (this waits for the last messages to go out)
        self.network.shutdown()
        self.logger.close()

    # ----- network events -----

    # Called once for each new neighbor, after the handshake and before any
    # message from them is read.
    def on_connected(self, conn):
        n = NeighborState(conn.neighbor_id, conn, self.common.num_pieces)
        with self.neighbors_lock:
            self.neighbors[conn.neighbor_id] = n
        # the spec has a different log line depending on who connected to whom
        if conn.outgoing:
            self.logger.log_made_connection_to(conn.neighbor_id)
        else:
            self.logger.log_got_connection_from(conn.neighbor_id)
        # The first message after the handshake is our bitfield. The spec says
        # a peer with no pieces can skip it, so we only send it if we have some.
        if self.store.piece_count() > 0:
            conn.send(Message.bitfield(self.store.bitfield_bytes()))

    # Called once when a neighbor's connection ends, for any reason.
    def on_disconnected(self, neighbor_id):
        with self.neighbors_lock:
            n = self.neighbors.pop(neighbor_id, None)
        # If we were waiting on a piece from them, it's never coming. Release
        # it so we can request it from someone else.
        if n is not None and n.pending_request is not None:
            self.store.release_request(n.pending_request)

    # Called for every message from every neighbor. Figures out the type and
    # sends it to the right handler.
    def on_message(self, neighbor_id, msg):
        n = self.get_neighbor(neighbor_id)
        if n is None:
            # they already disconnected, so ignore it
            return
        if msg.type == CHOKE:
            self.on_choke(n)
        elif msg.type == UNCHOKE:
            self.on_unchoke(n)
        elif msg.type == INTERESTED:
            # They want something we have. The ChokeManager checks this (through
            # wants_our_pieces) to decide who to unchoke.
            self.logger.log_received_interested(n.peer_id)
            n.set_they_are_interested(True)
        elif msg.type == NOT_INTERESTED:
            self.logger.log_received_not_interested(n.peer_id)
            n.set_they_are_interested(False)
        elif msg.type == HAVE:
            self.on_have(n, msg)
        elif msg.type == BITFIELD:
            self.on_bitfield(n, msg)
        elif msg.type == REQUEST:
            self.on_request(n, msg)
        elif msg.type == PIECE:
            self.on_piece(n, msg)

    # ----- one handler per message type -----

    # They choked us, so they won't send us any more pieces for now.
    def on_choke(self, n):
        self.logger.log_choked_by(n.peer_id)
        n.they_are_choking_us = True
        # The spec says a peer that chokes us won't answer requests we already
        # sent. Release that piece so we can ask someone else for it.
        if n.pending_request is not None:
            self.store.release_request(n.pending_request)
            n.pending_request = None

    # They unchoked us, so we can start requesting pieces from them.
    def on_unchoke(self, n):
        self.logger.log_unchoked_by(n.peer_id)
        n.they_are_choking_us = False
        self.request_next(n)

    # They got a new piece. Mark it in our copy of their bitfield.
    def on_have(self, n, msg):
        index = msg.piece_index()
        self.logger.log_received_have(n.peer_id, index)
        n.bitfield.set(index)
        self.their_pieces_changed(n)

    # Their full list of pieces, sent right after the handshake.
    def on_bitfield(self, n, msg):
        # from_bytes also checks the size and that the extra bits at the end are 0
        n.bitfield = Bitfield.from_bytes(msg.bitfield_bits(), self.common.num_pieces)
        self.their_pieces_changed(n)

    # They want a piece from us.
    def on_request(self, n, msg):
        index = msg.piece_index()
        # Only answer if we've unchoked them. A request can show up just after
        # we choked them (it was already on its way), and the spec says to
        # ignore those. Also skip pieces we don't have, just in case.
        if n.we_choke_them or not self.store.has_piece(index):
            return
        n.connection.send(Message.piece(index, self.store.read_piece(index)))

    # They sent us a piece we asked for.
    def on_piece(self, n, msg):
        index = msg.piece_index()
        data = msg.piece_data()
        # we're not waiting on this piece anymore
        if n.pending_request == index:
            n.pending_request = None
        # count the bytes toward their download rate. The ChokeManager uses
        # this to pick the neighbors who sent us the most.
        n.add_bytes_received(len(data))
        # write_piece returns None if we already had this piece (it's a duplicate)
        result = self.store.write_piece(index, data)
        if result is not None:
            count, complete = result
            self.logger.log_downloaded_piece(index, n.peer_id, count)
            # tell every neighbor we have this piece now
            self.network.broadcast(Message.have(index))
            if complete:
                self.logger.log_downloaded_whole_file()
                self.termination.mark_complete(self.peer_id)
            # Getting this piece might mean some neighbors have nothing left
            # that we need, so check each one. update_our_interest() should only
            # send NOT_INTERESTED if our interest actually changed.
            for other in self.get_neighbors():
                other.update_our_interest(self.store.bitfield)
        # keep going: ask this neighbor for another piece
        self.request_next(n)

    # ----- helpers -----

    # Called after a HAVE or BITFIELD tells us about pieces they have.
    def their_pieces_changed(self, n):
        # send INTERESTED or NOT_INTERESTED if it changed
        n.update_our_interest(self.store.bitfield)
        # if they have every piece, they're done
        if n.bitfield.is_complete():
            self.termination.mark_complete(n.peer_id)
        # If they already unchoked us but had nothing we needed, we may have
        # stopped requesting. Now that they have a new piece, try again.
        self.request_next(n)

    # Ask neighbor n for one random piece.
    #
    # We only keep one request open per neighbor at a time. We don't send one
    # if they're choking us, or if we're still waiting on the last one.
    # pick_request() makes sure we don't ask two neighbors for the same piece.
    def request_next(self, n):
        if n.they_are_choking_us or n.pending_request is not None:
            return
        index = self.store.pick_request(n.bitfield)
        # None means they have nothing we need that isn't already requested
        if index is not None:
            n.pending_request = index
            n.connection.send(Message.request(index))


def main():
    # sys.argv[0] is the script name, sys.argv[1] should be the peer ID
    if len(sys.argv) != 2:
        print("usage: python3 peerProcess.py <peerID>")
        return 1
    peer_id = int(sys.argv[1])
    peer = Peer(peer_id, load_common("Common.cfg"), load_peers("PeerInfo.cfg"))
    # blocks until every peer has the whole file
    peer.run()
    return 0


# only run main() when this file is run directly, not when it's imported (like in tests)
if __name__ == "__main__":
    sys.exit(main())
