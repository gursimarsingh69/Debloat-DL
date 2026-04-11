import os
import time
import requests
import urllib3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from .storage_manager import StorageManager

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
        
    def get_file_info(self):
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}
        head = requests.head(self.url, headers=headers, allow_redirects=True, verify=False, timeout=10)
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
        
        # Wait until allowed by thread constraints
        while True:
            if self.aborted.is_set():
                return chunk["id"], False
            
            with self.lock:
                if self.active_threads < self.throttler.get_max_threads():
                    self.active_threads += 1
                    break
            time.sleep(0.1)

        try:
            self.storage.mark_chunk_status(chunk["id"], "downloading")
            response = requests.get(self.url, headers=headers, stream=True, verify=False, timeout=15)
            response.raise_for_status()

            offset = chunk['start']
            for data in response.iter_content(chunk_size=self.throttler.get_buffer_size()):
                if self.aborted.is_set():
                    self.storage.mark_chunk_status(chunk["id"], "pending")
                    return chunk["id"], False

                self.throttler.wait_if_paused()
                
                if data:
                    length = len(data)
                    self.storage.write_chunk_data(chunk["id"], offset, data)
                    offset += length
                    self._update_progress(length)
                    
                    self.throttler.enforce_speed_limit(length)

            # Reached end of stream successfully.
            self.storage.mark_chunk_status(chunk["id"], "done")
            return chunk["id"], True

        except Exception as e:
            self.storage.mark_chunk_status(chunk["id"], "pending")
            return chunk["id"], False
        finally:
            with self.lock:
                self.active_threads -= 1

    def start(self):
        size, supports_ranges = self.get_file_info()
        
        if not supports_ranges:
            # fallback
            self.default_chunk_size = size if size > 0 else 1024*1024*100
            
        self.storage.initialize_file(size, self.default_chunk_size)
        pending_chunks = self.storage.get_pending_chunks()
        
        if not pending_chunks:
            return True

        executor = ThreadPoolExecutor(max_workers=32)
        futures = []
        for chunk in pending_chunks:
            futures.append(executor.submit(self._download_chunk, chunk))
            
        try:
            for future in as_completed(futures):
                idx, success = future.result()
        except KeyboardInterrupt:
            self.aborted.set()
            executor.shutdown(wait=False)
            return False

        executor.shutdown(wait=True)
        return self.storage.check_completion()
