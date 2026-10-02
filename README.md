# foobar2000 Duplicate & Upgrade Assistant

A fast, intelligent duplicate detector and upgrade engine tailored for large music collections and electronic music (EDM, Trance, House, Techno, DJ mixes).

---

## Key Features

1. **In-Place Inspection**: No need to move files around into temporary folders. Tracks are inspected wherever they live on disk.
2. **Quality & Duration Upgrade Engine**:
   - Detects if an incoming track is an **Extended/Club Mix** replacing an old **Radio Edit** ($\Delta > 25\text{s}$).
   - Detects if an incoming track is **Lossless (FLAC)** replacing an old **Lossy (MP3/AAC)** version.
   - Detects if an incoming track is **Higher Bitrate** (e.g. 320k vs 192k).
   - Detects and protects **Different Remixes** (e.g. *Original Mix* vs *Dub Mix* or *Remixer X* vs *Remixer Y*) so alternative versions are never deleted by accident.
3. **DJ Mix `.CUE` Parser**:
   - Parses `.cue` files directly, calculating track durations from timestamps.
   - Compares every cue track against your 68k library to highlight which tracks to extract and which to skip.
4. **Grouped Multi-Candidate Review UI**:
   - Dark-mode Tkinter GUI matching foobar's DarkOne theme.
   - Displays incoming tracks as clusters with all matching library candidates nested underneath.
   - Independent checkboxes with pre-checked safe recommendations.
5. **Safe Deletion & Foobar Playlist Sync**:
   - Moves deleted files to the **Windows Recycle Bin** (via native Windows Shell API, fully undoable).
   - Automatically signals foobar2000 (`Edit/Remove Dead Items`) to instantly purge deleted files from your active playlist.
   - Updates the local SQLite index immediately.

---

## File Structure

- `config.py`: Configuration settings (library folders, thresholds, foobar executable path).
- `normalizer.py`: Smart artist, title, collaboration (`feat.`, `pres.`, `vs.`), and mix type extractor.
- `indexer.py`: SQLite database manager (`library.db`) with multi-threaded mutagen scanning.
- `matcher.py`: Comparison and cluster evaluation engine.
- `cue_parser.py`: `.cue` file parser with track duration calculation.
- `trash.py`: Safe Windows Recycle Bin deletion and foobar playlist synchronizer.
- `ui.py`: Interactive dark-themed review GUI.
- `main.py`: CLI entrypoint.
- `Update_Library_Index.bat`: Double-click to scan and update the index.
- `Check_From_Clipboard.bat`: Launch review on paths currently in Windows clipboard.
- `Check_Dropped_Files.bat`: Drag-and-drop files or folders directly onto this script.

---

## First-Time Setup: Indexing Your Library

1. Double-click `Update_Library_Index.bat` (or run `python main.py index`).
2. The indexer will scan `I:\Music` and build `library.db`.
3. Subsequent runs check file modification timestamps (`mtime`) and take only 1–2 seconds to update.

---

## Using with foobar2000

### Method 1: Via Windows Clipboard (Zero Configuration)
1. In foobar2000, select any number of tracks in your playlist.
2. Press `Ctrl+C` (or right-click -> `Copy`).
3. Run `Check_From_Clipboard.bat` (or bind it to a Windows hotkey).
4. The review window pops up with side-by-side comparisons and pre-checked recommendations.
5. Review, adjust checkboxes if desired, and click **Confirm & Move to Recycle Bin**.
6. Foobar's playlist is automatically cleaned of the deleted items!

### Method 2: Via `foo_run` (Native Right-Click Context Menu)
1. Install the official `foo_run` component in foobar2000 (`foobar2000.exe -> Preferences -> Components`).
2. Go to `Preferences -> Tools -> Run services`.
3. Add a new service:
   - **Label**: `Check Duplicates & Upgrades`
   - **Path**: `pythonw.exe "c:\Users\USER\Documents\dev\foobar-dedupe\main.py" check "%path%"`
4. Now, simply right-click any selected tracks in foobar2000 -> `Run -> Check Duplicates & Upgrades`.

### Method 3: Drag and Drop
- Drag any audio file(s), folders, or `.cue` files and drop them directly onto `Check_Dropped_Files.bat`.
