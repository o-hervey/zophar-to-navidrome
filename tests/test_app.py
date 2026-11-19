import json
import os
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path

import pytest

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["TRACK_SECONDS"] = "3"
os.environ["FADE_SECONDS"] = "1"

from app import convert, zophar  # noqa: E402


@pytest.fixture(autouse=True)
def _no_silence_check(monkeypatch):
    """The test NSFs are silent on purpose; the silence check has its own tests."""
    monkeypatch.setattr(convert.C, "MIN_TRACK_SECONDS", 0.0)


def make_nsf(path: Path, songs=3, game="Test Game", artist="Tester"):
    hdr = b"NESM\x1a" + bytes([1, songs, 1]) + struct.pack("<HHH", 0x8000, 0x8000, 0x8000)
    hdr += game.encode().ljust(32, b"\0") + artist.encode().ljust(32, b"\0") + b"2026".ljust(32, b"\0")
    hdr += struct.pack("<H", 16639) + bytes(8) + struct.pack("<H", 20000) + bytes([0, 0, 0])
    hdr = hdr.ljust(0x80, b"\0")
    path.write_bytes(hdr + b"\x60" * 16)   # code: RTS (silent, but a valid NSF)


def test_nsf_meta(tmp_path):
    f = tmp_path / "a.nsf"
    make_nsf(f, songs=5)
    m = convert.read_meta(f)
    assert (m.count, m.game, m.artist) == (5, "Test Game", "Tester")


def test_m3u_titles(tmp_path):
    m3u = tmp_path / "a.m3u"
    m3u.write_text("# c\na.nsf::NSF,1,Title\\, One,0:01:00,,\na.nsf::NSF,$02,Two,0:02:00\nother.nsf::NSF,1,X\n")
    assert convert.parse_m3u_titles(m3u, "a.nsf") == {0: "Title, One", 1: "Two"}


def test_zip_slip_rejected(tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../../evil.txt", "x")
    with zipfile.ZipFile(z) as zf, pytest.raises(ValueError):
        convert.safe_extract(zf, tmp_path / "x")


def test_end_to_end_multitrack(tmp_path):
    ex = tmp_path / "job" / "x"
    ex.mkdir(parents=True)
    make_nsf(ex / "a.nsf", songs=3)
    (ex / "a.m3u").write_text("a.nsf::NSF,1,Opening\na.nsf::NSF,2,Dungeon\n")
    root = tmp_path / "music"
    n = convert.convert_emu(ex, convert.Album("Test: Game?", "Nintendo NES", "1987", "Nintendo EAD"), root)
    assert n == 3
    out = sorted((root / "Nintendo NES" / "Test_ Game_").glob("*.flac"))
    assert [p.name for p in out] == ["001 - Opening.flac", "002 - Dungeon.flac", "003 - Track 3.flac"]
    tags = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(out[0])]))["format"]["tags"]
    tags = {k.lower(): v for k, v in tags.items()}
    assert tags["album"] == "Test: Game?" and tags["title"] == "Opening" and tags["genre"] == "Nintendo NES"
    assert tags["album_artist"] == "Nintendo EAD" and tags["date"] == "1987"
    assert tags["track"] == "1"


FIX = Path(__file__).parent / "fixtures"


def test_parse_search_real_page():
    r = zophar.parse_search((FIX / "search_zelda.html").read_text(encoding="utf-8"))
    assert len(r) == 25                       # "Found 25 games" on the page; sidebar links must not leak in
    assert r[3] == {"title": "Legend of Zelda, The - A Link Between Worlds", "system": "Nintendo 3DS", "year": "2013",
                    "page_url": "https://www.zophar.net/music/nintendo-3ds-3sf/legend-of-zelda-the-a-link-between-worlds"}
    assert all(x["page_url"].count("/") == 5 and "/letter/" not in x["page_url"] for x in r)


def test_parse_game_real_page():
    url = "https://www.zophar.net/music/nintendo-3ds-3sf/legend-of-zelda-the-a-link-between-worlds"
    g = zophar.parse_game((FIX / "game_page.html").read_text(encoding="utf-8"), url)
    assert (g.title, g.system, g.year, g.developer) == (
        "Legend of Zelda, The - A Link Between Worlds", "Nintendo 3DS", "2013", "Nintendo EAD, Monolith")
    assert g.downloads["mp3"][0].endswith("%28MP3%29.zophar.zip") and g.downloads["mp3"][1] == "382 MB"
    assert g.downloads["emu"][0].endswith("%28EMU%29.zophar.zip") and g.downloads["emu"][1] == "306 MB"


