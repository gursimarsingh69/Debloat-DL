import psutil
import threading
import time

class ActivityDetector:
    def __init__(self, network_monitor, latency_monitor, interval=2.0):
        self.network_monitor = network_monitor
        self.latency_monitor = latency_monitor
        self.interval = interval
        self.running = False
        self.thread = None
        self.lock = threading.Lock()
        
        self.current_activity = "Idle"
        
    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()

    def _monitor_loop(self):
        while self.running:
            try:
                # Basic heuristic check based on current network bandwidth and connections
                net_stats = self.network_monitor.get_stats()
                lat_stats = self.latency_monitor.get_stats()
                
                dl_mbps = net_stats["download_speed_bps"] / 1024 / 1024
                ul_mbps = net_stats["upload_speed_bps"] / 1024 / 1024
                
                # Check active UDP connections
                # Requires admin privileges on some systems to get full process table, but we count connections
                # UDP has no state, so status is always NONE. We just count active bindings.
                conns = psutil.net_connections(kind='udp')
                udp_conns = len(conns)
                activity = "Idle"
                
                if lat_stats.get("target_loss", 0.0) > 5.0 or lat_stats.get("buffer_bloat", 0.0) > 100.0 or lat_stats.get("dns_time", 0.0) > 150.0:
                    activity = "Congested"
                elif udp_conns > 50:
                    # Many UDP packets sent/received signals gaming or VoIP
                    activity = "Gaming"
                elif dl_mbps > 10.0:
                    # Continuous high download likely streaming
                    activity = "Streaming"
                
                with self.lock:
                    self.current_activity = activity
            except psutil.AccessDenied:
                # If access denied for net_connections, fall back to pure bandwidth heuristics
                with self.lock:
                    self.current_activity = "Unknown (No Admin)"
                    
            time.sleep(self.interval)

    def get_activity(self):
        with self.lock:
            return self.current_activity
