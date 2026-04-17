import os
import time
import requests
import urllib3
import threading
import hashlib
import sys
import queue
from .storage_manager import StorageManager


class DownloadEngine:
    def __init__(self, url, file_path, throttler, default_chunk_size=5*1024*1024):
        self.url = url
        self.file_path = file_path
        self.throttler = throttler
        self.default_chunk_size = default_chunk_size
        self.storage = StorageManager(file_path)
        self.aborted = threading.Event()
        self.total_bytes_downloaded = 0
        self.lock = threading.Lock()
        self.active_threads = 0
        self.chunk_fails = {}
        
    def get_file_info(self):
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}
        head = requests.head(self.url, headers=headers, allow_redirects=True, timeout=10)
        size = int(head.headers.get("content-length", 0))
        accept_ranges = head.headers.get("accept-ranges", "none") == "bytes"
        return size, accept_ranges

    def _update_progress(self, size):
        with self.lock:
            self.total_bytes_downloaded += size

    def _download_chunk(self, chunk):
        if self.aborted.is_set():
            return chunk["id"], False

        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}
        if chunk['end'] > 0:
            headers["Range"] = f"bytes={chunk['start']}-{chunk['end']}"

        try:
            self.storage.mark_chunk_status(chunk["id"], "downloading")
            response = requests.get(self.url, headers=headers, stream=True, timeout=10)
            response.raise_for_status()

            if response.status_code == 200 and chunk['id'] != 0:
                print(f"\n[FATAL] Server ignored HTTP Range boundaries on chunk {chunk['id']}! Aborting down-stream threads to prevent massive sparse map overwrite.", file=sys.stderr)
                self.aborted.set()
                self.storage.mark_chunk_status(chunk["id"], "pending")
                return False

            offset = chunk['start']
            piece_sha256 = hashlib.sha256() if "expected_hash" in chunk else None
            
            for data in response.iter_content(chunk_size=self.throttler.get_buffer_size()):
                if self.aborted.is_set():
                    self.storage.mark_chunk_status(chunk["id"], "pending")
                    return chunk["id"], False

                self.throttler.wait_if_paused()
                
                if data:
                    length = len(data)
                    if piece_sha256:
                        piece_sha256.update(data)
                        
                    self.storage.write_chunk_data(chunk["id"], offset, data)
                    offset += length
                    self._update_progress(length)
                    
                    self.throttler.enforce_speed_limit(length)

            if piece_sha256:
                if piece_sha256.hexdigest() != chunk["expected_hash"]:
                    with self.lock:
                        self.chunk_fails[chunk["id"]] = self.chunk_fails.get(chunk["id"], 0) + 1
                        if self.chunk_fails[chunk["id"]] >= 3:
                            print(f"\n[FATAL] Chunk {chunk['id']} failed checksum 3 times! Terminating.", file=sys.stderr)
                            self.aborted.set()
                    
                    # Back out progress visually before resetting payload chunk status
                    self._update_progress(-(offset - chunk['start'])) 
                    self.storage.mark_chunk_status(chunk["id"], "pending")
                    return chunk["id"], False
                    
            # Reached end of stream successfully.
            self.storage.mark_chunk_status(chunk["id"], "done")
            return chunk["id"], True

        except Exception as e:
            self.storage.mark_chunk_status(chunk["id"], "pending")
            return False
            
    def start(self, manifest_hashes=None):
        size, supports_ranges = self.get_file_info()
        
        if not supports_ranges:
            # fallback
            self.default_chunk_size = size if size > 0 else 1024*1024*100
            
        self.storage.initialize_file(size, self.default_chunk_size, manifest_hashes)
        pending_chunks = self.storage.get_pending_chunks()
        
        if not pending_chunks:
            return True

        download_queue = queue.Queue()
        for c in pending_chunks:
            download_queue.put(c)
            
        active_workers = []
        
        def worker():
            with self.lock:
                self.active_threads += 1
            try:
                while not self.aborted.is_set():
                    # Dynamic Thread Retirement: Check if boundary dropped
                    with self.lock:
                        if self.active_threads > self.throttler.get_max_threads():
                            return
                            
                    try:
                        chunk = download_queue.get_nowait()
                    except queue.Empty:
                        break
                        
                    success = self._download_chunk(chunk)
                    if not success and not self.aborted.is_set():
                        # The chunk failed piece-wise checks or net-timeouts; inject natively back into the evaluation loop
                        download_queue.put(chunk)
                    
                    download_queue.task_done()
            finally:
                with self.lock:
                    self.active_threads -= 1

        try:
            while not download_queue.empty() and not self.aborted.is_set():
                target = self.throttler.get_max_threads()
                active_workers = [t for t in active_workers if t.is_alive()]
                
                while len(active_workers) < target and not download_queue.empty() and not self.aborted.is_set():
                    t = threading.Thread(target=worker, daemon=True)
                    t.start()
                    active_workers.append(t)
                    
                time.sleep(0.2)
                
            # Wait for remaining active workers evaluating their current chunks securely
            for t in active_workers:
                t.join()
                
        except KeyboardInterrupt:
            self.aborted.set()
            return False

        return self.storage.check_completion()
