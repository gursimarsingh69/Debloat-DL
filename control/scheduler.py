import threading
import time

class Scheduler:
    def __init__(self, throttler, latency_monitor, network_monitor, activity_detector, interval=1.0):
        self.throttler = throttler
        self.latency_monitor = latency_monitor
        self.network_monitor = network_monitor
        self.activity_detector = activity_detector
        self.interval = interval
        
        self.running = False
        self.thread = None
        
        self.baseline_latency = None

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._decision_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()

    def _decision_loop(self):
        while self.running:
            time.sleep(self.interval)
            
            lat_stats = self.latency_monitor.get_stats()
            activity = self.activity_detector.get_activity()
            
            cur_lat = lat_stats.get("target_latency", 0.0)
            jitter = lat_stats.get("target_jitter", 0.0)
            packet_loss = lat_stats.get("target_loss", 0.0)
            bloat = lat_stats.get("buffer_bloat", 0.0)
            
            if cur_lat <= 0:
                continue
                
            # Decision Matrix
            
            # 1. Extreme conditions (Packet Loss / Congested)
            if packet_loss > 5.0 or activity.startswith("Congested"):
                # Severe throttle
                self.throttler.set_max_threads(1)
                self.throttler.set_target_bps(1024 * 1024) # 1MB/s Hard cap
                self.throttler.set_buffer_size(64 * 1024)
                continue
                
            # 2. Activity based
            if activity.startswith("Gaming"):
                # Minimize jitter impacts
                self.throttler.set_max_threads(2)
                self.throttler.set_target_bps(5 * 1024 * 1024) # 5MB/s limit
                self.throttler.set_buffer_size(32 * 1024)
                continue
                
            if activity.startswith("Streaming"):
                # Medium throttle
                self.throttler.set_max_threads(8)
                self.throttler.set_target_bps(None) # unlimited technically, but limited threads reduce spikes
                self.throttler.set_buffer_size(512 * 1024)
                continue
                
            # 3. Dynamic Latency check (AIMD like)
            if bloat > 80.0 or jitter > 50.0:
                # High latency -> Multiplicative Decrease
                curr_threads = self.throttler.get_max_threads()
                new_threads = max(2, curr_threads // 2)
                self.throttler.set_max_threads(new_threads)
            elif bloat < 20.0 and jitter < 15.0:
                # Stable -> Additive Increase
                curr_threads = self.throttler.get_max_threads()
                if curr_threads < 32:
                    self.throttler.set_max_threads(curr_threads + 1)
                self.throttler.set_target_bps(None) # Remove limit
                self.throttler.set_buffer_size(1024 * 1024) # 1MB buffer
