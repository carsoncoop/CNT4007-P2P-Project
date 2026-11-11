# termination.py
#
# TODO (Eddy): detecting that all peers are done.
#
# This is on Eddy's task list. peerProcess.py already uses the class below
# with these names, so fill it in (or update peerProcess.py to match).
#
# What peerProcess.py expects:
#   tracker = TerminationTracker(all_peer_ids)   every ID in PeerInfo.cfg, including ours
#   tracker.mark_complete(peer_id)               called when a peer (or we) has every piece
#   tracker.all_done                             a threading.Event that gets set once
#                                                every peer is complete; Peer.run()
#                                                waits on it, then shuts down
import threading


class TerminationTracker:
    def __init__(self, all_peer_ids):
        self.all_done = threading.Event()
        raise NotImplementedError("TODO (Eddy): termination tracking")

    def mark_complete(self, peer_id):
        raise NotImplementedError("TODO (Eddy): termination tracking")
