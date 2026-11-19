import json
from html import escape
from pathlib import Path

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import config as C, db, worker, zophar
from .convert import natural_title

app = FastAPI()
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
worker.start()

def _pixels(grid: str, size: int = 3) -> str:
    """Draw a pixel-art picture from a text grid ('#' = filled). Inline SVG, so nothing loads from outside."""
    rows = grid.strip("\n").split("\n")
    rects = "".join(f"<rect x='{x * size}' y='{y * size}' width='{size}' height='{size}'/>"
                    for y, row in enumerate(rows) for x, ch in enumerate(row) if ch == "#")
    return (f"<svg viewBox='0 0 {len(rows[0]) * size} {len(rows) * size}' width='{len(rows[0]) * size}' "
            f"height='{len(rows) * size}' fill='currentColor' aria-hidden='true'>{rects}</svg>")


_ARROW = _pixels("""
.......#...
.......##..
###########
.......##..
.......#...
""", 3)

PAGE = """<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Zophar to Navidrome</title>
<script src="/static/htmx.min.js"></script>
<style>
:root{color-scheme:light dark;--bg:#eef0f8;--panel:#fff;--ink:#1c2040;--dim:#6b7090;--line:#1c204018;--hover:#3d4fd80d;
 --accent:#3d4fd8;--on-accent:#fff;--ok:#1f8a4c;--work:#b87500;--bad:#c93a3a;--r:14px}
@media(prefers-color-scheme:dark){:root{--bg:#13152b;--panel:#1b1e3a;--ink:#e8eaf8;--dim:#9aa0c4;--line:#e8eaf81c;
 --hover:#8e9bff14;--accent:#8e9bff;--on-accent:#13152b;--ok:#5fd08a;--work:#f0b840;--bad:#ff7b7b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;
 -webkit-font-smoothing:antialiased}
.wrap{max-width:74rem;margin:0 auto;padding:1.5rem 1rem 3rem}
header{display:flex;align-items:center;gap:.7rem;margin-bottom:1.25rem;color:var(--accent)}
header h1{margin:0;font-size:1.65rem;font-weight:800;letter-spacing:-.03em;color:var(--ink);display:flex;align-items:center;gap:.6rem}
header h1 svg{color:var(--accent)}
form.search{display:flex;gap:.5rem;flex-wrap:wrap;margin-bottom:1.25rem}
input,select,button{font:inherit;color:inherit}
input,select{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:.55rem .8rem;min-width:0}
input{flex:1 1 16rem}
input:focus,select:focus,button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
button{cursor:pointer;border-radius:10px;padding:.5rem .9rem;border:1px solid var(--line);background:transparent;transition:background .15s,transform .05s}
button:hover{background:var(--hover)}button:active{transform:translateY(1px)}
button.primary,form.search button{background:var(--accent);color:var(--on-accent);border-color:transparent;font-weight:600}
button.primary:hover,form.search button:hover{background:var(--accent);filter:brightness(1.1)}
button.retry{color:var(--bad);border-color:var(--bad)}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 21rem;gap:1.5rem;align-items:start}
aside{position:sticky;top:1rem}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);overflow:hidden}
.panel>h2{margin:0;padding:.9rem 1rem;font-size:1rem;font-weight:700;border-bottom:1px solid var(--line)}
.bar{display:flex;justify-content:space-between;align-items:center;padding:.6rem 1rem;border-bottom:1px solid var(--line)}
.msg,.empty{color:var(--dim)}.empty{padding:2.5rem 1rem;text-align:center}
table{width:100%;border-collapse:collapse}
td{padding:.5rem .75rem;border-bottom:1px solid var(--line);vertical-align:middle}
tr:last-child td{border-bottom:0}
tr:hover td{background:var(--hover)}
td:first-child{padding-left:1rem}td:last-child{padding-right:1rem}
td.console{width:1%;white-space:nowrap}td.year{width:1%;color:var(--dim);white-space:nowrap;font-variant-numeric:tabular-nums}
td.act{width:9rem;text-align:right}
td.act button{width:100%}
.hint{display:block;color:var(--dim);font-size:.8rem}
.chip{display:inline-block;padding:.1rem .6rem;border-radius:99px;font-size:.8rem;white-space:nowrap;
 background:hsl(var(--h) 70% 50%/.15);color:hsl(var(--h) 60% 34%)}
@media(prefers-color-scheme:dark){.chip{background:hsl(var(--h) 80% 70%/.16);color:hsl(var(--h) 85% 78%)}}
.meta{display:none;margin-top:.25rem;gap:.5rem;align-items:center}
.st{display:inline-flex;align-items:center;gap:.4rem;justify-content:center;width:100%;font-size:.9rem}
.st::before{content:"";width:.5rem;height:.5rem;border-radius:50%;background:currentColor}
.st.queued{color:var(--dim)}.st.working{color:var(--work)}.st.done{color:var(--ok)}.st.failed{color:var(--bad)}
.st.working::before{animation:pulse 1.1s ease-in-out infinite}
ul.q{list-style:none;margin:0;padding:0}
ul.q li{padding:.75rem 1rem;border-bottom:1px solid var(--line)}
ul.q li:last-child{border-bottom:0}
.job{display:flex;justify-content:space-between;gap:.75rem;align-items:flex-start}
.job .t{min-width:0;font-weight:500}
.job .chip{margin-top:.3rem}
.stage{display:flex;align-items:center;gap:.6rem;margin-top:.55rem;font-size:.85rem}
.meter{display:inline-flex;gap:3px}
.meter i{width:.7rem;height:.7rem;background:var(--line);border-radius:2px}
.meter[data-n="1"] i:nth-child(-n+1),.meter[data-n="2"] i:nth-child(-n+2),
.meter[data-n="3"] i:nth-child(-n+3),.meter[data-n="4"] i:nth-child(-n+4){background:var(--accent)}
.meter.working[data-n="2"] i:nth-child(2),.meter.working[data-n="3"] i:nth-child(3){animation:pulse 1.1s ease-in-out infinite}
.meter.done i{background:var(--ok)!important}
.meter.failed i{background:transparent;box-shadow:inset 0 0 0 1.5px var(--bad)}
.stage.queued{color:var(--dim)}.stage.working{color:var(--work)}.stage.done{color:var(--ok)}.stage.failed{color:var(--bad)}
.stage button{margin-left:auto;padding:.15rem .6rem;font-size:.85rem}
@keyframes pulse{50%{opacity:.35}}
.htmx-indicator{opacity:0;transition:opacity .2s;align-self:center;color:var(--dim)}.htmx-request .htmx-indicator{opacity:1}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
@media(max-width:800px){
 .layout{grid-template-columns:1fr}aside{position:static;order:-1}
 td.console,td.year{display:none}.meta{display:flex}td.act{width:7.5rem}
 #queue{max-height:16rem;overflow:auto}}
</style>
<div class=wrap>
<header><h1>Zophar __ARROW__ Navidrome</h1></header>
<form class=search hx-post="/search" hx-target="#results" hx-indicator="#spin">
<input name="q" placeholder="Search for a game" required autofocus aria-label="Game name">
<select name="console" aria-label="Console">__CONSOLES__</select><button>Search</button>
<span id=spin class=htmx-indicator>Searching&hellip;</span></form>
<div class=layout>
<main><div id="results"><div class="panel empty">Search for a game to see its soundtracks.</div></div></main>
<aside><div class=panel><h2>Queue</h2><div id=queue hx-get="/queue" hx-trigger="load, every 3s"></div></div></aside>
</div></div>"""
PAGE = PAGE.replace("__ARROW__", _ARROW)

