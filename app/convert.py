"""Safe archive extraction, track discovery, rendering and tagging."""
import re
import shutil
import struct
import subprocess
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import config as C

log = logging.getLogger("zophar")


# ---------------------------------------------------------------- extraction
def safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    dest = dest.resolve()
    infos = zf.infolist()
    if len(infos) > C.MAX_ZIP_FILES:
        raise ValueError("too many files in archive")
    if sum(i.file_size for i in infos) > C.MAX_EXTRACTED_BYTES:
        raise ValueError("archive expands beyond size limit")
    for i in infos:
        target = (dest / i.filename).resolve()
        if not target.is_relative_to(dest):
            raise ValueError("unsafe path in archive")
        if (i.external_attr >> 16) & 0o170000 == 0o120000:  # symlink
            raise ValueError("symlink in archive")
    zf.extractall(dest)


# ------------------------------------------------------------ track metadata
@dataclass
class Meta:
    count: int = 1                      # number of subtracks, 0 = unknown (probe)
    titles: dict = field(default_factory=dict)   # 0-based track index -> title
    lengths: dict = field(default_factory=dict)  # 0-based track index -> seconds (only when the file says)
    game: str = ""
    artist: str = ""


def _text(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode("latin-1", "replace").strip()


def read_meta(path: Path) -> Meta:
    ext = path.suffix.lower()
    try:
        data = path.read_bytes()[:1_000_000]
    except OSError:
        return Meta(count=0)
    if ext == ".nsf" and data[:5] == b"NESM\x1a":
        return Meta(count=max(1, data[6]), game=_text(data[0x0E:0x2E]), artist=_text(data[0x2E:0x4E]))
    if ext == ".nsfe" and data[:4] == b"NSFE":
        return _read_nsfe(data)
    if ext == ".gbs" and data[:3] == b"GBS":
        return Meta(count=max(1, data[4]), game=_text(data[0x10:0x30]), artist=_text(data[0x30:0x50]))
    if ext == ".spc" and data[:27] == b"SNES-SPC700 Sound File Data" and len(data) > 0xD0:
        m = Meta(count=1)
        if data[0x23] == 26:  # has ID666 tag
            m.titles[0] = _text(data[0x2E:0x4E])
            m.game = _text(data[0x4E:0x6E])
            m.artist = _text(data[0xB1:0xD1])
            secs = _text(data[0xA9:0xAC])
            if secs.isdigit() and int(secs) > 0:
                m.lengths[0] = int(secs)
        return m
    if ext in {".vgm", ".vgz", ".gym"}:
        return Meta(count=1)
    return Meta(count=0)  # HES/KSS/AY/SAP/...: probe by rendering until it fails


def _read_nsfe(data: bytes) -> Meta:
    m, pos = Meta(count=1), 4
    while pos + 8 <= len(data):
        size, cid = struct.unpack_from("<I4s", data, pos)
        body = data[pos + 8: pos + 8 + size]
        if cid == b"INFO" and len(body) >= 10:
            m.count = max(1, body[8])
        elif cid == b"auth":
            parts = body.split(b"\0")
            m.game = parts[0].decode("utf-8", "replace") if parts else ""
            m.artist = parts[1].decode("utf-8", "replace") if len(parts) > 1 else ""
        elif cid == b"tlbl":
            for i, t in enumerate(body.split(b"\0")[: m.count]):
                if t:
                    m.titles[i] = t.decode("utf-8", "replace")
        elif cid == b"time":
            for i in range(min(len(body) // 4, m.count)):
                ms = struct.unpack_from("<i", body, i * 4)[0]
                if ms > 0:
                    m.lengths[i] = ms / 1000
        elif cid == b"NEND":
            break
        pos += 8 + size
    return m


_COMMA = re.compile(r"(?<!\\),")


def _parse_length(text: str):
    """'0:01:30' / '1:30' / '90' (GME playlists may add a trailing '-' for 'loops') -> seconds, or None."""
    t = text.strip().rstrip("-").strip()
    if not t:
        return None
    try:
        secs = 0.0
        for part in t.split(":"):
            secs = secs * 60 + float(part)
    except ValueError:
        return None
    return secs if secs > 0 else None


def parse_m3u(m3u: Path, audio_name: str):
    """GME-style playlist lines: `file.nsf::NSF,3,Title,0:01:30,...` -> ({2: 'Title'}, {2: 90.0}) (0-based)."""
    titles, lengths = {}, {}
    try:
        text = m3u.read_text("utf-8", "replace")
    except OSError:
        return titles, lengths
    for line in text.splitlines():
        if line.startswith("#") or "::" not in line:
            continue
        fname, rest = line.split("::", 1)
        if fname.strip().lower() != audio_name.lower():
            continue
        parts = [p.replace("\\,", ",") for p in _COMMA.split(rest)]
        if len(parts) >= 3 and parts[1].strip().lstrip("$").isdigit():
            n = int(parts[1].strip().lstrip("$"))
            if parts[2].strip():
                titles[n - 1] = parts[2].strip()
            if len(parts) >= 4 and (length := _parse_length(parts[3])):
                lengths[n - 1] = length
    return titles, lengths


def parse_m3u_titles(m3u: Path, audio_name: str) -> dict:
    return parse_m3u(m3u, audio_name)[0]


# ----------------------------------------------------------------- rendering
def _out_args(out: Path, tags: dict) -> list:
    args = []
    for k, v in tags.items():
        args += ["-metadata", f"{k}={v}"]
    if C.OUTPUT_FORMAT == "opus":
        return args + ["-c:a", "libopus", "-b:a", "128k", str(out)]
    return args + ["-c:a", "flac", str(out)]


def plan_duration(length=None):
    """(seconds to render, fade-out seconds). Chip tunes loop forever, so a fixed length is used unless the file
    or its playlist states one (then short jingles stay short)."""
    if length:
        dur = min(max(float(length), 1.0), float(C.MAX_TRACK_SECONDS))
        return dur, min(float(C.FADE_SECONDS), dur / 4)
    return float(C.TRACK_SECONDS), float(min(C.FADE_SECONDS, C.TRACK_SECONDS))


_SIL_START = re.compile(r"silence_start: (-?[\d.]+)")
_SIL_END = re.compile(r"silence_end: (-?[\d.]+)")


_TIME = re.compile(r"time=(\d+):(\d+):([\d.]+)")


def parse_audible(stderr: str, dur: float) -> float:
    """Seconds of real sound at the start of a track, from ffmpeg silencedetect output.
    libgme ends a subtrack by itself once it has been quiet for a few seconds, so the stream can be much
    shorter than `dur`: the real length comes from ffmpeg's last progress line. If the track goes quiet and
    stays quiet to its end, the answer is where the quiet began; otherwise the whole stream."""
    times = _TIME.findall(stderr)
    total = min(dur, int(times[-1][0]) * 3600 + int(times[-1][1]) * 60 + float(times[-1][2])) if times else dur
    starts = [max(0.0, float(m)) for m in _SIL_START.findall(stderr)]
    ends = [float(m) for m in _SIL_END.findall(stderr)]
    if starts and (len(starts) > len(ends) or ends[-1] >= total - 0.5):
        return min(starts[-1], total)
    return total


def probe_audible(src: Path, track: int, dur: float) -> float:
    """How much of the first `dur` seconds of a subtrack is audible. Chip tunes that play once and stop
    (sound effects, fanfares) come back short; looping music comes back as the full `dur`."""
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-stats", "-loglevel", "info",
           "-track_index", str(track), "-i", str(src), "-vn", "-t", f"{dur:g}",
           "-af", "silencedetect=noise=-55dB:d=2", "-f", "null", "-"]
    r = subprocess.run(cmd, check=True, capture_output=True)
    return parse_audible(r.stderr.decode("utf-8", "replace"), dur)


def _band_features(x, rate: int):
    """Loudness of 6 frequency bands every 20 ms (log scale). Noise-channel effects never repeat sample for sample,
    but their loudness-by-band pattern does, so this is what gets compared."""
    import numpy as np
    hop = rate // 50
    n = len(x) // hop
    frames = x[: n * hop].reshape(n, hop) * np.hanning(hop)
    spec = np.abs(np.fft.rfft(frames, axis=1))[:, 1:]
    return np.log1p(np.stack([b.mean(axis=1) for b in np.array_split(spec, 6, axis=1)], axis=1)), hop


def loop_period(samples, rate: int, max_seconds: float, threshold: float = 0.9):
    """Shortest repeat (seconds, at least 0.5) at which a mono signal's band-loudness pattern matches itself, if
    that is under `max_seconds`; else None. Looping effects score about 0.998; music, even with a steady beat,
    stays under 0.7 (measured on the original Legend of Zelda NSF)."""
    import numpy as np
    x = np.asarray(samples, dtype=np.float64)[int(2 * rate):]   # skip any lead-in
    if len(x) < rate * 4 or float(np.dot(x, x)) < 1e-6 * len(x):
        return None                                              # too short to judge, or silent
    feats, hop = _band_features(x, rate)
    feats = feats - feats.mean(axis=0)
    n = len(feats)
    lo, hi = int(0.5 * rate / hop), min(int(max_seconds * rate / hop), n // 2)
    for lag in range(lo, hi + 1):
        a, b = feats[: n - lag], feats[lag:]
        denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
        if denom > 1e-9 and float((a * b).sum()) / denom >= threshold:
            return lag * hop / rate
    return None


def short_loop_period(src: Path, track: int, dur: float, max_seconds: float):
    """loop_period() for one subtrack: decode up to 60 s as 8 kHz mono and look for a short exact repeat."""
    try:
        import numpy as np
    except Exception as e:                      # e.g. a numpy build the CPU can't run; fail loudly, never skip silently
        raise RuntimeError(f"loop check unavailable ({e}). Set MAX_LOOP_SECONDS=0 to switch it off.") from None
    rate = 8000
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-track_index", str(track), "-i", str(src), "-vn",
           "-t", f"{min(dur, 60):g}", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    return loop_period(np.frombuffer(raw, dtype="<i2").astype(np.float64), rate, max_seconds)


def render_gme(src: Path, out: Path, track: int, tags: dict, length=None, fade=None) -> None:
    dur, default_fade = plan_duration(length)
    fade = default_fade if fade is None else min(fade, dur)
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
           "-track_index", str(track), "-i", str(src), "-vn",
           "-t", f"{dur:g}",
           "-af", f"afade=t=out:st={max(0.0, dur - fade):g}:d={fade:g}"]
    subprocess.run(cmd + _out_args(out, tags), check=True, capture_output=True)


# -------------------------------------------------------------------- driver
class NoPlayableFiles(RuntimeError):
    pass


@dataclass
class Album:
    title: str
    system: str
    year: str = ""
    developer: str = ""


def natural_title(title: str) -> str:
    """'Legend of Zelda, The - A Link Between Worlds' -> 'The Legend of Zelda - A Link Between Worlds'"""
    m = re.match(r"^(.+?), (The|A|An)((?: - .*)?)$", title.strip())
    return f"{m.group(2)} {m.group(1)}{m.group(3)}" if m else title.strip()


def tidy_track_name(stem: str) -> str:
    """'BossGetFanfare.ry.32.dspadpcm' -> 'Boss Get Fanfare'; 'BGM_BARD_D_FIELD_DARK' -> 'BARD D FIELD DARK'.
    Readable and recognisable, not authoritative: Zophar only has the in-game filenames."""
    n = re.sub(r"(\.[A-Za-z]{1,3})?\.\d+\.[a-z]*adpcm$", "", stem, flags=re.I)
    n = re.sub(r"^BGM[_ -]", "", n)
    n = re.sub(r"[_\-]+", " ", n)
    n = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", n)      # camelCase -> words
    n = re.sub(r"\s+", " ", n).strip()
    return n or stem


def _clean(name: str) -> str:
    name = re.sub(r"[^\w\-.() &']", "_", name).strip(" .")
    return name[:120] or "Unknown"


def _natural(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(p))]


def _tags(album: Album, title: str, n: int, artist: str = "", game: str = "") -> dict:
    tags = {"album": album.title, "title": title, "track": str(n),
            "album_artist": album.developer or artist or "Various Artists",
            "artist": artist or album.developer or "Various Artists",
            "genre": album.system, "comment": "Zophar's Domain" + (f" ({game})" if game else "")}
    if album.year:
        tags["date"] = album.year
    return tags


def _publish(stage: Path, album: Album, album_root: Path) -> Path:
    """Move the finished album into place atomically: copy into a hidden sibling folder, then rename it.
    Navidrome (and our own 'already in library' check) therefore never sees a half-written album."""
    dest = album_dir(album, album_root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if any(dest.iterdir()):
            raise FileExistsError(f"{dest} already has files")
        dest.rmdir()
    tmp = dest.parent / f".incoming-{dest.name}"
    shutil.rmtree(tmp, ignore_errors=True)   # leftover from an interrupted run
    tmp.mkdir()
    try:
        for p in sorted(stage.iterdir()):
            shutil.move(str(p), tmp / p.name)
        tmp.rename(dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return dest


def convert_emu(extract_dir: Path, album: Album, album_root: Path, progress=lambda n: None) -> int:
    """Render original sound files (NSF/SPC/GBS/...) via ffmpeg+libgme. Returns track count."""
    files = sorted((f for f in extract_dir.rglob("*") if f.is_file() and f.suffix.lower() in C.GME_EXTS),
                   key=_natural)
    if not files:
        raise NoPlayableFiles("no files libgme can render in the archive")
    ext = ".opus" if C.OUTPUT_FORMAT == "opus" else ".flac"
    stage = extract_dir.parent / "out"
    stage.mkdir(exist_ok=True)
    n = skipped = 0
    for f in files:
        meta = read_meta(f)
        titles, lengths = dict(meta.titles), dict(meta.lengths)   # titles/lengths inside the file itself ...
        for m3u in f.parent.glob("*.m3u"):                         # ... overridden by a playlist if there is one
            t, l = parse_m3u(m3u, f.name)
            titles.update(t)
            lengths.update(l)
        tracks = range(meta.count) if meta.count else range(C.MAX_TRACKS_PER_FILE)
        for i in tracks:
            name = titles.get(i) or (f"Track {i + 1}" if len(files) == 1 else f"{f.stem} - Track {i + 1}")
            length, fade = lengths.get(i), None
            try:
                if C.MIN_TRACK_SECONDS > 0:
                    planned, _ = plan_duration(length)
                    audible = probe_audible(f, i, planned)
                    if audible < C.MIN_TRACK_SECONDS:
                        skipped += 1
                        continue   # a sound effect or jingle, not music worth a library entry
                    if audible < planned - 1:   # plays once and stops: end where the sound does
                        length, fade = audible + 1.5, 1.0
                    elif C.MAX_LOOP_SECONDS > 0 and (
                            period := short_loop_period(f, i, planned, C.MAX_LOOP_SECONDS)) is not None:
                        log.info("skipping %s track %d: sound repeats every %.1fs (an effect)", f.name, i + 1, period)
                        skipped += 1
                        continue
                out = stage / f"{n + 1:03d} - {_clean(name)}{ext}"
                render_gme(f, out, i, _tags(album, name, n + 1, meta.artist, meta.game), length, fade)
            except subprocess.CalledProcessError as e:
                if meta.count == 0 and i > 0:
                    break  # probed past the last subtrack
                raise RuntimeError(f"ffmpeg failed on {f.name} track {i + 1}: "
                                   f"{e.stderr.decode('utf-8', 'replace')[-300:]}")
            n += 1
            progress(n)
    if n == 0:
        raise NoPlayableFiles(f"every track was shorter than {C.MIN_TRACK_SECONDS:g}s or silent "
                              f"({skipped} skipped)")
    _publish(stage, album, album_root)
    return n


def mp3_seconds(path: Path):
    """Length of an audio file in seconds, or None if ffprobe can't tell."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                              str(path)], check=True, capture_output=True).stdout.decode().strip()
        return float(out)
    except (subprocess.CalledProcessError, ValueError):
        return None


def convert_mp3(extract_dir: Path, album: Album, album_root: Path, progress=lambda n: None) -> int:
    """Zophar's own MP3 renders: no re-encoding, just clean tags. Tracks shorter than MIN_TRACK_SECONDS
    (sound effects, jingles) are left out. Returns track count."""
    files = sorted((f for f in extract_dir.rglob("*.mp3") if f.is_file()), key=_natural)
    if not files:
        raise NoPlayableFiles("no MP3 files in the archive")
    stage = extract_dir.parent / "out"
    stage.mkdir(exist_ok=True)
    n = skipped = 0
    for f in files:
        if C.MIN_TRACK_SECONDS > 0 and (secs := mp3_seconds(f)) is not None and secs < C.MIN_TRACK_SECONDS:
            skipped += 1
            continue
        name = tidy_track_name(f.stem)
        out = stage / f"{n + 1:03d} - {_clean(name)}.mp3"
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(f), "-vn",
               "-map_metadata", "-1", "-c:a", "copy", "-id3v2_version", "3"]
        for k, v in _tags(album, name, n + 1).items():
            cmd += ["-metadata", f"{k}={v}"]
        try:
            subprocess.run(cmd + [str(out)], check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ffmpeg failed on {f.name}: {e.stderr.decode('utf-8', 'replace')[-300:]}")
        n += 1
        progress(n)
    if n == 0:
        raise NoPlayableFiles(f"every MP3 was shorter than {C.MIN_TRACK_SECONDS:g}s ({skipped} skipped)")
    _publish(stage, album, album_root)
    return n


def album_dir(album: Album, album_root: Path) -> Path:
    return album_root / _clean(album.system) / _clean(album.title)
