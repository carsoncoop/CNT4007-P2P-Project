import random
import threading
import traceback


class ChokeManager:
    def __init__(self, preferred_neighbor_count, preferred_interval_seconds,
                 optimistic_interval_seconds, get_connected_neighbors,
                 we_have_whole_file, logger, random_generator=None):
        """get_connected_neighbors: function returning the current list of neighbors.
        we_have_whole_file: function returning True once our own file is complete."""
        self._preferred_neighbor_count = preferred_neighbor_count
        self._preferred_interval_seconds = preferred_interval_seconds
        self._optimistic_interval_seconds = optimistic_interval_seconds
        self._get_connected_neighbors = get_connected_neighbors
        self._we_have_whole_file = we_have_whole_file
        self._logger = logger
        self._random = random_generator or random.Random()

        self._state_lock = threading.Lock()
        self._preferred_neighbors = []
        self._optimistic_neighbor = None
        self._last_logged_preferred_ids = []

        self._stop_signal = threading.Event()
        self._timer_threads = []

    def start_timers(self):
        if self._timer_threads:
            return
        self._stop_signal.clear()
        rounds = [
            ("preferred-timer", self._preferred_interval_seconds, self.pick_preferred_neighbors),
            ("optimistic-timer", self._optimistic_interval_seconds, self.pick_optimistic_neighbor),
        ]
        for name, seconds, job in rounds:
            thread = threading.Thread(target=self._repeat_every, args=(seconds, job),
                                      name=name, daemon=True)
            thread.start()
            self._timer_threads.append(thread)

    def stop_timers(self):
        self._stop_signal.set()
        for thread in self._timer_threads:
            thread.join(timeout=2)
        self._timer_threads = []

    def _repeat_every(self, seconds, job):
        while not self._stop_signal.wait(seconds):
            try:
                job()
            except Exception:
                traceback.print_exc()

    def pick_preferred_neighbors(self):
        """Pick the interested neighbors who sent us the most data since the last round. 
        Unchoke the new picks and choke anyone else who was unchoked, except the optimistic neighbor."""
        with self._state_lock:
            all_neighbors = list(self._get_connected_neighbors())

            """Read and reset every neighbor's byte counter, even uninterested ones, so every neighbor starts the next interval at zero"""
            bytes_this_interval = {n.peer_id: n.take_bytes_received_this_interval()
                                   for n in all_neighbors}

            interested_neighbors = [n for n in all_neighbors if n.wants_our_pieces()]
            self._random.shuffle(interested_neighbors)

            if not self._we_have_whole_file():
                interested_neighbors.sort(key=lambda n: bytes_this_interval[n.peer_id],
                                          reverse=True)

            how_many_to_pick = min(max(self._preferred_neighbor_count, 0), len(interested_neighbors))
            new_preferred = interested_neighbors[:how_many_to_pick]

            """Unchoke the new picks, skipping any who are already unchoked (no duplicate messages)."""
            for neighbor in new_preferred:
                if neighbor.we_are_choking_them():
                    neighbor.unchoke()

            """Choke every unchoked neighbor who wasn't picked, but leave the optimistic neighbor alone (`is` checks that it is the very same object)."""
            for neighbor in all_neighbors:
                should_stay_unchoked = (neighbor in new_preferred
                                        or neighbor is self._optimistic_neighbor)
                if not should_stay_unchoked and not neighbor.we_are_choking_them():
                    neighbor.choke()
            self._preferred_neighbors = new_preferred

            new_preferred_ids = sorted(n.peer_id for n in new_preferred)
            if new_preferred_ids != self._last_logged_preferred_ids:
                self._last_logged_preferred_ids = new_preferred_ids
                self._logger.log_preferred_neighbors_changed(new_preferred_ids)

    def pick_optimistic_neighbor(self):
        """Pick one random neighbor who is choked but interested and unchoke it. The previous
        optimistic neighbor is choked again unless it is also a preferred neighbor."""
        with self._state_lock:
            eligible_neighbors = [n for n in self._get_connected_neighbors()
                                  if n.we_are_choking_them() and n.wants_our_pieces()]
            if not eligible_neighbors:
                return

            lucky_neighbor = self._random.choice(eligible_neighbors)
            previous_optimistic = self._optimistic_neighbor
            self._optimistic_neighbor = lucky_neighbor
            lucky_neighbor.unchoke()

            """Re-choke the old optimistic neighbor, unless it was re-picked, is also a preferred neighbor, or is already choked."""
            previous_should_be_choked = (previous_optimistic is not None
                                         and previous_optimistic is not lucky_neighbor
                                         and previous_optimistic not in self._preferred_neighbors
                                         and not previous_optimistic.we_are_choking_them())
            if previous_should_be_choked:
                previous_optimistic.choke()

            self._logger.log_optimistic_neighbor_changed(lucky_neighbor.peer_id)

    def get_optimistic_neighbor(self):
        with self._state_lock:
            return self._optimistic_neighbor

    def get_preferred_neighbors(self):
        with self._state_lock:
            return list(self._preferred_neighbors)