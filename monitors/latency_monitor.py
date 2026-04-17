import subprocess
import threading
import time
import re
from collections import deque
import dns.resolver

class LatencyMonitor:
    def __init__(self, target_ip="8.8.8.8", interval=1.0, history_size=10):
        self.target_ip = target_ip
        self.interval = interval
        self.history_size = history_size
        self.running = False
        
        self.router_ip = None
        self.isp_ip = None
        
        # Histories mapping IP -> deque or name -> deque
        self.histories = {
            "target": deque(maxlen=history_size),
            "router": deque(maxlen=history_size),
            "isp": deque(maxlen=history_size)
        }
        
        self.stats = {
            "target_latency": 0.0,
            "target_jitter": 0.0,
            "target_loss": 0.0,
            "router_latency": 0.0,
            "isp_latency": 0.0,
            "dns_time": 0.0,
            "buffer_bloat": 0.0
        }
        
        self.min_target_latency = None
        self.lock = threading.Lock()
        
        self.thread = None
        self.resolver = dns.resolver.Resolver()
        self.resolver.timeout = 2.0
        self.resolver.lifetime = 2.0

    def start(self):
        self._find_hops()
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()

    def _find_hops(self):
        # Quick traceroute-equivalent using TTL limits
        self.router_ip = self._ping_ttl(1)
        self.isp_ip = self._ping_ttl(2)
        
        # If hop 2 resolves to the target directly, or same as router, we just set it to None to avoid duped work
        if self.isp_ip == self.target_ip or self.isp_ip == self.router_ip:
            self.isp_ip = None
            
        # Logging out to let UI know
        import sys
        print(f"\n[LatencyMonitor] Discovered Router IP: {self.router_ip}, ISP Hop: {self.isp_ip}", file=sys.stderr)

    def _ping_ttl(self, ttl):
        try:
            output = subprocess.check_output(
                ["ping", "-n", "1", "-i", str(ttl), "-w", "1000", self.target_ip], 
                stderr=subprocess.STDOUT,
                universal_newlines=True
            )
            match = re.search(r"from\s+([0-9\.]+):", output)
            if match:
                return match.group(1)
            return None
        except Exception:
            return None

    def _ping(self, ip):
        """Standard ICMP Ping"""
        if not ip: return None
        try:
            output = subprocess.check_output(
                ["ping", "-n", "1", "-w", "1000", ip], 
                stderr=subprocess.STDOUT,
                universal_newlines=True
            )
            match = re.search(r"time[=<](\d+)ms", output)
            if match:
                return float(match.group(1))
            if "time<1ms" in output or "time=0ms" in output:
                return 0.1
            return None
        except Exception:
            return None

    def _monitor_loop(self):
        dns_counter = 0
        while self.running:
            threads = []
            results = {}
            
            def do_ping(name, ip):
                results[name] = self._ping(ip)
                
            # Fire all pings in parallel to save cyclic delay
            for name, ip in [("target", self.target_ip), ("router", self.router_ip), ("isp", self.isp_ip)]:
                if ip is not None:
                    t = threading.Thread(target=do_ping, args=(name, ip))
                    t.start()
                    threads.append(t)
            
            # Check DNS latency sporadically to not get throttled. Every 5 loops.
            if dns_counter % 5 == 0:
                def do_dns():
                    start = time.time()
                    try:
                        self.resolver.resolve("google.com", "A") 
                        results["dns"] = (time.time() - start) * 1000
                    except Exception:
                        results["dns"] = None
                
                t_dns = threading.Thread(target=do_dns)
                t_dns.start()
                threads.append(t_dns)
            dns_counter += 1
            
            for t in threads:
                t.join(timeout=2.0)
                
            self._update_stats(results)
            time.sleep(self.interval)

    def _update_stats(self, results):
        with self.lock:
            # Process Target Latency
            t_lat = results.get("target")
            if t_lat is not None:
                self.histories["target"].append(t_lat)
                self.stats["target_latency"] = t_lat
                if self.min_target_latency is None or t_lat < self.min_target_latency:
                    self.min_target_latency = t_lat
            else:
                # packet loss proxy
                self.histories["target"].append(1000.0)
                self.stats["target_latency"] = 1000.0
                
            # Buffer bloat calculation
            if self.min_target_latency is not None and t_lat is not None:
                self.stats["buffer_bloat"] = max(0.0, t_lat - self.min_target_latency)
                
            # Target Jitter & Packet Loss
            t_hist = self.histories["target"]
            if len(t_hist) > 1:
                diffs = [abs(t_hist[i] - t_hist[i-1]) for i in range(1, len(t_hist))]
                self.stats["target_jitter"] = sum(diffs) / len(diffs)
            else:
                self.stats["target_jitter"] = 0.0
                
            losses = sum(1 for x in t_hist if x >= 1000.0)
            self.stats["target_loss"] = (losses / len(t_hist)) * 100 if len(t_hist) > 0 else 0.0
            
            # Router & ISP Tracking
            for node in ["router", "isp"]:
                if node in results:
                    lat = results.get(node)
                    if lat is not None:
                        self.histories[node].append(lat)
                        self.stats[f"{node}_latency"] = lat

            if "dns" in results and results["dns"] is not None:
                self.stats["dns_time"] = results["dns"]

    def get_stats(self):
        with self.lock:
            return dict(self.stats)

