import os
from pathlib import Path

# Base directories
BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "library.db"

# Music library folders to index
LIBRARY_DIRS = [
    r"I:\Music",
]

# Supported audio file extensions
AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".aac", ".wav", ".alac", ".ogg", ".opus"
}

# foobar2000 executable path
FOOBAR_EXE = r"C:\Program Files (x86)\foobar2000\foobar2000.exe"

# Duration thresholds (in seconds)
DURATION_DELTA_UPGRADE = 25.0  # If candidate is > 25s longer, considered extended/club mix
DURATION_DELTA_SAME = 15.0     # If within 15s, considered the same track version

# Bitrate thresholds (in kbps)
BITRATE_DELTA_UPGRADE = 60     # Noticeable quality upgrade (e.g. 320 vs 256/192)

# Fuzzy match threshold (0-100)
FUZZY_MATCH_THRESHOLD = 75
