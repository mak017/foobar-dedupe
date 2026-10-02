import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

import mutagen
from normalizer import extract_artist, extract_title_and_mix


def parse_cue_timestamp(ts: str) -> float:
    """Converts MM:SS:FF (75 frames/sec) to seconds."""
    parts = ts.strip().split(':')
    if len(parts) == 3:
        m, s, f = map(int, parts)
        return m * 60 + s + (f / 75.0)
    elif len(parts) == 2:
        m, s = map(int, parts)
        return m * 60 + s
    return 0.0


def format_seconds_to_cue(seconds: float) -> str:
    """Converts seconds back to MM:SS:FF."""
    m = int(seconds // 60)
    s = int(seconds % 60)
    f = int(round((seconds - int(seconds)) * 75))
    return f"{m:02d}:{s:02d}:{f:02d}"


def parse_cue_file(cue_path: str) -> List[Dict[str, Any]]:
    """
    Parses a .cue file and calculates track durations and metadata.
    """
    p = Path(cue_path)
    if not p.exists():
        return []

    # Read cue file with encoding detection
    content = ""
    for enc in ['utf-8-sig', 'utf-8', 'cp1251', 'cp1252', 'latin-1']:
        try:
            with open(cue_path, 'r', encoding=enc) as f:
                content = f.read()
            break
        except UnicodeDecodeError:
            continue

    if not content:
        return []

    lines = content.splitlines()

    global_performer = ""
    global_title = ""
    audio_file_name = ""
    tracks = []
    current_track: Optional[Dict[str, Any]] = None

    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue

        # Global or track performer
        perf_match = re.match(r'^PERFORMER\s+"?([^"]*)"?', line_clean, re.IGNORECASE)
        if perf_match:
            val = perf_match.group(1).strip()
            if current_track is None:
                global_performer = val
            else:
                current_track['artist'] = val
            continue

        # Global or track title
        title_match = re.match(r'^TITLE\s+"?([^"]*)"?', line_clean, re.IGNORECASE)
        if title_match:
            val = title_match.group(1).strip()
            if current_track is None:
                global_title = val
            else:
                current_track['title'] = val
            continue

        # Audio file reference
        file_match = re.match(r'^FILE\s+"?([^"]*)"?\s+(\w+)', line_clean, re.IGNORECASE)
        if file_match:
            audio_file_name = file_match.group(1).strip()
            continue

        # Track start
        track_match = re.match(r'^TRACK\s+(\d+)\s+AUDIO', line_clean, re.IGNORECASE)
        if track_match:
            if current_track is not None:
                tracks.append(current_track)
            current_track = {
                'track_num': int(track_match.group(1)),
                'artist': '',
                'title': '',
                'index01': 0.0,
                'index01_str': '00:00:00'
            }
            continue

        # Index 01 timestamp
        idx_match = re.match(r'^INDEX\s+01\s+([\d:]+)', line_clean, re.IGNORECASE)
        if idx_match and current_track is not None:
            ts_str = idx_match.group(1).strip()
            current_track['index01_str'] = ts_str
            current_track['index01'] = parse_cue_timestamp(ts_str)

    if current_track is not None:
        tracks.append(current_track)

    if not tracks:
        return []

    # Resolve audio file path
    audio_full_path = p.parent / audio_file_name if audio_file_name else None
    audio_total_duration = 0.0
    audio_format = "MP3"
    audio_bitrate = 320
    is_lossless = 0

    if audio_full_path and audio_full_path.exists():
        try:
            mut_file = mutagen.File(str(audio_full_path), easy=True)
            if mut_file and mut_file.info:
                audio_total_duration = mut_file.info.length
                audio_bitrate = int(getattr(mut_file.info, 'bitrate', 0) / 1000)
                ext = audio_full_path.suffix.lower()
                audio_format = ext.replace('.', '').upper()
                if ext in {'.flac', '.wav', '.alac'}:
                    is_lossless = 1
        except Exception:
            pass

    # Calculate duration for each track
    result = []
    for i, trk in enumerate(tracks):
        start_time = trk['index01']
        if i + 1 < len(tracks):
            end_time = tracks[i + 1]['index01']
            duration = max(0.0, end_time - start_time)
        else:
            if audio_total_duration > start_time:
                duration = audio_total_duration - start_time
            else:
                duration = 360.0  # Fallback estimate (6 min) if audio file duration unknown

        artist = trk['artist'] or global_performer or "Unknown Artist"
        title = trk['title'] or f"Track {trk['track_num']:02d}"

        artist_norm, _ = extract_artist(artist)
        title_norm, mix_name, mix_type = extract_title_and_mix(title)

        result.append({
            'path': f"{p}#{trk['track_num']:02d}",
            'filename': f"{trk['track_num']:02d} - {artist} - {title}",
            'artist': artist,
            'artist_norm': artist_norm,
            'title': title,
            'title_norm': title_norm,
            'mix_name': mix_name,
            'mix_type': mix_type,
            'duration': round(duration, 2),
            'bitrate': audio_bitrate,
            'format': audio_format,
            'is_lossless': is_lossless,
            'is_cue_track': True,
            'cue_path': str(p),
            'cue_track_num': trk['track_num'],
            'cue_start_time': start_time,
            'cue_end_time': start_time + duration,
            'cue_audio_file': str(audio_full_path) if audio_full_path else ""
        })

    return result
