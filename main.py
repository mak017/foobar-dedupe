import os
import sys
import argparse
from pathlib import Path
from typing import List, Dict, Any

from config import AUDIO_EXTENSIONS
from indexer import LibraryIndexer, extract_file_metadata
from matcher import TrackMatcher, TrackCluster
from cue_parser import parse_cue_file
from ui import run_review_ui


def parse_input_paths(args: List[str]) -> List[str]:
    """Parses input paths, expanding any list files or directory trees."""
    paths = []
    for arg in args:
        p = Path(arg.strip('"'))
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
    print(f"Index complete!")
    print(f" • Total tracks in library : {stats['scanned']}")
    print(f" • Newly indexed / updated : {stats['updated']}")
    print(f" • Deleted from index      : {stats['deleted']}")
    print("=" * 60)


def cmd_check(target_paths: List[str]):
    resolved_paths = parse_input_paths(target_paths)
    if not resolved_paths:
        print("No valid audio files or CUE sheets provided.")
        return

    print(f"Analyzing {len(resolved_paths)} item(s)...")

    indexer = LibraryIndexer()
    matcher = TrackMatcher(indexer)

    clusters: List[TrackCluster] = []

    for path_str in resolved_paths:
        p = Path(path_str)
        ext = p.suffix.lower()

        # Handle CUE sheets
        if ext == '.cue':
            cue_tracks = parse_cue_file(path_str)
            for track_meta in cue_tracks:
                cluster = matcher.match_track(track_meta)
                if cluster:
                    clusters.append(cluster)
            continue

        # Handle single audio files
        if ext in AUDIO_EXTENSIONS:
            meta = extract_file_metadata(path_str)
            if meta:
                cluster = matcher.match_track(meta)
                if cluster:
                    clusters.append(cluster)

    print(f"Found {len(clusters)} track(s) with potential duplicates/upgrades.")
    print("Opening review window...")
    run_review_ui(clusters)


def get_clipboard_paths() -> List[str]:
    """Reads paths from Windows clipboard."""
    import tkinter as tk
    try:
        r = tk.Tk()
        r.withdraw()
        text = r.clipboard_get()
        r.destroy()
    except Exception:
        return []

    lines = [line.strip().strip('"') for line in text.splitlines() if line.strip()]
    valid_paths = [line for line in lines if Path(line).exists()]
    return valid_paths


def main():
    parser = argparse.ArgumentParser(description="foobar2000 Duplicate & Upgrade Assistant")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Command: index
    subparsers.add_parser("index", help="Scan and update the library index database")

    # Command: check
    check_parser = subparsers.add_parser("check", help="Check tracks for duplicates and upgrades")
    check_parser.add_argument("paths", nargs="*", default=[], help="File paths, CUE files, folders, or @list.txt")
    check_parser.add_argument("--clipboard", action="store_true", help="Read file paths from Windows clipboard")

    args = parser.parse_args()

    if args.command == "index":
        cmd_index()
    elif args.command == "check":
        target_paths = list(args.paths)
        if args.clipboard:
            cb_paths = get_clipboard_paths()
            target_paths.extend(cb_paths)
        if not target_paths:
            print("No paths provided. Use paths or --clipboard.")
            return
        cmd_check(target_paths)
    else:
        # Default: if arguments passed without subcommand, treat as check
        if len(sys.argv) > 1:
            cmd_check(sys.argv[1:])
        else:
            parser.print_help()


if __name__ == "__main__":
    main()
