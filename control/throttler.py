import threading
import time

class Throttler:
    def __init__(self, max_threads=32, target_bytes_per_sec=None):
        self._max_threads = max_threads
        self._target_bps = target_bytes_per_sec
        self._buffer_size = 1024 * 1024 # 1MB by default
        
        self.paused_event = threading.Event()
        self.paused_event.set() # Not paused initially
        
        self.lock = threading.Lock()
        
        # Rate limiting state
        self.last_check_time = time.time()
        self.bytes_since_check = 0

    def get_max_threads(self):
        with self.lock:
            return self._max_threads

    def set_max_threads(self, count):
        with self.lock:
            self._max_threads = max(1, count)

    def get_buffer_size(self):
        with self.lock:
            return self._buffer_size

    def set_buffer_size(self, size):
        with self.lock:
            self._buffer_size = max(1024, size) # min 1KB

    def set_target_bps(self, bps):
        with self.lock:
            self._target_bps = bps

    def pause(self):
        self.paused_event.clear()

    def resume(self):
        self.paused_event.set()

    def wait_if_paused(self):
        self.paused_event.wait()

    def enforce_speed_limit(self, bytes_read):
        """Simplistic token bucket / time-sleep throttler per thread."""
        with self.lock:
            target = self._target_bps
            
        if target is None or target <= 0:
            return
            
        with self.lock:
            self.bytes_since_check += bytes_read
            now = time.time()
            elapsed = now - self.last_check_time
            
            # Reset window every 0.1s
            if elapsed >= 0.1:
                expected_bytes = target * elapsed
                if self.bytes_since_check > expected_bytes:
                    excess_bytes = self.bytes_since_check - expected_bytes
                    sleep_time = excess_bytes / target
                    time.sleep(sleep_time)
                
                self.last_check_time = time.time()
                self.bytes_since_check = 0
