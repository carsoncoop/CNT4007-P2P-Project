# test_loopback.py
#
# Tests for the network layer: handshakes, message encoding, socket reads,
# NeighborConnection, and ConnectionManager. None of these use the actual
# peer logic (choking, piece picking, etc.); that's covered by
# test_end_to_end.py.
#
# Run from the repo root:
#   python3 -m unittest discover -s tests -v
#
# The test groups go from simplest to most complex:
#   TestHandshake          - just bytes, no sockets
#   TestMessageEncoding    - just bytes, no sockets
#   TestSocketLoopback     - two real sockets on this machine, no threads
#   TestNeighborConnection - real sockets plus the reader/writer threads
#   TestConnectionManager  - several "peers" connecting to each other
#
# If something breaks, the first failing group usually tells you which layer
# the bug is in.
import os
import socket
import struct
import sys
import threading
import time
import unittest

# so we can import the project files from the tests folder
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import message as m
from message import Message
from handshake import build_handshake, parse_handshake, HANDSHAKE_LENGTH
from listener import MessageListener
from network import recv_exact, read_message, NeighborConnection, ConnectionManager
from config import PeerInfo


# one of each of the 8 message types
def all_messages():
    return [
        Message.choke(),
        Message.unchoke(),
        Message.interested(),
        Message.not_interested(),
        Message.have(7),
        Message.bitfield(b"\xf0\x80"),
        Message.request(3),
        Message.piece(3, b"hello piece data"),
    ]


