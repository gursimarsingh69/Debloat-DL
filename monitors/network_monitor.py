import psutil
import threading
import time

class NetworkMonitor:
    def __init__(self, interval=1.0):
        self.interval = interval
        self.running = False
        self.thread = None
        self.lock = threading.Lock()
        
        self.last_bytes_recv = 0
        self.last_bytes_sent = 0
        
        self.curr_download_speed = 0.0 # bytes/sec
        self.curr_upload_speed = 0.0 # bytes/sec
        
        self.peak_download_speed = 0.0
        self.peak_upload_speed = 0.0

    def start(self):
        counters = psutil.net_io_counters()
        self.last_bytes_recv = counters.bytes_recv
        self.last_bytes_sent = counters.bytes_sent
        
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()

    def _monitor_loop(self):
        while self.running:
            time.sleep(self.interval)
            counters = psutil.net_io_counters()
            
            bytes_recv = counters.bytes_recv
            bytes_sent = counters.bytes_sent
            
            dl_speed = (bytes_recv - self.last_bytes_recv) / self.interval
            ul_speed = (bytes_sent - self.last_bytes_sent) / self.interval
            
            with self.lock:
                self.curr_download_speed = max(0.0, dl_speed)
                self.curr_upload_speed = max(0.0, ul_speed)
                
                self.peak_download_speed = max(self.peak_download_speed, self.curr_download_speed)
                self.peak_upload_speed = max(self.peak_upload_speed, self.curr_upload_speed)
                
            self.last_bytes_recv = bytes_recv
            self.last_bytes_sent = bytes_sent

    def get_stats(self):
        with self.lock:
            return {
                "download_speed_bps": self.curr_download_speed,
                "upload_speed_bps": self.curr_upload_speed,
                "peak_download_bps": self.peak_download_speed,
                "peak_upload_bps": self.peak_upload_speed
            }