def test_source_order():
    from app import worker
    g = zophar.GamePage(downloads={"mp3": ("u", ""), "emu": ("u", "")})
    assert worker._source_order(g, "https://www.zophar.net/music/nintendo-nes-nsf/x") == ["emu", "mp3"]
    assert worker._source_order(g, "https://www.zophar.net/music/nintendo-3ds-3sf/x") == ["mp3"]
    assert worker._source_order(zophar.GamePage(downloads={"emu": ("u", "")}),
                                "https://www.zophar.net/music/nintendo-3ds-3sf/x") == []


def test_refuses_foreign_hosts():
    assert zophar.is_zophar_url("https://fi.zophar.net/a.zip")
    assert not zophar.is_zophar_url("https://zophar.net.evil.com/a.zip")
    assert not zophar.is_zophar_url("http://www.zophar.net/a.zip")


def test_mp3_path(tmp_path):
    ex = tmp_path / "job" / "x" / "sub"
    ex.mkdir(parents=True)
    for name in ("B_Track", "A_Track.ry.32.dspadpcm"):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1", "-c:a", "libmp3lame",
                        "-metadata", "title=old junk", str(ex / f"{name}.mp3")], check=True)
    root = tmp_path / "music"
    n = convert.convert_mp3(tmp_path / "job" / "x", convert.Album("Zelda: ALBW", "Nintendo 3DS", "2013", "Nintendo EAD"), root)
    out = sorted((root / "Nintendo 3DS" / "Zelda_ ALBW").glob("*.mp3"))
    assert n == 2 and out[0].name.startswith("001 - A Track")
    tags = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json",
                                               str(out[0])]))["format"]["tags"]
    assert tags["title"] == "A Track" and tags["album"] == "Zelda: ALBW" and tags["track"] == "1"


def test_worker_fallback_emu_to_mp3(tmp_path, monkeypatch):
    """EMU zip with nothing libgme can play -> falls back to the MP3 zip; cover is saved; job completes."""
    from app import config as C, db, worker
    mp3 = tmp_path / "t.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1", "-c:a", "libmp3lame", str(mp3)], check=True)
    zips = {"emu": tmp_path / "emu.zip", "mp3": tmp_path / "mp3.zip"}
    with zipfile.ZipFile(zips["emu"], "w") as z:
        z.writestr("readme.txt", "nothing playable")
    with zipfile.ZipFile(zips["mp3"], "w") as z:
        z.write(mp3, "Theme.mp3")
    g = zophar.GamePage("Some Game", "Nintendo NES", "1990", "Dev", "https://www.zophar.net/cover.png",
                        {"emu": ("https://fi.zophar.net/emu.zip", ""), "mp3": ("https://fi.zophar.net/mp3.zip", "")})
    got = []
    monkeypatch.setattr(zophar, "get_game", lambda url: g)
    monkeypatch.setattr(zophar, "download_file", lambda url, dest: (got.append(url),
                        Path(dest).write_bytes(zips["emu" if "emu" in url else "mp3"].read_bytes())))
    class R: content = b"PNGDATA"
    monkeypatch.setattr(zophar, "polite_get", lambda url, **kw: R())
    monkeypatch.setattr(C, "MUSIC_ROOT", tmp_path / "music")
    monkeypatch.setattr(worker, "trigger_scan", lambda: "")
    jid = db.run("INSERT INTO jobs(url,title,system) VALUES('https://www.zophar.net/music/nintendo-nes-nsf/some-game','Some Game','Nintendo NES')").lastrowid
    worker._process(jid)
    assert got == ["https://fi.zophar.net/emu.zip", "https://fi.zophar.net/mp3.zip"]
    album = tmp_path / "music" / "Nintendo NES" / "Some Game"
    assert (album / "001 - Theme.mp3").exists()
    assert (album / "cover.png").read_bytes() == b"PNGDATA"   # cover saved by default
    n_dl = len(got)
    worker._process(db.run("INSERT INTO jobs(url,title,system) VALUES('https://www.zophar.net/music/nintendo-nes-nsf/some-game-2','Some Game','Nintendo NES')").lastrowid)
    assert len(got) == n_dl                             # album already in library: nothing downloaded again


