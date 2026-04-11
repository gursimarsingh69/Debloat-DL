import argparse
import sys
import threading
import time

from core.download_engine import DownloadEngine
from monitors.latency_monitor import LatencyMonitor
from monitors.network_monitor import NetworkMonitor
from monitors.activity_detector import ActivityDetector
from control.throttler import Throttler
from control.scheduler import Scheduler
from ui.dashboard import Dashboard

def main():
    parser = argparse.ArgumentParser(description="Latency Aware Download Manager")
    parser.add_argument("url", help="URL of the file to download")
    parser.add_argument("output", help="Output file path")
    args = parser.parse_args()

    # 1. Initialize monitors
    latency_mon = LatencyMonitor(target_ip="8.8.8.8", interval=0.5)
    net_mon = NetworkMonitor(interval=0.5)
    activity_det = ActivityDetector(network_monitor=net_mon, latency_monitor=latency_mon)

    # 2. Control loops
    throttler = Throttler(max_threads=32)
    scheduler = Scheduler(throttler, latency_mon, net_mon, activity_det)

    # 3. Engine
    engine = DownloadEngine(args.url, args.output, throttler)

    # Start monitors
    latency_mon.start()
    net_mon.start()
    activity_det.start()
    scheduler.start()

    # Need a small delay for baseline latency gathering
    time.sleep(2.0)

    # 4. UI Dashboard
    dashboard = Dashboard(engine, latency_mon, net_mon, activity_det, throttler)

    # We start download in a separate thread so Dashboard can block the main thread
    down_thread = threading.Thread(target=engine.start, daemon=True)
    dashboard.down_thread = down_thread
    down_thread.start()

    try:
        # Blocks the main thread with rich CLI layout
        dashboard.start_sync()
    except KeyboardInterrupt:
        engine.aborted.set()

    # Shutdown
    engine.aborted.set()
    down_thread.join(timeout=2)
    scheduler.stop()
    activity_det.stop()
    net_mon.stop()
    latency_mon.stop()
    
    print("\nDownload finished or interrupted. Verifying SHA256 if complete...")
    if engine.storage.check_completion():
        print(f"File verified. SHA256: {engine.storage.verify_sha256()}")
    else:
        print("Download incomplete. Run process again to resume.")

if __name__ == "__main__":
    main()
