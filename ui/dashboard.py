from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.progress import Progress, BarColumn, TextColumn, TransferSpeedColumn, TimeRemainingColumn
import time

class Dashboard:
    def __init__(self, engine, latency_mon, net_mon, activity_det, throttler):
        self.engine = engine
        self.latency_mon = latency_mon
        self.net_mon = net_mon
        self.activity_det = activity_det
        self.throttler = throttler
        self.running = False

    def _generate_layout(self):
        # Top Panel - Main stats
        table = Table(show_header=True, header_style="bold magenta", expand=True)
        table.add_column("Metric")
        table.add_column("Value")
        
        lat_stats = self.latency_mon.get_stats()
        net_stats = self.net_mon.get_stats()
        activity = self.activity_det.get_activity()
        
        table.add_row("Detected Activity", f"[bold cyan]{activity}[/bold cyan]")
        table.add_row("Latency / Jitter", f"{lat_stats['latency_current']:.1f}ms / {lat_stats['jitter']:.1f}ms")
        table.add_row("Packet Loss", f"{lat_stats['packet_loss']:.1f}%")
        
        dl_mbps = net_stats["download_speed_bps"] / 1024 / 1024
        ul_mbps = net_stats["upload_speed_bps"] / 1024 / 1024
        table.add_row("Sys Download / Upload", f"{dl_mbps:.2f} MB/s | {ul_mbps:.2f} MB/s")
        
        table.add_row("Max Core Threads", f"{self.throttler.get_max_threads()} (Active: {self.engine.active_threads})")
        table.add_row("Buffer Size Constraint", f"{self.throttler.get_buffer_size() / 1024:.1f} KB")

        # Bottom Panel - Progress
        total_size = self.engine.storage.metadata.get("total_size", 0) if hasattr(self.engine, 'storage') and self.engine.storage else 0
        downloaded = self.engine.total_bytes_downloaded
        if total_size > 0:
            pct = (downloaded / total_size) * 100
        else:
            pct = 0.0
            
        progress_text = Text(f"Downloaded: {downloaded / 1024 / 1024:.2f} MB / {total_size / 1024 / 1024:.2f} MB ({pct:.1f}%)", style="green")
        
        layout = Layout()
        layout.split_column(
            Layout(Panel(table, title="Network Intelligence", border_style="blue"), ratio=2),
            Layout(Panel(progress_text, title="Download Progress", border_style="yellow"))
        )
        return layout

    def start_sync(self):
        self.running = True
        with Live(self._generate_layout(), refresh_per_second=2) as live:
            try:
                # Keep running until the download is complete or we stop
                while self.running:
                    # check if the engine has stopped
                    if getattr(self.engine, 'aborted', None) and self.engine.aborted.is_set():
                        break
                    if hasattr(self.engine, 'storage') and self.engine.storage:
                        with self.engine.storage.lock:
                            done = True
                            for c in self.engine.storage.metadata.get("chunks", []):
                                if c.get("status") != "done":
                                    done = False
                                    break
                        if done and len(self.engine.storage.metadata.get("chunks", [])) > 0:
                            self.running = False
                            
                    # Check if the engine thread itself died abruptly
                    if hasattr(self, 'down_thread') and not self.down_thread.is_alive():
                        if not done:
                            # Thread died but we aren't done = Fatal network drop on all threads
                            self.running = False
                            break

                    live.update(self._generate_layout())
                    time.sleep(0.5)
            except KeyboardInterrupt:
                self.running = False