def test_natural_title_and_track_names():
    nt = convert.natural_title
    assert nt("Legend of Zelda, The - A Link Between Worlds") == "The Legend of Zelda - A Link Between Worlds"
    assert nt("Legend of Zelda 2, The - The Adventure of Link") == "The Legend of Zelda 2 - The Adventure of Link"
    assert nt("Zelda II - The Adventure of Link") == "Zelda II - The Adventure of Link"
    assert nt("Sonic, Tails and Friends") == "Sonic, Tails and Friends"
    tt = convert.tidy_track_name
    assert tt("BossGetFanfare.ry.32.dspadpcm") == "Boss Get Fanfare"
    assert tt("BGM_BARD_D_FIELD_DARK") == "BARD D FIELD DARK"
    assert tt("MainField-Hikari-Kouhan-intro.ry.32.dspadpcm") == "Main Field Hikari Kouhan intro"
    assert tt("ZenmetsuBeya-little-loop.ry.32.dspadpcm") == "Zenmetsu Beya little loop"
    assert tt("Kakariko.ry.32.dspadpcm") == "Kakariko"
    assert tt("Track 01") == "Track 01"


def test_queue_all_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    rows = [{"title": f"G{i}", "system": "Nintendo NES", "year": "1990", "page_url": f"https://www.zophar.net/music/nintendo-nes-nsf/g{i}"} for i in range(3)]
    monkeypatch.setattr(zophar, "search", lambda q, c="0": rows)
    monkeypatch.setattr(main.worker.jobs, "put", lambda x: None)
    c = TestClient(main.app)
    assert "Queue all 3 results" in c.post("/search", data={"q": "g", "console": "1"}).text
    assert "Queued 3 new" in c.post("/download_all", data={"q": "g", "console": "1"}).text
    assert "Queued 0 new (3 already" in c.post("/download_all", data={"q": "g", "console": "1"}).text


def test_cover_url_parsing_and_large_fallback(tmp_path, monkeypatch):
    html = ('<div id="music_info"><h2>Final Fantasy VII</h2></div><div id="music_cover">'
            '<img src="https://fi.zophar.net/soundcovers/playstation-psf/thumbs_small/ps1_finalfantasy7_front_eu.jpg"></div>')
    g = zophar.parse_game(html, "https://www.zophar.net/music/playstation-psf/final-fantasy-vii")
    assert g.cover_url == "https://fi.zophar.net/soundcovers/playstation-psf/thumbs_small/ps1_finalfantasy7_front_eu.jpg"
    assert zophar.is_zophar_url(g.cover_url)
    # relative form also resolves
    g2 = zophar.parse_game(html.replace("https://fi.zophar.net", ""), "https://www.zophar.net/music/x/y")
    assert g2.cover_url == "https://www.zophar.net/soundcovers/playstation-psf/thumbs_small/ps1_finalfantasy7_front_eu.jpg"


def test_m3u_lengths_and_plan_duration(tmp_path):
    m3u = tmp_path / "a.m3u"
    m3u.write_text("a.nsf::NSF,1,Jingle,0:00:02,,\na.nsf::NSF,2,Theme,1:30-,,\na.nsf::NSF,3,NoLen,,,\n")
    titles, lengths = convert.parse_m3u(m3u, "a.nsf")
    assert titles == {0: "Jingle", 1: "Theme", 2: "NoLen"}
    assert lengths == {0: 2.0, 1: 90.0}
    assert convert.plan_duration(None) == (3.0, 1.0)          # TRACK_SECONDS=3, FADE_SECONDS=1 in this test run
    dur, fade = convert.plan_duration(2.0)
    assert dur == 2.0 and fade == 0.5                          # short jingle stays short, fade shrinks with it
    assert convert.plan_duration(99999)[0] == float(convert.C.MAX_TRACK_SECONDS)


def test_nsfe_and_spc_lengths(tmp_path):
    def chunk(cid, body):
        return struct.pack("<I4s", len(body), cid) + body
    info = struct.pack("<HBBBBBB", 0x8000, 0, 0, 0, 0, 2, 0)     # byte 8 = song count... padded below
    info = (info + bytes(10))[:10]
    info = info[:8] + bytes([2]) + info[9:]
    data = b"NSFE" + chunk(b"INFO", info) + chunk(b"time", struct.pack("<ii", 2500, -1)) + chunk(b"NEND", b"")
    f = tmp_path / "a.nsfe"
    f.write_bytes(data)
    m = convert.read_meta(f)
    assert m.count == 2 and m.lengths == {0: 2.5}
    spc = bytearray(b"SNES-SPC700 Sound File Data v0.30".ljust(0x100, b"\0"))
    spc[0x23] = 26
    spc[0x2E:0x2E + 5] = b"Title"
    spc[0xA9:0xAC] = b"075"
    f2 = tmp_path / "a.spc"
    f2.write_bytes(bytes(spc))
    m2 = convert.read_meta(f2)
    assert m2.titles == {0: "Title"} and m2.lengths == {0: 75}


