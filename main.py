import os
import sys
import time
import ctypes
from pathlib import Path
from urllib.parse import unquote
from typing import List, Dict, Any, Optional

from config import AUDIO_EXTENSIONS
from indexer import LibraryIndexer, extract_file_metadata
from matcher import TrackMatcher, TrackCluster
from cue_parser import parse_cue_file
from ui import run_review_ui

QUEUE_FILE = Path(__file__).resolve().parent / ".incoming_queue.txt"
LOCK_FILE = Path(__file__).resolve().parent / ".incoming_lock"


def enqueue_and_aggregate(incoming_paths: List[str], initial_wait_ms: int = 350, **kwargs) -> Optional[List[str]]:
    """
    Collects paths from rapid sequential or concurrent launches (e.g. foo_run).
    Returns the aggregated list of paths for the coordinator process,
    or None for secondary processes that should exit.
    """
    if not incoming_paths:
        return []

    # Append our paths to the queue file with retries for Windows concurrent access
    appended = False
    for _ in range(6):
        try:
            with open(QUEUE_FILE, "a", encoding="utf-8") as f:
                for p in incoming_paths:
                    p_clean = p.strip().strip('"')
                    if p_clean:
                        f.write(p_clean + "\n")
            appended = True
            break
        except Exception:
            time.sleep(0.04)

    if not appended:
        return incoming_paths

    # Try to become the primary coordinator by creating LOCK_FILE atomically
    is_coordinator = False
    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        is_coordinator = True
    except FileExistsError:
        # Check if lock file is stale (> 4 seconds old)
        try:
            if time.time() - LOCK_FILE.stat().st_mtime > 4.0:
                LOCK_FILE.unlink(missing_ok=True)
                fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                is_coordinator = True
        except Exception:
            pass

    if not is_coordinator:
        # Secondary process: paths queued, exit cleanly
        return None

    # Coordinator process: dynamically wait until no new paths are arriving
    time.sleep(initial_wait_ms / 1000.0)
    last_count = 0
    for _ in range(15):  # Max 1.5 seconds wait
        try:
            if QUEUE_FILE.exists():
                with open(QUEUE_FILE, "r", encoding="utf-8") as f:
                    curr_count = sum(1 for line in f if line.strip())
                if curr_count == last_count and curr_count > 0:
                    break
                last_count = curr_count
            time.sleep(0.12)
        except Exception:
            break

    # Read all aggregated paths
    all_paths = []
    try:
        if QUEUE_FILE.exists():
            with open(QUEUE_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip().strip('"')
                    if line and line not in all_paths:
                        all_paths.append(line)
            QUEUE_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    finally:
        LOCK_FILE.unlink(missing_ok=True)

    return all_paths if all_paths else incoming_paths


def get_windows_clipboard_paths() -> List[str]:
    """
    Extracts file paths from Windows clipboard, supporting:
    1. CF_HDROP (files copied in Explorer or foobar)
    2. CF_UNICODETEXT / file:// URLs
    """
    paths = []
    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32

    CF_HDROP = 15
    CF_UNICODETEXT = 13

    if user32.OpenClipboard(None):
        try:
            # 1. Check CF_HDROP (native file drag/copy list)
            hDrop = user32.GetClipboardData(CF_HDROP)
            if hDrop:
                count = shell32.DragQueryFileW(hDrop, 0xFFFFFFFF, None, 0)
                for i in range(count):
                    buf = ctypes.create_unicode_buffer(2048)
                    shell32.DragQueryFileW(hDrop, i, buf, 2048)
                    p = buf.value.strip()
                    if p and Path(p).exists():
                        paths.append(p)

            # 2. Check CF_UNICODETEXT if no HDROP files
            if not paths:
                hMem = user32.GetClipboardData(CF_UNICODETEXT)
                if hMem:
                    kernel32.GlobalLock.restype = ctypes.c_void_p
                    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
                    pData = kernel32.GlobalLock(hMem)
                    if pData:
                        text = ctypes.c_wchar_p(pData).value or ""
                        kernel32.GlobalUnlock(hMem)
                        for line in text.splitlines():
                            line = line.strip().strip('"')
                            if not line:
                                continue
                            if line.lower().startswith("file://"):
                                line = unquote(line[7:])
                                if line.startswith("/") and len(line) > 2 and line[2] == ":":
                                    line = line[1:]
                            if Path(line).exists():
                                paths.append(line)
        finally:
            user32.CloseClipboard()

    return paths


def parse_input_paths(args: List[str]) -> List[str]:
    """Parses input paths, expanding any list files or directory trees."""
    paths = []
    for arg in args:
        clean = arg.strip().strip('"')
        if not clean:
            continue
        p = Path(clean)
        if not p.exists():
            continue

        # If a text file is passed containing a list of paths
        if p.suffix.lower() == '.txt':
            try:
                with open(p, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        line = line.strip().strip('"')
                        if line and Path(line).exists():
                            paths.append(line)
            except Exception:
                pass
            continue

        # If it's a directory
        if p.is_dir():
            for root, _, files in os.walk(p):
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext in AUDIO_EXTENSIONS or ext == '.cue':
                        paths.append(os.path.join(root, f))
            continue

        paths.append(str(p))

    return paths


def cmd_index():
    print("=" * 60)
    print("FOOBAR2000 MUSIC LIBRARY INDEXER")
    print("=" * 60)
    print("Scanning configured library directories...")

    indexer = LibraryIndexer()

    def progress(current, total):
        pct = (current / total) * 100 if total > 0 else 100
        print(f"\rIndexing new/updated files: {current}/{total} ({pct:.1f}%)", end="", flush=True)

    stats = indexer.scan_and_update(progress_callback=progress)
    print("\n" + "=" * 60)
    print("Index complete!")
    print(f" • Total tracks in library : {stats['scanned']}")
    print(f" • Newly indexed / updated : {stats['updated']}")
    print(f" • Deleted from index      : {stats['deleted']}")
    print("=" * 60)


def cmd_check(target_paths: List[str]):
    resolved_paths = parse_input_paths(target_paths)
    if not resolved_paths:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            "No Tracks Found",
            "No audio files or track paths were detected.\n\n"
            "To check tracks from foobar2000:\n"
            "• Right-click selected tracks -> Run -> Check Duplicates & Upgrades\n"
            "• Or right-click selected tracks -> Copy -> Copy File Path(s), then run Check_From_Clipboard\n"
            "• Or drag audio files directly onto Check_Dropped_Files.bat"
        )
        root.destroy()
        return

    indexer = LibraryIndexer()
    matcher = TrackMatcher(indexer)

    clusters: List[TrackCluster] = []
    analyzed_tracks: List[Dict[str, Any]] = []

    for path_str in resolved_paths:
        p = Path(path_str)
        ext = p.suffix.lower()

        # Handle CUE sheets
        if ext == '.cue':
            cue_tracks = parse_cue_file(path_str)
            for track_meta in cue_tracks:
                analyzed_tracks.append(track_meta)
                cluster = matcher.match_track(track_meta)
                if cluster:
                    clusters.append(cluster)
            continue

        # Handle single audio files
        if ext in AUDIO_EXTENSIONS:
            meta = extract_file_metadata(path_str)
            if meta:
                analyzed_tracks.append(meta)
                cluster = matcher.match_track(meta)
                if cluster:
                    clusters.append(cluster)

    total_count = len(analyzed_tracks) if analyzed_tracks else len(resolved_paths)
    run_review_ui(clusters, total_incoming=total_count, analyzed_tracks=analyzed_tracks)


def main():
    raw_args = sys.argv[1:]

    # Command: index
    if raw_args and raw_args[0].lower() == "index":
        cmd_index()
        return

    # Strip optional 'check' sub-command
    if raw_args and raw_args[0].lower() == "check":
        raw_args = raw_args[1:]

    # Check for clipboard mode
    if "--clipboard" in raw_args:
        cb_paths = get_windows_clipboard_paths()
        cmd_check(cb_paths)
        return

    # If file paths were passed (e.g. from foo_run or drag-and-drop)
    # Aggregate multiple invocations into a single batch
    aggregated = enqueue_and_aggregate(raw_args, initial_wait_ms=350)
    if aggregated is None:
        # Secondary process in a multi-call sequence (foo_run); exit cleanly
        return

    cmd_check(aggregated)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log_file = Path(__file__).resolve().parent / "crash_log.txt"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\nCRASH AT {time.ctime()}:\n")
            traceback.print_exc(file=f)
        try:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror("Dedupe Assistant Error", f"An error occurred:\n\n{e}\n\nDetails saved to:\n{log_file}")
            r.destroy()
        except Exception:
            pass
