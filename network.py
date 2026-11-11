# network.py
#
# This file handles everything that has to do with sockets. The rest of the
# program never touches a socket directly, it only deals with Message objects.
#
# There are three main parts:
#
#   1. recv_exact() and read_message()
#      TCP is a stream, not a list of packets. One call to recv() can give back
#      half a message, or a message and a half. These two functions take care of
#      reading exactly the number of bytes we need.
#
#   2. NeighborConnection
#      Wraps one open socket to one neighbor. Each connection gets two threads:
#        - a reader thread that keeps reading messages and hands each one to
#          the listener (the Peer class in peerProcess.py)
#        - a writer thread that takes messages off a queue and sends them
#      Because of the queue, any thread can call send() without worrying about
#      two threads writing to the same socket at the same time.
#
#   3. ConnectionManager
#      Sets up all the connections at startup. The project spec says each peer
#      connects OUT to every peer listed before it in PeerInfo.cfg, and waits
#      for every peer listed after it to connect IN. Both sides swap a
#      handshake first so each one knows who is on the other end.
import socket
import threading
import queue
import struct
import time
import traceback

from message import Message
from listener import MessageListener
from handshake import build_handshake, parse_handshake, HANDSHAKE_LENGTH


# Read exactly n bytes from the socket.
#
# sock.recv(n) gives back AT MOST n bytes. It can give back fewer if the rest
# hasn't arrived yet, so we keep calling it and adding to a buffer until we
# have all n. If recv() returns an empty result, the other side closed the
# connection, so we raise an error instead of looping forever.
def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        # only ask for what we're still missing
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(f"Socket closed with {n - len(buf)} bytes left to read")
        buf.extend(chunk)
    return bytes(buf)


# Read one full message off the socket.
#
# Every message looks like this on the wire:
#   [4 byte length][1 byte type][payload]
# The length counts the type byte plus the payload, but NOT the 4 length bytes
# themselves. So we read the 4 byte length first, then the type, then
# (length - 1) bytes of payload.
#
# Creating the Message checks the type and payload size, so bad data raises a
# ValueError here instead of causing problems later.
def read_message(sock: socket.socket) -> Message:
    # "!I" means a 4 byte unsigned int in network (big-endian) byte order
    length = struct.unpack('!I', recv_exact(sock, 4))[0]
    # length always includes the type byte, so 0 can't be right
    if length < 1:
        raise ValueError("Message length can't be 0")
    # indexing a bytes object gives an int, which is the type number
    msg_type = recv_exact(sock, 1)[0]
    payload = recv_exact(sock, length - 1)
    return Message(msg_type, payload)


