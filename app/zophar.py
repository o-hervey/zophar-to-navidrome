"""Everything that touches zophar.net lives here (verified against saved pages, Nov 2026).

Search:  GET /music/search?search=<term>[&search_consoleid=<id>]
         results are rows of table#gamelist; the link is `td.name a`, the console is `td.console`.
Game:    page has `#mass_download` with two zip links: "(MP3)" (rendered tracks) and "(EMU)"
         (original sound files: NSF/SPC/GBS/...), hosted on fi.zophar.net.
"""
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .config import BASE_URL, MAX_ZIP_BYTES, MIN_REQUEST_INTERVAL, USER_AGENT

ALLOWED_HOSTS = {"www.zophar.net", "zophar.net", "fi.zophar.net"}

# From the search page's console dropdown.
CONSOLES = {
    "0": "All", "24": "Amiga", "23": "Arcade", "25": "Atari 8-Bit", "26": "Atari ST", "28": "CD-i",
    "27": "Commodore 64", "29": "FM Towns", "3": "Game Boy", "4": "Game Boy Advance", "41": "Mobile",
    "30": "MS-DOS", "5": "MSX", "31": "MSX2", "15": "Nintendo 3DS", "10": "Nintendo 64",
    "16": "Nintendo DS", "19": "Nintendo GameCube", "1": "Nintendo NES", "2": "Nintendo SNES",
    "43": "Nintendo Switch", "14": "Nintendo Wii", "39": "Nintendo Wii U", "40": "PC", "32": "PC-88",
    "33": "PC-98", "34": "PC-9821", "9": "Playstation", "11": "Playstation 2", "12": "Playstation 3",
    "44": "Playstation 4", "13": "Playstation Portable", "42": "Playstation Vita",
    "20": "Sega Dreamcast", "6": "Sega Game Gear", "7": "Sega Master System",
    "8": "Sega Mega Drive / Genesis", "21": "Sega Saturn", "35": "Sharp X1", "36": "Spectrum",
    "22": "TurboGrafx-16", "37": "Windows", "38": "X68000", "17": "Xbox", "18": "Xbox 360",
}

_lock = threading.Lock()
_last = 0.0
_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT


def is_zophar_url(url: str) -> bool:
    p = urlparse(url)
    return p.scheme == "https" and p.hostname in ALLOWED_HOSTS


def polite_get(url, **kw):
    """Global rate limit across search + worker: one request per MIN_REQUEST_INTERVAL seconds."""
    if not is_zophar_url(url):
        raise ValueError(f"refusing non-Zophar URL: {url}")
    global _last
    with _lock:
        wait = MIN_REQUEST_INTERVAL - (time.time() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.time()
    r = _session.get(url, timeout=60, **kw)
    r.raise_for_status()
    return r


def clean_console(text: str) -> str:
    """'Nintendo NES (NSF)' -> 'Nintendo NES'"""
    return re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()


# ------------------------------------------------------------------- search
def parse_search(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for row in soup.select("table#gamelist tr.regularrow, table#gamelist tr.regularrow_image"):
        a = row.select_one("td.name a[href]")
        if not a:
            continue
        url = urljoin(BASE_URL, a["href"])
        if url in seen:
            continue
        seen.add(url)
        cons = row.select_one("td.console")
        year = row.select_one("td.year")
        out.append({
            "title": a.get_text(" ", strip=True),
            "system": clean_console(cons.get_text(" ", strip=True)) if cons else "",
            "year": year.get_text(strip=True) if year else "",
            "page_url": url,
        })
    return out


def search(term: str, console_id: str = "0") -> list[dict]:
    params = {"search": term}
    if console_id and console_id != "0":
        params["search_consoleid"] = console_id
    return parse_search(polite_get(f"{BASE_URL}/music/search", params=params).text)


# ---------------------------------------------------------------- game page
@dataclass
class GamePage:
    title: str = ""
    system: str = ""
    year: str = ""
    developer: str = ""
    cover_url: str = ""
    downloads: dict = field(default_factory=dict)   # 'mp3' / 'emu' -> (url, size_text)


def _info(soup, label):
    for span in soup.select("#music_info .infoname"):
        if span.get_text(strip=True).rstrip(":").lower() == label.lower():
            data = span.find_next_sibling("span", class_="infodata")
            return data.get_text(" ", strip=True) if data else ""
    return ""


def parse_game(html: str, page_url: str) -> GamePage:
    soup = BeautifulSoup(html, "html.parser")
    g = GamePage()
    h2 = soup.select_one("#music_info h2")
    g.title = h2.get_text(" ", strip=True) if h2 else ""
    g.system = clean_console(_info(soup, "Console"))
    year = soup.select_one("#music_info a[href*='/music/year/']")
    g.year = year.get_text(strip=True) if year else ""
    g.developer = _info(soup, "Developer")
    img = soup.select_one("#music_cover img[src]")
    if img and not img["src"].startswith(("./", "file:")):
        g.cover_url = urljoin(page_url, img["src"])
    for a in soup.select("#mass_download a[href]"):
        href, text = a["href"], a.get_text(" ", strip=True)
        size = re.search(r"\(([\d.,]+\s*[KMG]B)\)", text)
        kind = "mp3" if re.search(r"\(MP3\)|as MP3", href + " " + text, re.I) else "emu"
        g.downloads.setdefault(kind, (urljoin(page_url, href), size.group(1) if size else ""))
    return g


def get_game(page_url: str) -> GamePage:
    return parse_game(polite_get(page_url).text, page_url)


def download_file(url: str, dest) -> None:
    with polite_get(url, stream=True) as r:
        size = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                size += len(chunk)
                if size > MAX_ZIP_BYTES:
                    raise RuntimeError("download larger than size limit")
                f.write(chunk)