def test_publish_is_atomic_and_cleans_up(tmp_path):
    album = convert.Album("Game", "Nintendo NES")
    root = tmp_path / "music"
    stale = root / "Nintendo NES" / ".incoming-Game"
    stale.mkdir(parents=True)
    (stale / "junk").write_text("x")
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "001 - A.flac").write_bytes(b"a")
    dest = convert._publish(stage, album, root)
    assert [p.name for p in dest.iterdir()] == ["001 - A.flac"]
    assert [p.name for p in dest.parent.iterdir()] == ["Game"]    # no .incoming leftovers
    (stage / "002 - B.flac").write_bytes(b"b")
    with pytest.raises(FileExistsError):
        convert._publish(stage, album, root)
    assert [p.name for p in dest.parent.iterdir()] == ["Game"]    # failed publish cleaned up after itself


def test_retry_only_queues_failed_jobs(monkeypatch):
    from fastapi.testclient import TestClient
    from app import db, main
    queued = []
    monkeypatch.setattr(main.worker.jobs, "put", queued.append)
    jid = db.run("INSERT INTO jobs(url,title,system,status) VALUES('https://www.zophar.net/music/x/retry','R','X','failed')").lastrowid
    c = TestClient(main.app)
    c.post(f"/retry/{jid}")
    c.post(f"/retry/{jid}")                      # second click: job is already 'queued'
    assert queued == [jid]


