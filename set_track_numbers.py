#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A tool to set the Track Number and Track Total for all audio files
in a folder.

Track Total = number of audio files found in the folder.

Track Number = the RELATIVE order (rank) of each file among the files
that are actually present - NOT the literal number written in the
filename. This matters when a playlist is incomplete, for example:

    - Folder has files "30", "31", ..., "39" (10 files, episodes
      1-29 were never downloaded)
      -> Track numbers become 1, 2, 3, ..., 10 (not 30-39)

    - Folder has files "00", "01", ..., "100" (101 files, complete
      set starting at 0)
      -> Track numbers become 1, 2, 3, ..., 101

Both cases are handled by the same logic automatically: the detected
number is only used to work out each file's position relative to the
others, then that position (1..N) becomes the actual track number.

SKIPPING ALREADY-CORRECT FILES:
Before touching a file, the tool reads its current Track Number and
Track Total. If they already match what this run would set them to
(same total file count AND same expected track number), the file is
left untouched. Only files that are missing tags or have the wrong
numbers get modified. This makes it safe to re-run the tool on a
folder after adding new files - only the new/incorrect ones change.

The track number is detected anywhere in the filename (start, middle,
or end), and the tool tells it apart from other numbers that might
appear in a filename, such as:
    - Years                  e.g. "Song (2019).mp3"        -> ignored
    - Bitrate / sample rate  e.g. "Song - 320kbps.mp3"      -> ignored
    - Disc / volume numbers  e.g. "CD1 - Song.mp3"          -> deprioritized
    - The actual track number, e.g.:
          "01 - Song.mp3"
          "Song - 01 - Artist.mp3"
          "Artist - Song (Track 07).mp3"
          "Song #12.mp3"

If a filename has no reliable number, that file keeps its place
according to the natural (alphabetical/numeric) sort order instead.

Supported formats: mp3, flac, ogg, m4a/mp4

Requirements:
    pip install mutagen --break-system-packages

Usage:
    python set_track_numbers.py "/path/to/folder"
"""

import os
import re
import sys

try:
    from mutagen import File as MutagenFile
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3, TRCK
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC
    from mutagen.oggvorbis import OggVorbis
    from mutagen.mp4 import MP4
except ImportError:
    print("The 'mutagen' library is required. Install it with:")
    print("    pip install mutagen --break-system-packages")
    sys.exit(1)

AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".m4a", ".mp4"}

# Words that, right before a number, strongly suggest it IS the track number
TRACK_HINT_RE = re.compile(r"(track|trk|#)\s*$", re.IGNORECASE)

# Words that, right before a number, suggest it's a disc/volume/part number
# instead of a track number (lower priority, but still usable as fallback)
DISC_HINT_RE = re.compile(r"(disc|cd|vol(?:ume)?|part)\s*$", re.IGNORECASE)

# Units that, right after a number (no separator), mean it's NOT a track
# number (bitrate, sample rate, duration, tempo, etc.)
UNIT_HINT_RE = re.compile(
    r"^(kbps|khz|hz|kbit|kb|mb|gb|bit|bits|min|sec|fps|bpm)\b", re.IGNORECASE
)


def natural_key(text):
    """Natural sort key so that '2' comes before '10', etc."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", text)
    ]


def extract_track_number(filename):
    """
    Intelligently find the track number inside a filename, wherever it
    appears (start, middle, or end), and tell it apart from other
    numbers such as years, bitrates, or disc/volume numbers.

    Works by scoring every number found in the filename and picking
    the most likely candidate. Returns None if no usable number exists.
    This value is used only to determine relative order between files,
    never written to the file as-is.
    """
    name = os.path.splitext(filename)[0]  # ignore the file extension
    matches = list(re.finditer(r"\d+", name))
    if not matches:
        return None

    best_score = None
    best_value = None
    best_start = None

    for match in matches:
        digits = match.group()
        start, end = match.span()
        value = int(digits)
        length = len(digits)
        before = name[max(0, start - 6):start]
        after = name[end:end + 6]

        score = 0

        # Explicit "track"/"trk"/"#" label right before the number
        if TRACK_HINT_RE.search(before):
            score += 100

        # Typical track numbers are short (1-3 digits)
        if length <= 3:
            score += 10

        # 4-digit numbers that look like a year are almost never track numbers
        if length == 4 and 1900 <= value <= 2099:
            score -= 60

        # A unit right after the number means it's a bitrate/duration/etc.
        if UNIT_HINT_RE.match(after):
            score -= 100

        # Disc/volume/part numbers are not track numbers
        if DISC_HINT_RE.search(before):
            score -= 40

        # Slight preference for numbers that appear earlier in the name,
        # since track numbers are conventionally placed near the start
        score += max(0, 20 - start)

        if (
            best_score is None
            or score > best_score
            or (score == best_score and start < best_start)
        ):
            best_score = score
            best_value = value
            best_start = start

    # A score of 0 or below means every number we found looked more like
    # a year, a bitrate, or some other non-track number than an actual
    # track number - treat it as "not found" instead of guessing wrong.
    if best_score is not None and best_score <= 0:
        return None

    return best_value


def get_audio_files(folder):
    # Collect only supported audio files and sort them naturally by name
    files = [
        f for f in os.listdir(folder)
        if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
        and os.path.isfile(os.path.join(folder, f))
    ]
    files.sort(key=natural_key)
    return files


