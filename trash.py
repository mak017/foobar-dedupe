import os
import subprocess
import ctypes
from ctypes import wintypes
from typing import List, Tuple
from pathlib import Path

from config import FOOBAR_EXE
from indexer import get_db_connection


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ('hwnd', wintypes.HWND),
        ('wFunc', wintypes.UINT),
        ('pFrom', wintypes.LPCWSTR),
        ('pTo', wintypes.LPCWSTR),
        ('fFlags', wintypes.WORD),
        ('fAnyOperationsAborted', wintypes.BOOL),
        ('hNameMappings', wintypes.LPVOID),
        ('lpszProgressTitle', wintypes.LPCWSTR)
    ]


FO_DELETE = 3
FOF_ALLOWUNDO = 0x0040          # Preserves undo (sends to Windows Recycle Bin)
FOF_NOCONFIRMATION = 0x0010     # Do not prompt with Windows confirmation dialog
FOF_SILENT = 0x0004             # Do not show Windows progress dialog


def send_to_recycle_bin(file_path: str) -> bool:
    """Safely moves a single file to Windows Recycle Bin."""
    if not os.path.exists(file_path):
        return False

    abs_path = os.path.abspath(file_path)
    # SHFileOperation requires double null-terminated string
    path_buffer = abs_path + '\0\0'

    fileop = SHFILEOPSTRUCTW()
    fileop.hwnd = None
    fileop.wFunc = FO_DELETE
    fileop.pFrom = path_buffer
    fileop.pTo = None
    fileop.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT

    res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(fileop))
    return res == 0 and not fileop.fAnyOperationsAborted


def batch_delete_to_trash(file_paths: List[str]) -> Tuple[List[str], List[str]]:
    """
    Moves a list of files to Recycle Bin.
    Returns (successful_paths, failed_paths).
    """
    successful = []
    failed = []

    for path in file_paths:
        try:
            if send_to_recycle_bin(path):
                successful.append(path)
            else:
                failed.append(path)
        except Exception:
            failed.append(path)

    # Clean up successfully deleted files from the SQLite library index
    if successful:
        conn = get_db_connection()
        with conn:
            conn.executemany("DELETE FROM tracks WHERE path = ?", [(p,) for p in successful])
        conn.close()

    return successful, failed


def sync_foobar_playlist():
    """Signals foobar2000 to purge missing / dead items from the active playlist."""
    if os.path.exists(FOOBAR_EXE):
        try:
            subprocess.run([FOOBAR_EXE, '/command:Edit/Remove Dead Items'], check=False, timeout=3)
        except Exception:
            pass