def test_download_all_reports_search_failure(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    def boom(q, c="0"):
        raise RuntimeError("zophar down")
    monkeypatch.setattr(zophar, "search", boom)
    r = TestClient(main.app).post("/download_all", data={"q": "g", "console": "1"})
    assert r.status_code == 200 and "zophar down" in r.text


def test_htmx_served_locally():
    from fastapi.testclient import TestClient
    from app import main
    c = TestClient(main.app)
    assert "/static/htmx.min.js" in c.get("/").text and "unpkg" not in c.get("/").text
    r = c.get("/static/htmx.min.js")
    assert r.status_code == 200 and b"htmx" in r.content


def test_chip_and_meter_markup():
    from app import main
    assert "--h:22" in main._chip("Nintendo NES") and "Nintendo NES" in main._chip("Nintendo NES")
    assert main._chip("Weird <b>System</b>") == main._chip("Weird <b>System</b>")        # unknown consoles: steady colour
    assert "<b>" not in main._chip("Weird <b>System</b>")                                  # and always escaped
    assert "data-n='3'" in main._meter("converting") and "working" in main._meter("converting")
    assert "data-n='4'" in main._meter("done") and "data-n='0'" in main._meter("failed")


def test_parse_audible():
    jingle = "silence_start: 3.2\nsilence_end: 4.0 | silence_duration: 0.8\nsilence_start: 5.1\n"
    assert convert.parse_audible(jingle, 180) == 5.1        # goes quiet for good at 5.1s
    assert convert.parse_audible("silence_start: 3\nsilence_end: 180 | silence_duration: 177\n", 180) == 3   # ends at EOF
    assert convert.parse_audible("", 180) == 180            # no silence: loops, keep full length
    assert convert.parse_audible("silence_start: 9\nsilence_end: 12 | silence_duration: 3\n", 180) == 180
    assert convert.parse_audible("silence_start: -0.002\n", 180) == 0.0   # silent from the start


def test_silent_tracks_are_skipped_and_album_fails_over(tmp_path, monkeypatch):
    ex = tmp_path / "ex"; ex.mkdir()
    make_nsf(ex / "a.nsf", songs=3)                          # valid NSF that makes no sound
    album = convert.Album("Silent", "Nintendo NES", "", "")
    monkeypatch.setattr(convert.C, "MIN_TRACK_SECONDS", 20.0)
    monkeypatch.setattr(convert.C, "TRACK_SECONDS", 4)
    try:
        convert.convert_emu(ex, album, tmp_path / "lib")
        assert False, "expected NoPlayableFiles"
    except convert.NoPlayableFiles as e:
        assert "3 skipped" in str(e)
    assert not (tmp_path / "lib").exists() or not any((tmp_path / "lib").rglob("*.flac"))


def test_parse_audible_when_stream_ends_early():
    # libgme ended the track at 8s: 3s of sound then quiet to the end. Must not be mistaken for a 180s track.
    out = "silence_start: 3\nsize=N/A time=00:00:08.00 bitrate=N/A\nsilence_end: 8 | silence_duration: 5\n"
    assert convert.parse_audible(out, 180) == 3
    assert convert.parse_audible("size=N/A time=00:00:02.50 bitrate=N/A\n", 180) == 2.5   # just ends, no quiet part
    assert convert.parse_audible("size=N/A time=00:03:00.00 bitrate=N/A\n", 180) == 180   # loops the full length


def test_probe_audible_with_real_ffmpeg(tmp_path, monkeypatch):
    # A WAV stands in for a subtrack; only the GME-specific -track_index option has to be dropped for it.
    real_run = subprocess.run
    monkeypatch.setattr(convert.subprocess, "run", lambda cmd, **kw: real_run(
        [c for i, c in enumerate(cmd) if c != "-track_index" and cmd[i - 1] != "-track_index"], **kw))
    for name, spec, want in (("jingle", "sine=frequency=440:duration=3,apad=whole_dur=8", 3.0),
                             ("short", "sine=frequency=440:duration=4", 4.0),
                             ("music", "sine=frequency=440:duration=40", 40.0)):
        f = tmp_path / f"{name}.wav"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", spec, str(f)], check=True)
        got = convert.probe_audible(f, 0, 40.0)
        assert abs(got - want) < 0.6, (name, got)


def test_loop_period_separates_effects_from_music():
    import numpy as np
    rate = 8000
    t = np.arange(rate * 40) / rate
    beep = np.where((t % 0.8) < 0.15, np.sin(2 * np.pi * 880 * t), 0.0)           # an effect repeating every 0.8s
    rng = np.random.default_rng(1)
    notes = rng.integers(200, 900, 40)                                              # 40 different notes, 1s each
    music = np.sin(2 * np.pi * notes[(t).astype(int)] * t)
    assert abs(convert.loop_period(beep, rate, 10) - 0.8) < 0.05
    assert convert.loop_period(music, rate, 10) is None
    assert convert.loop_period(np.zeros(rate * 40), rate, 10) is None               # silence is not a loop
    assert convert.loop_period(beep[: rate * 3], rate, 10) is None                   # too short to judge


def test_untitled_tracks_are_just_numbered(tmp_path):
    ex = tmp_path / "job" / "x"; ex.mkdir(parents=True)
    make_nsf(ex / "Some Game (1990)(Co)(Co).nsf", songs=2)
    convert.convert_emu(ex, convert.Album("G", "Nintendo NES", "1990", ""), tmp_path / "lib")
    names = sorted(p.name for p in (tmp_path / "lib" / "Nintendo NES" / "G").glob("*.flac"))
    assert names == ["001 - Track 1.flac", "002 - Track 2.flac"]


def test_mp3_route_skips_short_tracks(tmp_path, monkeypatch):
    ex = tmp_path / "job" / "x"; ex.mkdir(parents=True)
    for name, secs in (("Boss Theme", 25), ("Door Open", 2), ("Item Get", 4), ("Overworld", 30)):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=d={secs}", "-c:a", "libmp3lame",
                        str(ex / f"{name}.mp3")], check=True)
    monkeypatch.setattr(convert.C, "MIN_TRACK_SECONDS", 20.0)
    n = convert.convert_mp3(tmp_path / "job" / "x", convert.Album("G", "Nintendo 3DS", "2013", ""), tmp_path / "lib")
    names = sorted(p.name for p in (tmp_path / "lib" / "Nintendo 3DS" / "G").glob("*.mp3"))
    assert n == 2 and names == ["001 - Boss Theme.mp3", "002 - Overworld.mp3"]      # numbered without gaps
    monkeypatch.setattr(convert.C, "MIN_TRACK_SECONDS", 60.0)
    try:
        convert.convert_mp3(tmp_path / "job" / "x", convert.Album("H", "Nintendo 3DS", "2013", ""), tmp_path / "lib")
        assert False
    except convert.NoPlayableFiles as e:
        assert "4 skipped" in str(e)
