import os
from pathlib import Path

MUSIC_ROOT = Path(os.environ.get("MUSIC_ROOT", "/music/Zophar"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
ND_URL = os.environ.get("ND_URL", "")
ND_USER = os.environ.get("ND_USER", "")
ND_PASS = os.environ.get("ND_PASS", "")

# Looping policy: chip tunes loop forever, so render a fixed length and fade out.
TRACK_SECONDS = int(os.environ.get("TRACK_SECONDS", "180"))
FADE_SECONDS = int(os.environ.get("FADE_SECONDS", "6"))
# Upper bound when a file/playlist states its own length.
MAX_TRACK_SECONDS = int(os.environ.get("MAX_TRACK_SECONDS", "600"))
# Tracks whose audible part is shorter than this are skipped (sound effects, jingles). 0 turns the check off.
# Tracks that go silent before their planned length are also trimmed to where the sound stops.
MIN_TRACK_SECONDS = float(os.environ.get("MIN_TRACK_SECONDS", "20"))
# Effects that loop on their own (a beep repeating every second, say) never go quiet, so they are caught by their
# short repeat instead: a track whose sound repeats exactly within this many seconds is skipped. 0 turns it off.
MAX_LOOP_SECONDS = float(os.environ.get("MAX_LOOP_SECONDS", "10"))
# flac (big, lossless) or opus (small). Navidrome plays both.
OUTPUT_FORMAT = os.environ.get("OUTPUT_FORMAT", "flac").lower()

BASE_URL = "https://www.zophar.net"
USER_AGENT = "zophar-fetch/0.9.2 (self-hosted, personal use; one request per 2s)"
MIN_REQUEST_INTERVAL = float(os.environ.get("MIN_REQUEST_INTERVAL", "2"))

# Safety limits for untrusted archives.
MAX_ZIP_BYTES = int(os.environ.get("MAX_DOWNLOAD_MB", "1500")) * 1024 * 1024   # Zophar zips run to ~400 MB
MAX_EXTRACTED_BYTES = 3 * MAX_ZIP_BYTES   # total uncompressed
MAX_ZIP_FILES = 20000
MAX_TRACKS_PER_FILE = 256

# Formats libgme (via ffmpeg) can play
GME_EXTS = {".nsf", ".nsfe", ".spc", ".gbs", ".vgm", ".vgz", ".gym", ".hes", ".kss", ".ay", ".sap"}
# Formats that need vgmstream-cli (no longer used by default: the MP3 zip covers these) (PSF family etc). Optional.
VGMSTREAM_EXTS = {".minipsf", ".psf", ".usf", ".miniusf", ".gsf", ".minigsf", ".2sf", ".mini2sf",
                  ".ssf", ".minissf", ".dsf", ".minidsf", ".psf2", ".minipsf2"}

# Which Zophar download to use: "auto" = original files when libgme can render them, else Zophar's MP3s;
# "mp3" = always the MP3 zip; "emu" = original files only.
PREFER = os.environ.get("PREFER", "auto").lower()
# Console-slug endings libgme can render (from the game URL, e.g. .../nintendo-nes-nsf/...)
GME_SLUG_SUFFIXES = ("-nsf", "-spc", "-gbs", "-hes", "-kss", "-vgm")
WORK_DIR = Path(os.environ.get("WORK_DIR", str(DATA_DIR)))   # temp space: needs ~3x the zip size

COVER = os.environ.get("COVER", "1") == "1"          # save the game page's cover image as cover.jpg/png
MAX_BATCH = int(os.environ.get("MAX_BATCH", "100"))  # most games one "queue all results" click will queue
