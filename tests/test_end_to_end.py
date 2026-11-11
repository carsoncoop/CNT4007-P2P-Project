# test_end_to_end.py
#
# The full test: runs the real program the same way you would by hand, and
# checks that the file actually gets shared.
#
# What it does:
#   1. Makes a temporary folder with its own Common.cfg and PeerInfo.cfg.
#   2. Creates a random file and gives it to the peers marked with a 1.
#   3. Starts a separate peerProcess.py for each peer, in file order.
#   4. Waits for every process to exit on its own (this tests termination).
#   5. Checks every peer ended up with a byte-for-byte copy of the file, and
#      that each log has the right connection lines.
#
# The temporary folder is deleted at the end, so nothing is left behind.
#
# Run from the repo root:
#   python3 -m unittest tests/test_end_to_end.py -v
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PEER_PROCESS = os.path.join(REPO, "peerProcess.py")

FILE_NAME = "thefile.dat"
# 100003 isn't a multiple of 1000, so the file is 101 pieces and the last one
# is only 3 bytes. That checks we handle the smaller last piece correctly.
FILE_SIZE = 100003
PIECE_SIZE = 1000
# (peer_id, has_file). Two peers start with the file, and one of them is in
# the middle of the list. That checks that a peer with the file doesn't have
# to be first.
PEERS = [(1001, True), (1002, False), (1003, False), (1004, True), (1005, False)]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# This needs the TODO stubs in peiceStore.py, neighborstate.py and
# termination.py (Carson's and Eddy's tasks). Remove this skip once those
# are filled in.
@unittest.skip("waiting on Carson's and Eddy's TODO stubs")
class TestEndToEnd(unittest.TestCase):
    def test_every_peer_gets_the_whole_file(self):
        with tempfile.TemporaryDirectory() as work:
            content = os.urandom(FILE_SIZE)

            # Write the config files. The intervals are short (1 and 2
            # seconds) so the test finishes quickly.
            with open(os.path.join(work, "Common.cfg"), "w") as f:
                f.write("NumberOfPreferredNeighbors 2\n")
                f.write("UnchokingInterval 1\n")
                f.write("OptimisticUnchokingInterval 2\n")
                f.write(f"FileName {FILE_NAME}\n")
                f.write(f"FileSize {FILE_SIZE}\n")
                f.write(f"PieceSize {PIECE_SIZE}\n")
            with open(os.path.join(work, "PeerInfo.cfg"), "w") as f:
                for peer_id, has_file in PEERS:
                    f.write(f"{peer_id} 127.0.0.1 {free_port()} {int(has_file)}\n")

            # give the file to the peers that start with it
            for peer_id, has_file in PEERS:
                if has_file:
                    os.makedirs(os.path.join(work, f"peer_{peer_id}"))
                    with open(os.path.join(work, f"peer_{peer_id}", FILE_NAME), "wb") as f:
                        f.write(content)

            # Start the peers in order and wait for all of them to exit.
            # Each one is a real separate process, just like running
            # "python3 peerProcess.py 1001" in its own terminal. The short
            # sleep gives each peer time to start listening before the next
            # one tries to connect to it.
            procs = []
            try:
                for peer_id, has_file in PEERS:
                    proc = subprocess.Popen([sys.executable, PEER_PROCESS, str(peer_id)], cwd=work,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True)
                    procs.append(proc)
                    time.sleep(0.3)
                for i in range(len(PEERS)):
                    out, err = procs[i].communicate(timeout=60)
                    self.assertEqual(procs[i].returncode, 0, f"peer {PEERS[i][0]} output:\n{out}")
            finally:
                # if the test failed or timed out, don't leave peers running
                for proc in procs:
                    if proc.poll() is None:
                        proc.kill()

            # every peer should have the same file and the right log lines
            for peer_id, has_file in PEERS:
                with open(os.path.join(work, f"peer_{peer_id}", FILE_NAME), "rb") as f:
                    self.assertEqual(f.read(), content, f"peer {peer_id} file is different")
                with open(os.path.join(work, f"log_peer_{peer_id}.log")) as f:
                    log = f.read()
                connections = log.count("makes a connection to") + log.count("is connected from")
                self.assertEqual(connections, len(PEERS) - 1, log)
                if not has_file:
                    self.assertIn("has downloaded the complete file.", log)


if __name__ == "__main__":
    unittest.main()
