import hashlib
import logging
import queue
import secrets
import tempfile
import threading
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import config as C, db, zophar
from .convert import Album, natural_title, NoPlayableFiles, album_dir, convert_emu, convert_mp3, safe_extract

log = logging.getLogger("uvicorn.error")   # shows up in `docker logs`
jobs: "queue.Queue[int]" = queue.Queue()


def trigger_scan() -> str:
    """Ask Navidrome to rescan. Returns a note for the job detail; never raises."""
    if not (C.ND_URL and C.ND_USER):
        return ""
    salt = secrets.token_hex(6)
    token = hashlib.md5((C.ND_PASS + salt).encode()).hexdigest()  # Subsonic token auth requires md5
    try:
        r = requests.get(f"{C.ND_URL}/rest/startScan", timeout=30,
                         params={"u": C.ND_USER, "t": token, "s": salt, "v": "1.16.1", "c": "zophar", "f": "json"})
        body = r.json().get("subsonic-response", {})
        return "" if body.get("status") == "ok" else f"Navidrome scan failed: {body.get('error', r.status_code)}"
    except Exception as e:  # converted files are already on disk; scan failure isn't a job failure
        return f"Navidrome scan failed: {e}"


def _source_order(g: zophar.GamePage, page_url: str) -> list[str]:
    have = [k for k in ("emu", "mp3") if k in g.downloads]
    gme_ok = urlparse(page_url).path.rstrip("/").split("/")[-2].endswith(C.GME_SLUG_SUFFIXES)
    if C.PREFER == "mp3" or (C.PREFER == "auto" and not gme_ok):
        order = ["mp3", "emu"]
    else:
        order = ["emu", "mp3"]
    # 'auto' never falls back to formats we can't render
    return [k for k in order if k in have and (k == "mp3" or gme_ok)]


def _fetch_and_convert(kind: str, url: str, album: Album, jid: int) -> int:
    db.run("UPDATE jobs SET status='downloading', detail=? WHERE id=?", (f"{kind} zip", jid))
    with tempfile.TemporaryDirectory(dir=C.WORK_DIR) as tmp:
        tmp = Path(tmp)
        archive = tmp / "a.zip"
        zophar.download_file(url, archive)
        if not zipfile.is_zipfile(archive):
            raise RuntimeError("download is not a zip")
        with zipfile.ZipFile(archive) as zf:
            safe_extract(zf, tmp / "x")
        archive.unlink()
        db.run("UPDATE jobs SET status='converting' WHERE id=?", (jid,))
        conv = convert_emu if kind == "emu" else convert_mp3
        return conv(tmp / "x", album, C.MUSIC_ROOT,
                    progress=lambda k: db.run("UPDATE jobs SET tracks=? WHERE id=?", (k, jid)))


def _process(jid: int) -> str:
    job = db.one("SELECT url,title,system FROM jobs WHERE id=?", (jid,))
    db.run("UPDATE jobs SET status='downloading', detail='reading game page' WHERE id=?", (jid,))
    g = zophar.get_game(job["url"])
    album = Album(title=natural_title(g.title or job["title"]), system=g.system or job["system"],
                  year=g.year, developer=g.developer)
    dest = album_dir(album, C.MUSIC_ROOT)
    if dest.is_dir() and any(dest.iterdir()):
        db.run("UPDATE jobs SET tracks=? WHERE id=?", (sum(1 for _ in dest.iterdir()), jid))
        return "already in library"          # skip the 300 MB download
    order = _source_order(g, job["url"])
    if not order:
        raise RuntimeError("no usable download on the game page")
    last_err = None
    for kind in order:
        try:
            _fetch_and_convert(kind, g.downloads[kind][0], album, jid)
            break
        except NoPlayableFiles as e:   # e.g. EMU zip had nothing libgme can play: try the MP3s
            last_err = e
    else:
        raise last_err
    notes = []
    if C.COVER and not any((dest / n).exists() for n in ("cover.jpg", "cover.png")):
        try:   # Navidrome picks up cover.jpg / folder.jpg automatically
            if not g.cover_url:
                raise RuntimeError("no cover on page")
            r = None
            # Zophar serves thumbs_small and thumbs_large; prefer large, fall back to what the page gave us
            for url in dict.fromkeys([g.cover_url.replace("/thumbs_small/", "/thumbs_large/"), g.cover_url]):
                try:
                    r = zophar.polite_get(url)
                    break
                except Exception:
                    continue
            if r is None:
                raise RuntimeError("cover not downloadable")
            (dest / ("cover.png" if g.cover_url.lower().endswith(".png") else "cover.jpg")).write_bytes(r.content)
        except Exception:
            notes.append("no cover art")   # artwork is a nicety, never fail the job over it
    scan = trigger_scan()
    return "; ".join(n for n in notes + [scan] if n)


def _loop():
    while True:
        jid = jobs.get()
        try:
            note = _process(jid)
            db.run("UPDATE jobs SET status='done', detail=? WHERE id=?", (note, jid))
        except Exception as e:
            log.exception("job %s failed", jid)
            db.run("UPDATE jobs SET status='failed', detail=? WHERE id=?", (str(e)[:500], jid))


def start():
    for row in db.all_("SELECT id FROM jobs WHERE status='queued' ORDER BY id"):
        jobs.put(row["id"])
    threading.Thread(target=_loop, daemon=True).start()
