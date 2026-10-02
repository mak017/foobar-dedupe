import os
import sqlite3
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple, Any

import mutagen
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4

from config import DATABASE_PATH, LIBRARY_DIRS, AUDIO_EXTENSIONS
from normalizer import extract_artist, extract_title_and_mix, normalize_text


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DATABASE_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def init_db():
    conn = get_db_connection()
    with conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            path TEXT PRIMARY KEY,
            filename TEXT,
            artist TEXT,
            artist_norm TEXT,
            title TEXT,
            title_norm TEXT,
            mix_name TEXT,
            mix_type TEXT,
            duration REAL,
            bitrate INTEGER,
            format TEXT,
            is_lossless INTEGER,
            file_size INTEGER,
            mtime REAL
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_title_norm ON tracks(title_norm);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_artist_norm ON tracks(artist_norm);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mtime ON tracks(mtime);")
    conn.close()


def parse_filename_fallback(filename: str) -> Tuple[str, str]:
    """Fallback if tags are missing: extract artist and title from filename."""
    stem = Path(filename).stem
    # Remove track numbers: "01 - ", "01. ", "01-", etc.
    stem = re.sub(r'^\d+[\s\.\-_]+', '', stem)
    
    parts = stem.split(" - ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    
    # Try underscore separation if no " - "
    if "_" in stem and "-" in stem:
        parts = stem.split("-", 1)
        return parts[0].replace("_", " ").strip(), parts[1].replace("_", " ").strip()
        
    return "", stem.replace("_", " ").strip()


def extract_file_metadata(file_path: str) -> Optional[Dict[str, Any]]:
    """Reads audio metadata and extracts normalized tags."""
    try:
        p = Path(file_path)
        stat = p.stat()
        file_size = stat.st_size
        mtime = stat.st_mtime
        ext = p.suffix.lower()

        audio = mutagen.File(file_path, easy=True)
        if audio is None or audio.info is None:
            return None

        duration = getattr(audio.info, 'length', 0.0)
        if duration <= 0:
            return None

        # Bitrate & Lossless detection
        is_lossless = 0
        bitrate = 0
        fmt = ext.replace('.', '').upper()

        if ext in {'.flac', '.wav', '.alac'}:
            is_lossless = 1
            # Calculate nominal bitrate for lossless
            bitrate = int((file_size * 8) / (duration * 1000)) if duration > 0 else 1411
        elif ext in {'.mp4', '.m4a', '.aac'}:
            bitrate = int(getattr(audio.info, 'bitrate', 0) / 1000)
            if bitrate == 0 and duration > 0:
                bitrate = int((file_size * 8) / (duration * 1000))
        elif ext == '.mp3':
            bitrate = int(getattr(audio.info, 'bitrate', 0) / 1000)
            if bitrate == 0 and duration > 0:
                bitrate = int((file_size * 8) / (duration * 1000))
        else:
            bitrate = int(getattr(audio.info, 'bitrate', 0) / 1000)

        # Tags
        artist_raw = ""
        title_raw = ""

        try:
            artist_list = audio.get('artist', [])
            if artist_list:
                artist_raw = str(artist_list[0]).strip()
        except Exception:
            pass

        try:
            title_list = audio.get('title', [])
            if title_list:
                title_raw = str(title_list[0]).strip()
        except Exception:
            pass

        # Fallback to filename if empty
        if not artist_raw or not title_raw:
            fn_artist, fn_title = parse_filename_fallback(p.name)
            if not artist_raw:
                artist_raw = fn_artist
            if not title_raw:
                title_raw = fn_title

        artist_norm, _ = extract_artist(artist_raw)
        title_norm, mix_name, mix_type = extract_title_and_mix(title_raw)

        return {
            'path': str(p),
            'filename': p.name,
            'artist': artist_raw,
            'artist_norm': artist_norm,
            'title': title_raw,
            'title_norm': title_norm,
            'mix_name': mix_name,
            'mix_type': mix_type,
            'duration': round(duration, 2),
            'bitrate': bitrate,
            'format': fmt,
            'is_lossless': is_lossless,
            'file_size': file_size,
            'mtime': mtime
        }
    except Exception:
        return None


class LibraryIndexer:
    def __init__(self):
        init_db()

    def get_existing_file_cache(self) -> Dict[str, Tuple[float, int]]:
        """Returns a dict of {path: (mtime, file_size)} currently in the DB."""
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT path, mtime, file_size FROM tracks")
        cache = {row['path']: (row['mtime'], row['file_size']) for row in cur.fetchall()}
        conn.close()
        return cache

    def scan_and_update(self, progress_callback=None, max_workers: int = 8) -> Dict[str, int]:
        """
        Scans library directories, updates modified/new files, and purges deleted ones.
        """
        existing_cache = self.get_existing_file_cache()
        files_to_process = []
        found_paths = set()

        # Step 1: Rapid directory traversal
        for lib_dir in LIBRARY_DIRS:
            if not os.path.exists(lib_dir):
                continue
            for root, _, files in os.walk(lib_dir):
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext in AUDIO_EXTENSIONS:
                        full_path = os.path.join(root, f)
                        found_paths.add(full_path)
                        try:
                            st = os.stat(full_path)
                            mtime = st.st_mtime
                            fsize = st.st_size
                            # Check if file is new or modified
                            if full_path not in existing_cache:
                                files_to_process.append(full_path)
                            else:
                                cached_mtime, cached_size = existing_cache[full_path]
                                if abs(mtime - cached_mtime) > 1.0 or fsize != cached_size:
                                    files_to_process.append(full_path)
                        except OSError:
                            continue

        # Step 2: Remove missing tracks from DB
        missing_paths = set(existing_cache.keys()) - found_paths
        if missing_paths:
            conn = get_db_connection()
            with conn:
                conn.executemany("DELETE FROM tracks WHERE path = ?", [(p,) for p in missing_paths])
            conn.close()

        # Step 3: Process new/updated files in parallel
        total_to_process = len(files_to_process)
        inserted_count = 0

        if total_to_process > 0:
            batch = []
            conn = get_db_connection()
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(extract_file_metadata, fp): fp for fp in files_to_process}
                
                for i, future in enumerate(as_completed(futures), 1):
                    meta = future.result()
                    if meta:
                        batch.append(meta)
                        inserted_count += 1

                    # Commit every 500 records
                    if len(batch) >= 500:
                        self._save_batch(conn, batch)
                        batch.clear()

                    if progress_callback and (i % 250 == 0 or i == total_to_process):
                        progress_callback(i, total_to_process)

                if batch:
                    self._save_batch(conn, batch)
                    batch.clear()

            conn.close()

        return {
            'scanned': len(found_paths),
            'updated': inserted_count,
            'deleted': len(missing_paths)
        }

    def _save_batch(self, conn: sqlite3.Connection, batch: List[Dict[str, Any]]):
        with conn:
            conn.executemany("""
            INSERT OR REPLACE INTO tracks (
                path, filename, artist, artist_norm, title, title_norm,
                mix_name, mix_type, duration, bitrate, format,
                is_lossless, file_size, mtime
            ) VALUES (
                :path, :filename, :artist, :artist_norm, :title, :title_norm,
                :mix_name, :mix_type, :duration, :bitrate, :format,
                :is_lossless, :file_size, :mtime
            );
            """, batch)

    def find_candidates(self, title_norm: str, artist_norm: str = "") -> List[sqlite3.Row]:
        """Finds candidate tracks from DB with similar title and artist."""
        if not title_norm:
            return []
        
        conn = get_db_connection()
        cur = conn.cursor()

        # First try exact title match
        cur.execute("SELECT * FROM tracks WHERE title_norm = ?", (title_norm,))
        rows = cur.fetchall()

        # If not enough matches, try LIKE substring match
        if len(rows) < 5:
            cur.execute("SELECT * FROM tracks WHERE title_norm LIKE ?", (f"%{title_norm}%",))
            like_rows = cur.fetchall()
            existing_paths = {r['path'] for r in rows}
            for lr in like_rows:
                if lr['path'] not in existing_paths:
                    rows.append(lr)

        conn.close()
        return rows