_OPTIONS = "".join(f"<option value='{k}'>{escape(v)}</option>" for k, v in zophar.CONSOLES.items())

_HUES = {"Nintendo NES": 22, "Nintendo SNES": 268, "Game Boy": 88, "Game Boy Advance": 246, "Nintendo DS": 205,
         "Nintendo 3DS": 330, "Nintendo 64": 150, "Nintendo GameCube": 300, "Nintendo Wii": 175,
         "Nintendo Wii U": 215, "Nintendo Switch": 0, "Playstation": 228, "Playstation 2": 238,
         "Playstation Portable": 248, "Sega Mega Drive / Genesis": 195, "Sega Master System": 10,
         "Sega Saturn": 205, "Sega Dreamcast": 28, "TurboGrafx-16": 40}


def _chip(system: str) -> str:
    hue = _HUES.get(system, sum(map(ord, system)) * 7 % 360)   # unknown consoles get a steady colour of their own
    return f"<span class=chip style='--h:{hue}'>{escape(system)}</span>"


_STAGE = {"queued": 1, "downloading": 2, "converting": 3, "done": 4}
_LABEL = {"queued": "Queued", "downloading": "Downloading", "converting": "Converting", "done": "Done", "failed": "Failed"}


def _meter(st: str) -> str:
    cls = "working" if st in ("downloading", "converting") else st
    return f"<span class='meter {cls}' data-n='{_STAGE.get(st, 0)}'><i></i><i></i><i></i><i></i></span>"


def _status_cell(job, r):
    """What goes in the action column: a button, or the job's status in the same place."""
    if job is None:
        vals = escape(json.dumps({"url": r["page_url"], "title": r["title"], "system": r["system"]}), quote=True)
        return f"<button class=primary hx-post='/download' hx-vals='{vals}' hx-swap='outerHTML'>Download</button>"
    st = job["status"]
    if st == "failed":
        return (f"<button class=retry hx-post='/retry/{job['id']}' hx-swap='outerHTML' "
                f"title='{escape(job['detail'])}'>Retry</button>")
    cls = {"queued": "queued", "done": "done"}.get(st, "working")
    return f"<span class='st {cls}'>{_LABEL.get(st, st)}</span>"


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE.replace("__CONSOLES__", _OPTIONS)