def rank_files(files):
    """
    Work out the final, relative track number (1..N) for every file.

    The detected number inside each filename is used only to sort the
    files into the correct order - the actual value written as the
    track number is always the file's RANK in that order, never the
    raw number from the filename. This is what makes an incomplete
    playlist like files "30".."39" become Track 1..10, while a
    complete playlist "00".."100" becomes Track 1..101 (matching the
    real numbers, since ranks and real numbers line up when nothing
    is missing).

    Files with no detected number keep their place in the natural
    (alphabetical/numeric) sort order relative to the others.
    """
    detected = [extract_track_number(f) for f in files]

    indexed = list(enumerate(zip(files, detected)))

    def sort_key(entry):
        idx, (filename, number) = entry
        if number is not None:
            return (0, number, idx)
        return (1, natural_key(filename), idx)

    ordered = sorted(indexed, key=sort_key)

    # rank[original_index] = final track number
    rank = {}
    for position, (idx, _) in enumerate(ordered, start=1):
        rank[idx] = position

    return [rank[i] for i in range(len(files))]


def get_current_track_mp3(path):
    try:
        audio = EasyID3(path)
    except Exception:
        return None
    values = audio.get("tracknumber")
    if not values:
        return None
    text = str(values[0])
    num_part, _, total_part = text.partition("/")
    try:
        num = int(num_part.strip())
    except (ValueError, TypeError):
        return None
    total = None
    if total_part:
        try:
            total = int(total_part.strip())
        except ValueError:
            total = None
    return (num, total)


def get_current_track_flac(path):
    audio = FLAC(path)
    num_values = audio.get("tracknumber")
    total_values = audio.get("tracktotal")
    if not num_values:
        return None
    try:
        num = int(str(num_values[0]).strip())
    except ValueError:
        return None
    total = None
    if total_values:
        try:
            total = int(str(total_values[0]).strip())
        except ValueError:
            total = None
    return (num, total)


def get_current_track_ogg(path):
    audio = OggVorbis(path)
    num_values = audio.get("tracknumber")
    total_values = audio.get("tracktotal")
    if not num_values:
        return None
    try:
        num = int(str(num_values[0]).strip())
    except ValueError:
        return None
    total = None
    if total_values:
        try:
            total = int(str(total_values[0]).strip())
        except ValueError:
            total = None
    return (num, total)


def get_current_track_mp4(path):
    audio = MP4(path)
    trkn = audio.get("trkn")
    if not trkn:
        return None
    num, total = trkn[0]
    return (num, total if total else None)


def get_current_track_info(path):
    """
    Read the file's existing Track Number / Track Total, if any.
    Returns (track_num, track_total) or None if unreadable/missing.
    Any error while reading is treated as "no existing tag".
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".mp3":
            return get_current_track_mp3(path)
        elif ext == ".flac":
            return get_current_track_flac(path)
        elif ext == ".ogg":
            return get_current_track_ogg(path)
        elif ext in (".m4a", ".mp4"):
            return get_current_track_mp4(path)
    except Exception:
        return None
    return None


def set_track_mp3(path, track_num, total):
    # Load the MP3 with an EasyID3-compatible tag interface. If the file
    # has no ID3 tag at all yet, add an empty one first - doing this on
    # the same MP3 object (instead of re-opening EasyID3 separately)
    # avoids trying to read a tag that hasn't been saved to disk yet.
    audio = MP3(path, ID3=EasyID3)
    if audio.tags is None:
        audio.add_tags()
    audio["tracknumber"] = f"{track_num}/{total}"
    audio.save()


def set_track_flac(path, track_num, total):
    audio = FLAC(path)
    audio["tracknumber"] = str(track_num)
    audio["tracktotal"] = str(total)
    audio.save()


def set_track_ogg(path, track_num, total):
    audio = OggVorbis(path)
    audio["tracknumber"] = str(track_num)
    audio["tracktotal"] = str(total)
    audio.save()


def set_track_mp4(path, track_num, total):
    # MP4/M4A stores track number as a (track, total) tuple
    audio = MP4(path)
    audio["trkn"] = [(track_num, total)]
    audio.save()


def set_track_number(path, track_num, total):
    # Dispatch to the correct handler based on file extension
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mp3":
        set_track_mp3(path, track_num, total)
    elif ext == ".flac":
        set_track_flac(path, track_num, total)
    elif ext == ".ogg":
        set_track_ogg(path, track_num, total)
    elif ext in (".m4a", ".mp4"):
        set_track_mp4(path, track_num, total)
    else:
        raise ValueError(f"Unsupported format: {ext}")


def main():
    if len(sys.argv) > 1:
        folder = sys.argv[1]
    else:
        folder = input("Enter the folder path: ").strip().strip('"')

    if not os.path.isdir(folder):
        print("Invalid path or folder does not exist.")
        sys.exit(1)

    files = get_audio_files(folder)
    total = len(files)

    if total == 0:
        print("No supported audio files were found in this folder.")
        sys.exit(0)

    track_numbers = rank_files(files)

    print(f"Found {total} audio file(s). Checking existing tags...\n")

    updated = 0
    skipped = 0
    failed = 0

    for filename, track_num in sorted(zip(files, track_numbers), key=lambda x: x[1]):
        full_path = os.path.join(folder, filename)

        current = get_current_track_info(full_path)
        if current is not None and current == (track_num, total):
            print(f"[skip]   {filename}  (already Track {track_num}/{total})")
            skipped += 1
            continue

        try:
            set_track_number(full_path, track_num, total)
            print(f"[fixed]  {filename}  ->  Track {track_num}/{total}")
            updated += 1
        except Exception as e:
            print(f"[!] Failed to update {filename}: {e}")
            failed += 1

    print(f"\nDone. Updated: {updated}, already correct (skipped): {skipped}, failed: {failed}.")


if __name__ == "__main__":
    main()
