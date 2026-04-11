import subprocess
import threading
import time
import re
from collections import deque

class LatencyMonitor:
    def __init__(self, target_ip="8.8.8.8", interval=1.0, history_size=10):
        self.target_ip = target_ip
        self.interval = interval
        self.history = deque(maxlen=history_size)
        self.running = False
        self.thread = None
        self.current_latency = 0.0
        self.current_jitter = 0.0
        self.packet_loss = 0.0
        self.lock = threading.Lock()
        
    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()

    def _ping(self):
        try:
            # -n 1 for 1 request, -w 1000 for 1000ms timeout
            output = subprocess.check_output(
                ["ping", "-n", "1", "-w", "1000", self.target_ip], 
                stderr=subprocess.STDOUT,
                universal_newlines=True
            )
            
            # Simple regex to extract time=Xms
            match = re.search(r"time[=<](\d+)ms", output)
            if match:
                return float(match.group(1))
            return None # Packet loss / Timeout
        except Exception:
            return None

    def _monitor_loop(self):
        while self.running:
            latency = self._ping()
            
            with self.lock:
                if latency is not None:
                    self.history.append(latency)
                    self.current_latency = latency
                else:
                    # Treat loss as very high latency for historical smoothing
                    self.history.append(1000.0) 
                    self.current_latency = 1000.0
                    
                if len(self.history) > 1:
                    # Calculate Jitter as variance/avg diff between consecutive pings
                    diffs = [abs(self.history[i] - self.history[i-1]) for i in range(1, len(self.history))]
                    self.current_jitter = sum(diffs) / len(diffs)
                else:
                    self.current_jitter = 0.0
                    
                # Simplistic packet loss % over the history window
                losses = sum(1 for x in self.history if x >= 1000.0)
                self.packet_loss = (losses / len(self.history)) * 100 if len(self.history) > 0 else 0.0

            time.sleep(self.interval)

    def get_stats(self):
        with self.lock:
            if len(self.history) == 0:
                avg = 0.0
            else:
                avg = sum(self.history) / len(self.history)
            
            return {
                "latency_current": self.current_latency,
                "latency_avg": avg,
                "jitter": self.current_jitter,
                "packet_loss": self.packet_loss
            }