@app.post("/search", response_class=HTMLResponse)
def search(q: str = Form(...), console: str = Form("0")):
    try:
        found = zophar.search(q, console if console in zophar.CONSOLES else '0')
    except Exception as e:
        return (f"<div class='panel empty'>Zophar search failed: {escape(str(e))}. "
                f"Check the server's internet connection and search again.</div>")
    if not found:
        return f"<div class='panel empty'>No soundtracks match &ldquo;{escape(q)}&rdquo;. Try a shorter name or another console.</div>"
    marks = ",".join("?" * len(found))
    jobs = {j["url"]: j for j in db.all_(f"SELECT id,url,status,detail FROM jobs WHERE url IN ({marks})",
                                          [r["page_url"] for r in found])}
    twins = {}
    for r in found:
        twins[(r["title"], r["system"])] = twins.get((r["title"], r["system"]), 0) + 1
    rows = []
    for r in found:
        hint = ""
        if twins[(r["title"], r["system"])] > 1:   # two Zophar rips of the same game: show what tells them apart
            hint = f"<span class=hint>{escape(r['page_url'].rsplit('/', 1)[-1])}</span>"
        rows.append(f"<tr><td>{escape(r['title'])}{hint}"
                    f"<span class=meta>{_chip(r['system'])}<span class=msg>{escape(r['year'])}</span></span></td>"
                    f"<td class=console>{_chip(r['system'])}</td><td class=year>{escape(r['year'])}</td>"
                    f"<td class=act>{_status_cell(jobs.get(r['page_url']), r)}</td></tr>")
    bar = f"<div class=bar><span class=msg>{len(found)} results</span>"
    if len(found) > 1:
        vals = escape(json.dumps({"q": q, "console": console}), quote=True)
        bar += (f"<button hx-post='/download_all' hx-vals='{vals}' hx-swap='outerHTML' "
                f"hx-confirm='Queue all {min(len(found), C.MAX_BATCH)} games? Each zip is often 300-400 MB.'>"
                f"Queue all {len(found)} results</button>")
    bar += "</div>"
    return f"<div class=panel>{bar}<table>{''.join(rows)}</table></div>"


@app.post("/download_all", response_class=HTMLResponse)
def download_all(q: str = Form(...), console: str = Form("0")):
    try:
        found = zophar.search(q, console if console in zophar.CONSOLES else "0")[: C.MAX_BATCH]
    except Exception as e:
        return f"<span class=failed>Search failed: {escape(str(e))}</span>"
    new = 0
    for r in found:
        cur = db.run("INSERT OR IGNORE INTO jobs(url,title,system) VALUES(?,?,?)", (r["page_url"], r["title"], r["system"]))
        if cur.rowcount:
            worker.jobs.put(cur.lastrowid)
            new += 1
    return f"<span class=msg>Queued {new} new ({len(found) - new} already requested). Refresh the search to see status.</span>"


@app.post("/download", response_class=HTMLResponse)
def download(url: str = Form(...), title: str = Form(...), system: str = Form(...)):
    if not zophar.is_zophar_url(url):
        return "<span class='st failed'>Refused</span>"
    cur = db.run("INSERT OR IGNORE INTO jobs(url,title,system) VALUES(?,?,?)", (url, title, system))
    if cur.rowcount:
        worker.jobs.put(cur.lastrowid)
        return "<span class='st queued'>Queued</span>"
    return "<span class='st queued'>Already requested</span>"


@app.post("/retry/{jid}", response_class=HTMLResponse)
def retry(jid: int):
    cur = db.run("UPDATE jobs SET status='queued', detail='' WHERE id=? AND status='failed'", (jid,))
    if cur.rowcount:   # a double-click or a stale page must not queue the same job twice
        worker.jobs.put(jid)
    return "<span class='st queued'>Queued</span>"


@app.get("/queue", response_class=HTMLResponse)
def queue_view():
    out = []
    for r in db.all_("SELECT id,title,system,status,detail,tracks FROM jobs ORDER BY id DESC LIMIT 30"):
        st = r["status"]
        cls = "working" if st in ("downloading", "converting") else st
        tracks = f"<span class=hint>{r['tracks']} tracks</span>" if r["tracks"] else ""
        detail = f"<span class=hint>{escape(r['detail'])}</span>" if r["detail"] else ""
        retry = f"<button class=retry hx-post='/retry/{r['id']}' hx-swap='outerHTML'>Retry</button>" if st == "failed" else ""
        out.append(f"<li><div class=job><span class=t>{escape(natural_title(r['title']))}{tracks}{detail}</span>"
                   f"{_chip(r['system'])}</div>"
                   f"<div class='stage {cls}'>{_meter(st)}<span>{_LABEL.get(st, st)}</span>{retry}</div></li>")
    return f"<ul class=q>{''.join(out) or '<li class=msg>Nothing queued yet. Downloads you start appear here.</li>'}</ul>"