# Close a socket in a way that works on both Mac and Linux.
#
# On Linux, if one thread is stuck inside recv() or accept() and another
# thread calls close(), the stuck thread may never wake up. Calling shutdown()
# first forces it to return. Both calls are wrapped in try/except because the
# socket might already be closed, and that's fine.
def close_socket(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


# One open connection to one neighbor.
#
# How to use it:
#   conn = NeighborConnection(neighbor_id, sock, listener)
#   conn.start()        start the reader and writer threads
#   conn.send(msg)      queue a message (safe to call from any thread)
#   conn.close()        send what's queued, then shut everything down
#
# The listener gets these calls:
#   listener.on_message(neighbor_id, msg)   for every message that comes in
#   listener.on_disconnected(neighbor_id)   once, when the connection ends
class NeighborConnection:
    # outgoing is True if we connected to them, False if they connected to us.
    # The logger needs this to choose between "makes a connection to" and
    # "is connected from".
    def __init__(self, neighbor_id: int, sock: socket.socket, listener: MessageListener,
                 outgoing=False):
        self.neighbor_id = neighbor_id
        self.sock = sock
        self.listener = listener
        self.outgoing = outgoing
        self.is_closed = False
        self.started = False
        # Messages waiting to be sent. The writer thread is the only thing that
        # takes from this queue, so only one thread ever writes to the socket.
        self.writer_queue = queue.Queue()
        # protects is_closed and started, since several threads can call close()
        self.lock = threading.Lock()

    # Start the reader and writer threads. Can only be done once.
    def start(self) -> None:
        with self.lock:
            if self.is_closed:
                raise RuntimeError("Cannot start a closed connection")
            if self.started:
                raise RuntimeError("Connection already started")
            self.started = True
        # daemon=True means these threads won't keep the program alive by
        # themselves when the main thread exits
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self.reader_thread.start()
        self.writer_thread.start()

    # Queue a message to send. This returns right away; the writer thread does
    # the actual sending. Messages sent after close() are ignored.
    def send(self, msg: Message) -> None:
        if not self.is_closed:
            self.writer_queue.put(msg)

    # Close the connection. Safe to call more than once and from any thread.
    #
    # We don't close the socket right here. Instead we put None on the queue,
    # which tells the writer "you're done". The writer sends everything that
    # was queued before the None, and THEN closes the socket. That way the
    # last messages (like the final HAVE when a peer finishes) still go out.
    # Closing the socket also wakes up the reader thread, which then ends too.
    def close(self) -> None:
        with self.lock:
            if self.is_closed:
                return
            self.is_closed = True
        if self.started:
            self.writer_queue.put(None)
        else:
            # no writer thread exists, so close the socket ourselves
            close_socket(self.sock)

    # Wait for the writer thread to finish sending what was queued before
    # close(). Used at shutdown so the program doesn't exit too early.
    def join(self, timeout=None) -> None:
        if self.started:
            self.writer_thread.join(timeout)

    # Runs on the reader thread. Keeps reading messages until the connection
    # ends, and hands each one to the listener.
    def _reader_loop(self) -> None:
        try:
            while True:
                try:
                    msg = read_message(self.sock)
                except (OSError, ValueError):
                    # OSError: the socket was closed (by them or by us)
                    # ValueError: they sent bytes that aren't a valid message
                    # Either way this connection is done.
                    break
                # This call is outside the try above on purpose. If the
                # listener has a bug, we want to see the error below instead
                # of mistaking it for a normal disconnect.
                self.listener.on_message(self.neighbor_id, msg)
        except Exception:
            # a bug in on_message: print it so we can fix it, then drop the connection
            traceback.print_exc()
        finally:
            # this runs no matter how the loop ended
            self.close()
            self.listener.on_disconnected(self.neighbor_id)

    # Runs on the writer thread. Takes messages off the queue one at a time and
    # sends them. queue.get() waits until something is in the queue, so this
    # thread just sleeps when there's nothing to send.
    def _writer_loop(self) -> None:
        try:
            while True:
                msg = self.writer_queue.get()
                # None is the signal from close() that we're done
                if msg is None:
                    break
                # sendall keeps sending until every byte is out
                self.sock.sendall(msg.encode())
        except OSError:
            # the other side went away; nothing left to do but clean up
            pass
        finally:
            self.close()
            close_socket(self.sock)


# Sets up the connections to every other peer.
#
# Example: with peers 1001, 1002, 1003 in PeerInfo.cfg, peer 1002 will
#   - connect out to 1001 (listed before it)
#   - wait for 1003 to connect in (listed after it)
#
# On every new connection, both sides send a handshake. The side that
# connected out sends first, and checks that the reply came from the peer it
# meant to reach. The side that accepted reads the handshake first to find
# out who connected, then replies.
#
# ConnectionManager is also a MessageListener itself. Each NeighborConnection
# reports to the manager, which removes closed connections from its table and
# then passes the event on to the real listener (the Peer).
class ConnectionManager(MessageListener):
    # my_id: our own peer ID
    # peers: the list from config.load_peers(), in PeerInfo.cfg order
    # listener: who gets on_connected / on_message / on_disconnected
    # handshake_timeout: how long to wait for a handshake before giving up
    # connect_retries / retry_delay: how many times to try connecting to an
    #   earlier peer that isn't up yet, and how long to wait between tries
    def __init__(self, my_id: int, peers: list, listener: MessageListener,
                 handshake_timeout=10.0, connect_retries=5, retry_delay=1.0):
        ids = [p.peer_id for p in peers]
        if my_id not in ids:
            raise KeyError(f"peer {my_id} is not in the peer list")
        my_index = ids.index(my_id)
        self.my_id = my_id
        self.me = peers[my_index]
        # everyone before us in the file: we connect to them
        self.earlier_peers = peers[:my_index]
        # everyone after us: they connect to us. We keep their IDs so we can
        # reject a connection from anyone else.
        self.later_ids = set(ids[my_index + 1:])
        self.listener = listener
        self.handshake_timeout = handshake_timeout
        self.connect_retries = connect_retries
        self.retry_delay = retry_delay

        # neighbor_id -> NeighborConnection, for every open connection
        self.connections = {}
        # several threads add and remove connections, so the table needs a lock
        self.lock = threading.Lock()
        # set when shutdown() is called, so the accept loop knows to stop
        self.stopping = threading.Event()
        self.server_sock = None
        self.accept_thread = None

    # Start listening for later peers, then connect to all earlier peers.
    #
    # We listen FIRST so that if a later peer starts while we're still
    # connecting out, its connection doesn't get refused.
    #
    # This returns once every outgoing connection is made. Incoming
    # connections keep being accepted on a background thread.
    def start(self) -> None:
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # SO_REUSEADDR lets us reuse the port right away if we restart the
        # program, instead of waiting for the OS to free it up
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # "" means listen on every network interface of this machine
        self.server_sock.bind(("", self.me.port))
        self.server_sock.listen()
        # With a timeout, accept() wakes up every half second. That gives the
        # accept loop a chance to check whether we're shutting down.
        self.server_sock.settimeout(0.5)
        self.accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.accept_thread.start()

        for peer in self.earlier_peers:
            self._connect_to(peer.peer_id, peer.host, peer.port)

    # Get the connection to one neighbor, or None if we aren't connected.
    def get(self, neighbor_id):
        with self.lock:
            return self.connections.get(neighbor_id)

    # Get a copy of the list of all open connections. It's a copy so the
    # caller can loop over it while connections are being added or removed.
    def all_connections(self):
        with self.lock:
            return list(self.connections.values())

    # Send the same message to every neighbor. Used for HAVE, since every
    # neighbor needs to hear when we get a new piece.
    def broadcast(self, msg: Message) -> None:
        for conn in self.all_connections():
            conn.send(msg)

    # Stop accepting connections and close every connection.
    def shutdown(self) -> None:
        self.stopping.set()
        if self.server_sock is not None:
            close_socket(self.server_sock)
        if self.accept_thread is not None:
            self.accept_thread.join(timeout=2)
        conns = self.all_connections()
        # close them all first so they can finish in parallel...
        for conn in conns:
            conn.close()
        # ...then wait for each writer to send its last queued messages.
        # Without this the program could exit before the final HAVE
        # messages go out, and other peers would never learn we finished.
        for conn in conns:
            conn.join(timeout=5)

    # Connect out to an earlier peer and do the handshake.
    def _connect_to(self, peer_id: int, host: str, port: int) -> None:
        # The earlier peer should already be running, but it might not be
        # listening yet, so try a few times before giving up.
        sock = None
        for attempt in range(self.connect_retries):
            try:
                sock = socket.create_connection((host, port), timeout=self.handshake_timeout)
                break
            except OSError:
                time.sleep(self.retry_delay)
        if sock is None:
            raise ConnectionError(f"Could not connect to peer {peer_id} at {host}:{port}")

        try:
            # we connected, so we send our handshake first
            sock.sendall(build_handshake(self.my_id))
            # then read theirs and make sure it's the peer we meant to reach
            their_id = parse_handshake(recv_exact(sock, HANDSHAKE_LENGTH))
            if their_id != peer_id:
                raise ValueError(f"Expected handshake from {peer_id}, got {their_id}")
            # The timeout was only for connecting and the handshake. After
            # this the reader thread should wait as long as it needs to.
            sock.settimeout(None)
            self._register(peer_id, sock, True)
        except Exception:
            # don't leave a half-set-up socket open
            close_socket(sock)
            raise

    # Runs on the accept thread. Waits for later peers to connect.
    def _accept_loop(self) -> None:
        while not self.stopping.is_set():
            try:
                sock, addr = self.server_sock.accept()
            except socket.timeout:
                # nobody connected in the last half second; check stopping and wait again
                continue
            except OSError:
                # the server socket was closed by shutdown()
                break
            # Do the handshake on its own thread. If one peer connects but is
            # slow to send its handshake, it won't block everyone else.
            threading.Thread(target=self._handle_incoming, args=(sock,), daemon=True).start()

    # Handle one incoming connection: read their handshake, check it, reply.
    def _handle_incoming(self, sock) -> None:
        try:
            # read their handshake first to find out who connected
            sock.settimeout(self.handshake_timeout)
            their_id = parse_handshake(recv_exact(sock, HANDSHAKE_LENGTH))
            # Only peers listed after us should ever connect to us. Anyone
            # else is either misconfigured or not part of this run.
            if their_id not in self.later_ids:
                raise ValueError(f"Unexpected handshake from peer {their_id}")
            # they check out, so send our handshake back
            sock.sendall(build_handshake(self.my_id))
            sock.settimeout(None)
            self._register(their_id, sock, False)
        except Exception as e:
            # bad handshake, timeout, or unknown peer: hang up on them
            print(f"[peer {self.my_id}] rejected incoming connection: {e}")
            close_socket(sock)

    # Turn a socket that finished its handshake into a NeighborConnection.
    def _register(self, neighbor_id: int, sock, outgoing) -> None:
        # We pass self as the listener, so the connection reports to us first
        # (see on_message and on_disconnected at the bottom).
        conn = NeighborConnection(neighbor_id, sock, self, outgoing)
        with self.lock:
            if self.stopping.is_set():
                raise ConnectionError("Shutting down")
            # we should only ever have one connection to each peer
            if neighbor_id in self.connections:
                raise ValueError(f"Already connected to peer {neighbor_id}")
            self.connections[neighbor_id] = conn
        # The order matters here. We tell the listener about the connection
        # BEFORE starting its threads. Otherwise a message from the neighbor
        # could arrive before the listener knows this neighbor exists. It also
        # lets the listener queue our BITFIELD to be the first message sent.
        self.listener.on_connected(conn)
        conn.start()

    # Each NeighborConnection calls these. We pass the message straight on.
    def on_message(self, neighbor_id, msg):
        self.listener.on_message(neighbor_id, msg)

    # When a connection ends, take it out of our table, then pass it on.
    def on_disconnected(self, neighbor_id):
        with self.lock:
            self.connections.pop(neighbor_id, None)
        self.listener.on_disconnected(neighbor_id)
