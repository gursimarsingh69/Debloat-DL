import os
import json
import hashlib
import threading

class StorageManager:
    def __init__(self, file_path):
        self.file_path = file_path
        self.meta_path = f"{file_path}.metadata.json"
        self.lock = threading.Lock()
        self.metadata = {}

    def initialize_file(self, total_size, chunk_size):
        """Creates a sparse file and metadata if not exists for random access write."""
        with self.lock:
            if not os.path.exists(self.file_path):
                with open(self.file_path, "wb") as f:
                    if total_size > 0:
                        f.seek(total_size - 1)
                        f.write(b'\0')
                    
            needs_init = True
            if os.path.exists(self.meta_path):
                try:
                    self._load_metadata_nolock()
                    needs_init = False
                except json.JSONDecodeError:
                    pass

            if needs_init:
                chunks = []
                num_chunks = (total_size + chunk_size - 1) // chunk_size if total_size > 0 else 1
                for i in range(num_chunks):
                    start = i * chunk_size
                    end = min(start + chunk_size - 1, total_size - 1) if total_size > 0 else 0
                    chunks.append({
                        "id": i,
                        "start": start,
                        "end": end,
                        "status": "pending"  # pending, downloading, done
                    })
                self.metadata = {"total_size": total_size, "chunks": chunks}
                self._save_metadata_nolock()

    def get_pending_chunks(self):
        """Returns chunks left to download and resets downloading chunks to pending."""
        with self.lock:
            pending = []
            for chunk in self.metadata.get("chunks", []):
                if chunk["status"] != "done":
                    chunk["status"] = "pending"
                    pending.append(dict(chunk)) # return copy
            self._save_metadata_nolock()
            return pending

    def write_chunk_data(self, chunk_id, offset, data):
        """Writes data at specific offset using random access."""
        with self.lock:
            # r+b doesn't truncate the file and allows seek
            with open(self.file_path, "r+b") as f:
                f.seek(offset)
                f.write(data)

    def mark_chunk_status(self, chunk_id, status):
        with self.lock:
            for chunk in self.metadata.get("chunks", []):
                if chunk["id"] == chunk_id:
                    chunk["status"] = status
                    break
            self._save_metadata_nolock()

    def _save_metadata_nolock(self):
        temp_path = self.meta_path + ".tmp"
        with open(temp_path, "w") as f:
            json.dump(self.metadata, f, indent=4)
        os.replace(temp_path, self.meta_path)

    def _load_metadata_nolock(self):
        with open(self.meta_path, "r") as f:
            self.metadata = json.load(f)

    def verify_sha256(self):
        """Returns the SHA256 of the downloaded file."""
        sha256 = hashlib.sha256()
        with open(self.file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096 * 64), b""):
                sha256.update(byte_block)
        return sha256.hexdigest()
        
    def check_completion(self):
        """Checks if all chunks are marked done."""
        with self.lock:
            for c in self.metadata.get("chunks", []):
                if c["status"] != "done":
                    return False
            # Clean up metadata if complete
            if os.path.exists(self.meta_path):
                os.remove(self.meta_path)
            return True
