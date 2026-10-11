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


DUMMY_ARTISTS = {'', 'unknown', 'unknown artist', 'various', 'various artists', 'va', 'untitled', 'various artists'}
DUMMY_TITLES = {'', 'untitled', 'title', 'audio track'}


def smart_parse_metadata(artist_tag: str, title_tag: str, filename: str) -> Tuple[str, str]:
    """
    Intelligently extracts clean artist and title, handling:
    - Missing or empty tags
    - Dummy tags like 'Unknown Artist', 'Track 01'
    - Title tags containing 'Artist - Title'
    - Messy filenames with track numbers, underscores, and release group suffixes
    """
    artist = artist_tag.strip() if artist_tag else ""
    title = title_tag.strip() if title_tag else ""

    if artist.lower() in DUMMY_ARTISTS:
        artist = ""
    if title.lower() in DUMMY_TITLES or re.match(r'^track\s*\d+$', title.lower()):
        title = ""

    # Check if title tag contains "Artist - Title"
    if title and " - " in title:
        parts = title.split(" - ", 1)
        if not artist or artist.lower() in DUMMY_ARTISTS:
            artist = parts[0].strip()
            title = parts[1].strip()

    # If still missing artist or title, extract from filename
    if not artist or not title:
        stem = Path(filename).stem
        # Strip bracketed release tags like [WEB], [320kbps], [ASOT123]
        stem = re.sub(r'\[[^\]]*\]', '', stem).strip()
        # Strip leading numbers/CD prefixes: "01. ", "(01) ", "CD1-01 ", "01-", "01 - "
        stem = re.sub(r'^\s*(\[|\()?(\d+|cd\s*\d+|disc\s*\d+)(\]|\))?[\s\.\-_]+', '', stem, flags=re.IGNORECASE).strip()

        stem_norm = stem.replace('_-_', ' - ').replace('__', ' - ')

        fn_artist = ""
        fn_title = ""

        if " - " in stem_norm:
            p = stem_norm.split(" - ", 1)
            fn_artist = p[0].replace('_', ' ').strip()
            fn_title = p[1].replace('_', ' ').strip()
        elif "-" in stem:
            p = stem.split("-")
            if len(p) >= 2:
                fn_artist = p[0].replace('_', ' ').strip()
                # Omit trailing scene tag (<=6 chars like 'rec', 'web', 'dh') unless it's a mix keyword
                if len(p[-1]) <= 6 and not any(w in p[-1].lower() for w in ('mix', 'edit', 'dub')):
                    fn_title = "-".join(p[1:-1]).replace('_', ' ').strip()
                else:
                    fn_title = "-".join(p[1:]).replace('_', ' ').strip()
        else:
            fn_title = stem.replace('_', ' ').strip()

        if not artist and fn_artist:
            artist = fn_artist
        if not title and fn_title:
            title = fn_title

    # Final cleanup of any remaining underscores
    artist = artist.replace('_', ' ').strip()
    title = title.replace('_', ' ').strip()

    return artist, title


def extract_file_metadata(file_path: str) -> Optional[Dict[str, Any]]:
    """Reads audio metadata and extracts normalized tags with smart fallbacks."""
    try:
        p = Path(file_path)
        if not p.exists():
            return None

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

        # Raw tags
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

        # Apply smart parser to handle empty tags, DJ cuts, and messy filenames
        artist_clean, title_clean = smart_parse_metadata(artist_raw, title_raw, p.name)

        artist_norm, _ = extract_artist(artist_clean)
        title_norm, mix_name, mix_type = extract_title_and_mix(title_clean)

        return {
            'path': str(p),
            'filename': p.name,
            'artist': artist_clean,
            'artist_norm': artist_norm,
            'title': title_clean,
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
        """Finds candidate tracks from DB with similar title and/or artist."""
        if not title_norm and not artist_norm:
            return []
        
        conn = get_db_connection()
        cur = conn.cursor()
        found_rows = []
        seen_paths = set()

        def add_rows(rows):
            for r in rows:
                p = r['path']
                if p not in seen_paths:
                    seen_paths.add(p)
                    found_rows.append(r)

        # 1. Exact title match
        if title_norm:
            cur.execute("SELECT * FROM tracks WHERE title_norm = ?", (title_norm,))
            add_rows(cur.fetchall())

        # 2. Artist match (if artist provided)
        if artist_norm and len(artist_norm) > 2:
            cur.execute("SELECT * FROM tracks WHERE artist_norm = ? LIMIT 100", (artist_norm,))
            add_rows(cur.fetchall())

        # 3. Substring match on title (if title >= 4 chars)
        if title_norm and len(title_norm) >= 4:
            cur.execute("SELECT * FROM tracks WHERE title_norm LIKE ? LIMIT 50", (f"%{title_norm}%",))
            add_rows(cur.fetchall())

        # 4. Significant words match (e.g. "sun" and "moon")
        if title_norm:
            tokens = [t for t in title_norm.split() if len(t) >= 3 and t not in ('and', 'the', 'for', 'with', 'from')]
            if len(tokens) >= 2:
                conditions = " AND ".join(["title_norm LIKE ?"] * len(tokens[:3]))
                params = [f"%{t}%" for t in tokens[:3]]
                cur.execute(f"SELECT * FROM tracks WHERE {conditions} LIMIT 50", params)
                add_rows(cur.fetchall())

        conn.close()
        return found_rows