# Ask the OS for a port nobody is using. Binding to port 0 makes the OS pick
# a free one; we read which one it picked and close the socket. Using random
# free ports means the tests don't fail if some fixed port is already taken.
def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# Keep checking until check() is true or we run out of time.
#
# The network code runs on background threads, so after we send something
# the result doesn't show up instantly. Instead of guessing with a fixed
# sleep (too short = random failures, too long = slow tests), we check every
# 10ms and move on as soon as it's true.
def wait_until(check, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return True
        time.sleep(0.01)
    return check()


# A fake peer for test_wrong_peer_answering_is_rejected. It accepts one
# connection on server, reads the handshake, and answers with a handshake
# from peer 9999 (the wrong ID). Runs on its own thread during the test.
def answer_with_wrong_id(server):
    sock, addr = server.accept()
    recv_exact(sock, HANDSHAKE_LENGTH)
    sock.sendall(build_handshake(9999))
    # wait for the other side to hang up before closing
    sock.recv(1)
    sock.close()


# A fake listener that just saves everything it gets, so the tests can look
# at what arrived. It stands in for the Peer class, which lets us test the
# network code without any of the peer logic.
class SavingListener(MessageListener):
    def __init__(self):
        self.messages = []
        self.connected = []
        self.disconnected = []

    def on_message(self, neighbor_id, msg):
        self.messages.append((neighbor_id, msg))

    def on_connected(self, conn):
        self.connected.append(conn)

    def on_disconnected(self, neighbor_id):
        self.disconnected.append(neighbor_id)


# The handshake is 32 bytes: 18 byte header, 10 zero bytes, 4 byte peer ID.
class TestHandshake(unittest.TestCase):
    # build a handshake, check every part of it, and parse it back
    def test_length_and_roundtrip(self):
        hs = build_handshake(1001)
        self.assertEqual(len(hs), HANDSHAKE_LENGTH)
        self.assertEqual(hs[:18], b"P2PFILESHARINGPROJ")
        self.assertEqual(hs[18:28], b"\x00" * 10)
        self.assertEqual(parse_handshake(hs), 1001)

    # change the first letter of the header, it should be rejected
    def test_bad_header_rejected(self):
        bad = b"X" + build_handshake(1001)[1:]
        with self.assertRaises(ValueError):
            parse_handshake(bad)

    # put a 1 in the middle of the zero bytes, it should be rejected
    def test_nonzero_bits_rejected(self):
        hs = bytearray(build_handshake(1001))
        hs[20] = 1
        with self.assertRaises(ValueError):
            parse_handshake(bytes(hs))

    # cut off the last byte, it should be rejected
    def test_wrong_length_rejected(self):
        with self.assertRaises(ValueError):
            parse_handshake(build_handshake(1001)[:-1])


# Messages on the wire are [4 byte length][1 byte type][payload].
class TestMessageEncoding(unittest.TestCase):
    # Encode each of the 8 types, check the bytes by hand, then decode them and
    # make sure we get the same message back.
    def test_every_type_roundtrips(self):
        msgs = all_messages()
        # make sure the list really has all 8 types, once each
        types = sorted([msg.type for msg in msgs])
        self.assertEqual(types, [0, 1, 2, 3, 4, 5, 6, 7])
        for msg in msgs:
            data = msg.encode()
            # length field = 1 type byte + the payload
            length = struct.unpack(">I", data[:4])[0]
            self.assertEqual(length, 1 + len(msg.payload))
            self.assertEqual(len(data), 4 + length)
            self.assertEqual(data[4], msg.type)
            self.assertEqual(data[5:], msg.payload)
            self.assertEqual(Message.decode(data), msg)

    # the helper methods should pull the right values back out of the payload
    def test_payload_accessors(self):
        self.assertEqual(Message.have(7).piece_index(), 7)
        self.assertEqual(Message.request(3).piece_index(), 3)
        p = Message.piece(5, b"abc")
        self.assertEqual(p.piece_index(), 5)
        self.assertEqual(p.piece_data(), b"abc")
        self.assertEqual(Message.bitfield(b"\xf0").bitfield_bits(), b"\xf0")

    # Each of these is broken in a different way and should raise ValueError.
    # This is what protects us from a neighbor sending garbage.
    def test_bad_messages_rejected(self):
        with self.assertRaises(ValueError):
            Message(8)                              # unknown type
        with self.assertRaises(ValueError):
            Message(m.CHOKE, b"x")                  # choke has no payload
        with self.assertRaises(ValueError):
            Message(m.HAVE, b"\x00\x00")            # have needs 4 bytes
        with self.assertRaises(ValueError):
            Message(m.PIECE, b"\x00")               # piece needs the index
        with self.assertRaises(ValueError):
            Message.decode(b"\x00\x00\x00\x05\x00") # length field is wrong
        with self.assertRaises(ValueError):
            Message.decode(b"\x00\x00")             # too short


# Two real TCP sockets on this machine ("loopback"), with no peer logic and no
# threads. This is the closest thing to the real network without starting
# whole peers.
class TestSocketLoopback(unittest.TestCase):
    # setUp runs before every test in this class. It makes a server socket,
    # connects a client to it, and accepts the connection, so each test
    # starts with a fresh connected pair: self.client <-> self.accepted.
    def setUp(self):
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen()
        port = self.server.getsockname()[1]
        self.client = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.accepted, addr = self.server.accept()
        self.accepted.settimeout(5)

    # tearDown runs after every test, even ones that fail, so sockets don't leak
    def tearDown(self):
        self.client.close()
        self.accepted.close()
        self.server.close()

    # The main loopback test: handshake both ways like real peers do, then
    # send every message type in both directions.
    def test_send_all_types_over_real_sockets(self):
        # handshake both ways
        self.client.sendall(build_handshake(1002))
        self.assertEqual(parse_handshake(recv_exact(self.accepted, HANDSHAKE_LENGTH)), 1002)
        self.accepted.sendall(build_handshake(1001))
        self.assertEqual(parse_handshake(recv_exact(self.client, HANDSHAKE_LENGTH)), 1001)

        # send every message type both ways
        for msg in all_messages():
            self.client.sendall(msg.encode())
            self.assertEqual(read_message(self.accepted), msg)
            self.accepted.sendall(msg.encode())
            self.assertEqual(read_message(self.client), msg)

    # On a real network a big message often arrives in several chunks. This
    # fakes that by sending a piece message 7 bytes at a time. recv_exact
    # should glue the chunks back together into one whole message.
    def test_recv_exact_handles_split_writes(self):
        data = Message.piece(1, b"x" * 1000).encode()
        # send it 7 bytes at a time
        for i in range(0, len(data), 7):
            self.client.sendall(data[i:i + 7])
        self.assertEqual(read_message(self.accepted), Message.decode(data))

    # The other side sends 2 bytes and then hangs up. We asked for 4, so
    # recv_exact should raise an error instead of waiting forever.
    def test_recv_exact_raises_when_peer_closes(self):
        self.client.sendall(b"\x00\x00")
        self.client.close()
        with self.assertRaises(ConnectionError):
            recv_exact(self.accepted, 4)


# Tests for the reader and writer threads. socket.socketpair() gives two
# sockets already connected to each other, so there's no server to set up.
class TestNeighborConnection(unittest.TestCase):
    # Send all 8 types through the threads and make sure they arrive in
    # order. Then close one side and make sure BOTH sides notice.
    def test_threads_deliver_all_types_and_close(self):
        a, b = socket.socketpair()
        la = SavingListener()
        lb = SavingListener()
        ca = NeighborConnection(2, a, la)
        cb = NeighborConnection(1, b, lb)
        ca.start()
        cb.start()

        for msg in all_messages():
            ca.send(msg)
        self.assertTrue(wait_until(lambda: len(lb.messages) == 8))
        self.assertEqual(lb.messages, [(1, msg) for msg in all_messages()])

        # closing one side should disconnect both
        ca.close()
        self.assertTrue(wait_until(lambda: la.disconnected == [2]))
        self.assertTrue(wait_until(lambda: lb.disconnected == [1]))

    # Queue 8 messages and close right away. All 8 should still arrive. This
    # matters at shutdown, when a peer's last HAVE messages have to get out
    # before the program exits.
    def test_queued_messages_still_sent_after_close(self):
        a, b = socket.socketpair()
        lb = SavingListener()
        ca = NeighborConnection(2, a, SavingListener())
        cb = NeighborConnection(1, b, lb)
        cb.start()
        ca.start()
        for msg in all_messages():
            ca.send(msg)
        ca.close()
        self.assertTrue(wait_until(lambda: lb.disconnected == [1]))
        self.assertEqual(len(lb.messages), 8)

    # If a neighbor sends something that isn't a valid message, we should drop
    # the connection cleanly instead of crashing the reader thread.
    def test_garbage_bytes_drop_connection(self):
        a, b = socket.socketpair()
        lb = SavingListener()
        NeighborConnection(1, b, lb).start()
        # message type 9 doesn't exist
        a.sendall(b"\x00\x00\x00\x01\x09")
        self.assertTrue(wait_until(lambda: lb.disconnected == [1]))
        a.close()


# Tests for connection setup between several peers, all in this one process.
class TestConnectionManager(unittest.TestCase):
    # Three peers start in file order. Each should end up connected to the
    # other two: connecting out to earlier peers and accepting later ones.
    # Then we check messages flow, and that shutdown closes everything.
    def test_three_peers_connect(self):
        peers = []
        for i in range(3):
            peers.append(PeerInfo(1001 + i, "127.0.0.1", free_port(), i == 0))
        listeners = {}
        managers = {}
        for p in peers:
            listeners[p.peer_id] = SavingListener()
            managers[p.peer_id] = ConnectionManager(p.peer_id, peers, listeners[p.peer_id],
                                                    retry_delay=0.1)
        try:
            # start them in file order like the real run
            for p in peers:
                managers[p.peer_id].start()

            for p in peers:
                listener = listeners[p.peer_id]
                self.assertTrue(wait_until(lambda: len(listener.connected) == 2))
                connected_ids = set(c.neighbor_id for c in listener.connected)
                others = set(q.peer_id for q in peers) - {p.peer_id}
                self.assertEqual(connected_ids, others)
                # we connect to earlier peers, later peers connect to us
                for c in listener.connected:
                    self.assertEqual(c.outgoing, c.neighbor_id < p.peer_id)

            # make sure messages go through
            managers[1003].get(1001).send(Message.have(4))
            self.assertTrue(wait_until(lambda: (1003, Message.have(4)) in listeners[1001].messages))
            managers[1001].broadcast(Message.interested())
            self.assertTrue(wait_until(lambda: (1001, Message.interested()) in listeners[1002].messages))
            self.assertTrue(wait_until(lambda: (1001, Message.interested()) in listeners[1003].messages))
        finally:
            for mgr in managers.values():
                mgr.shutdown()

        for p in peers:
            self.assertTrue(wait_until(lambda: len(listeners[p.peer_id].disconnected) == 2))
            self.assertEqual(managers[p.peer_id].all_connections(), [])

    # If an earlier peer isn't running, start() should give up after its
    # retries and raise an error instead of hanging forever.
    def test_missing_earlier_peer_raises(self):
        peers = [PeerInfo(1001, "127.0.0.1", free_port(), True),
                 PeerInfo(1002, "127.0.0.1", free_port(), False)]
        mgr = ConnectionManager(1002, peers, SavingListener(),
                                connect_retries=2, retry_delay=0.05)
        try:
            # 1001 was never started
            with self.assertRaises(ConnectionError):
                mgr.start()
        finally:
            mgr.shutdown()

    # When we connect OUT, the peer that answers has to be the one we meant to
    # reach. Here a fake "1001" answers with a handshake that says 9999, so
    # start() should fail and we should NOT end up with a connection.
    def test_wrong_peer_answering_is_rejected(self):
        fake_server = socket.socket()
        fake_server.bind(("127.0.0.1", 0))
        fake_server.listen()
        port = fake_server.getsockname()[1]
        thread = threading.Thread(target=answer_with_wrong_id, args=(fake_server,), daemon=True)
        thread.start()

        peers = [PeerInfo(1001, "127.0.0.1", port, True),
                 PeerInfo(1002, "127.0.0.1", free_port(), False)]
        listener = SavingListener()
        mgr = ConnectionManager(1002, peers, listener)
        try:
            with self.assertRaises(ValueError):
                mgr.start()
            self.assertEqual(listener.connected, [])
            self.assertEqual(mgr.all_connections(), [])
        finally:
            mgr.shutdown()
            thread.join(timeout=5)
            fake_server.close()

    # A connection from a peer ID that isn't in PeerInfo.cfg should be turned
    # away. (This test prints a "rejected incoming connection" line; that's expected.)
    def test_unknown_peer_rejected(self):
        peers = [PeerInfo(1001, "127.0.0.1", free_port(), True),
                 PeerInfo(1002, "127.0.0.1", free_port(), False)]
        listener = SavingListener()
        mgr = ConnectionManager(1001, peers, listener)
        mgr.start()
        try:
            # 9999 isn't in the peer list, so 1001 should hang up without replying
            s = socket.create_connection(("127.0.0.1", peers[0].port), timeout=5)
            s.sendall(build_handshake(9999))
            self.assertEqual(s.recv(1), b"")
            s.close()
            self.assertEqual(listener.connected, [])
        finally:
            mgr.shutdown()


if __name__ == "__main__":
    unittest.main()
