"""
👻 Ghost Union Blog v2.1 — Haunted Edition
============================================================
  ⚙ All configuration and content come from the remote JSON.
  ⚙ Threaded comments (with replies) stored in-memory.
  ⚙ Light & Dark themes.
  ⚙ Zero extra dependencies.
  ⚙ Cache-busting for GitHub raw CDN.
  ⚙ Full-image rendering (no crops) + ghostly ambience.
============================================================
"""
import asyncio
import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI(title="Ghost Union Blog")

# ============================================================
#                     CONFIG
# ============================================================
CONTENT_URL = "https://raw.githubusercontent.com/null-flame/-/refs/heads/main/a.json"
CACHE_TTL = 300

# ============================================================
#                     IN-MEMORY STORES
# ============================================================
_cache: dict = {"data": None, "ts": 0, "etag": None}
_comments: dict = {}
_comment_id = 0


# ============================================================
#              CONTENT FETCHING  (stdlib urllib)
# ============================================================
def _blocking_fetch(etag: Optional[str], bust: bool = False):
    url = CONTENT_URL
    if bust:
        url = f"{CONTENT_URL}?v={int(time.time() * 1000)}"

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "GhostUnionBlog/2.1",
            "Accept": "application/json",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )
    if etag and not bust:
        req.add_header("If-None-Match", etag)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return ("ok", json.loads(raw), resp.headers.get("ETag"))
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return ("not_modified", None, None)
        raise


async def fetch_content(force: bool = False) -> dict:
    now = time.time()
    if not force and _cache["data"] and (now - _cache["ts"]) < CACHE_TTL:
        return _cache["data"]
    try:
        status, data, etag = await asyncio.to_thread(
            _blocking_fetch, _cache["etag"], force,
        )
        if status == "ok":
            _cache["data"] = data
            _cache["ts"] = now
            _cache["etag"] = etag
        else:
            _cache["ts"] = now
        return _cache["data"]
    except Exception as e:
        if _cache["data"]:
            return _cache["data"]
        raise HTTPException(status_code=502, detail=f"Failed to fetch content: {e}")


# ============================================================
#                CUSTOM MARKDOWN PARSER
# ============================================================
_BLOCK_START = re.compile(r"^(#{1,6}\s|```|>|\s*[-*+]\s|\s*\d+\.\s)")


def _safe_url(url: str) -> str:
    u = url.strip()
    low = u.lower()
    if low.startswith(("javascript:", "vbscript:", "data:text/html")):
        return "#"
    return html.escape(u, quote=True)


def _render_inline(text: str) -> str:
    text = html.escape(text, quote=False)
    code_spans = []

    def _stash_code(m):
        code_spans.append(m.group(1))
        return f"\x00CODE{len(code_spans) - 1}\x00"

    text = re.sub(r"`([^`]+)`", _stash_code, text)
    text = re.sub(
        r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)",
        lambda m: f'<img src="{_safe_url(m.group(2))}" alt="{m.group(1)}" loading="lazy">',
        text,
    )
    text = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)",
        lambda m: f'<a href="{_safe_url(m.group(2))}" target="_blank" rel="noopener">{m.group(1)}</a>',
        text,
    )
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", text)
    text = re.sub(r"(?<![\w_])_([^_\n]+)_(?![\w_])", r"<em>\1</em>", text)
    text = re.sub(r"~~([^~]+)~~", r"<del>\1</del>", text)

    for i, code in enumerate(code_spans):
        text = text.replace(f"\x00CODE{i}\x00", f"<code>{code}</code>")
    return text


def render_markdown(text: str) -> str:
    if not text:
        return ""

    lines = text.replace("\r\n", "\n").split("\n")
    out = []
    i, n = 0, len(lines)

    def is_blank(s): return not s.strip()

    while i < n:
        line = lines[i]

        if line.lstrip().startswith("```"):
            lang = line.lstrip()[3:].strip()
            i += 1
            buf = []
            while i < n and not lines[i].lstrip().startswith("```"):
                buf.append(lines[i]); i += 1
            i += 1
            code = "\n".join(buf)
            lang_attr = f' class="language-{html.escape(lang, quote=True)}"' if lang else ""
            out.append(f"<pre><code{lang_attr}>{html.escape(code)}</code></pre>")
            continue

        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{_render_inline(m.group(2))}</h{level}>")
            i += 1
            continue

        if re.match(r"^\s*(\*\s*){3,}$|^\s*(-\s*){3,}$|^\s*(_\s*){3,}$", line):
            out.append("<hr>"); i += 1
            continue

        if line.lstrip().startswith(">"):
            buf = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i])); i += 1
            out.append(f"<blockquote>{render_markdown(chr(10).join(buf))}</blockquote>")
            continue

        if ("|" in line and i + 1 < n
                and re.match(r"^\s*\|?[\s\-:|]+\|?\s*$", lines[i + 1])
                and "-" in lines[i + 1]):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            thead = "<tr>" + "".join(f"<th>{_render_inline(c)}</th>" for c in header) + "</tr>"
            tbody = "".join(
                "<tr>" + "".join(f"<td>{_render_inline(c)}</td>" for c in r) + "</tr>"
                for r in rows
            )
            out.append(f"<table><thead>{thead}</thead><tbody>{tbody}</tbody></table>")
            continue

        if re.match(r"^\s*[-*+]\s+", line):
            items = []
            while i < n and re.match(r"^\s*[-*+]\s+", lines[i]):
                items.append(f"<li>{_render_inline(re.sub(r'^\\s*[-*+]\\s+', '', lines[i]))}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue

        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < n and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(f"<li>{_render_inline(re.sub(r'^\\s*\\d+\\.\\s+', '', lines[i]))}</li>")
                i += 1
            out.append("<ol>" + "".join(items) + "</ol>")
            continue

        if is_blank(line):
            i += 1
            continue

        buf = []
        while (i < n and not is_blank(lines[i])
               and not _BLOCK_START.match(lines[i])):
            if ("|" in lines[i] and i + 1 < n
                    and re.match(r"^\s*\|?[\s\-:|]+\|?\s*$", lines[i + 1])
                    and "-" in lines[i + 1]):
                break
            buf.append(lines[i]); i += 1
        if buf:
            joined = "\n".join(buf)
            rendered = _render_inline(joined).replace("\n", "<br>\n")
            out.append(f"<p>{rendered}</p>")

    result = "\n".join(out)
    result = re.sub(
        r"<\s*(script|style|iframe|object|embed|form)[^>]*>.*?<\s*/\s*\1\s*>",
        "", result, flags=re.DOTALL | re.IGNORECASE,
    )
    result = re.sub(
        r"<\s*(script|style|iframe|object|embed|form)[^>]*/?>",
        "", result, flags=re.IGNORECASE,
    )
    return result


# ============================================================
#                  DATA HELPERS
# ============================================================
def get_site(data: dict) -> dict:
    return data.get("site", {})


def get_posts(data: dict) -> list:
    return sorted(data.get("posts", []), key=lambda p: p.get("date", ""), reverse=True)


def find_post(data: dict, slug: str) -> Optional[dict]:
    for p in get_posts(data):
        if p.get("slug") == slug:
            return p
    return None


def excerpt(post: dict, length: int = 165) -> str:
    ex = post.get("excerpt") or ""
    if not ex:
        raw = re.sub(r"[#*`>\[\]()!_-]", "", post.get("content", ""))
        ex = raw[:length].strip()
        if len(raw) > length:
            ex += "…"
    return ex


def reading_time(post: dict) -> int:
    text = re.sub(r"\s+", " ", post.get("content", "") or "")
    words = len(text.split())
    return max(1, round(words / 220))


def format_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y")
    except Exception:
        return iso or ""


def tag_counts(posts: list) -> dict:
    counts: dict = {}
    for p in posts:
        for t in p.get("tags", []):
            counts[t] = counts.get(t, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower())))


def related_posts(data: dict, post: dict, limit: int = 3) -> list:
    tags = set(post.get("tags", []))
    if not tags:
        return []
    scored = []
    for p in get_posts(data):
        if p.get("slug") == post.get("slug"):
            continue
        common = len(tags & set(p.get("tags", [])))
        if common:
            scored.append((common, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:limit]]


def author_avatar(name: str) -> str:
    h = int(hashlib.md5((name or "?").encode()).hexdigest(), 16)
    return f"hsl({h % 360}, 70%, 48%)"


def author_initial(name: str) -> str:
    return (name or "?").strip()[:1].upper()


def cover_gradient(slug: str) -> str:
    h = int(hashlib.md5((slug or "?").encode()).hexdigest(), 16)
    h1 = h % 360
    h2 = (h1 + 70) % 360
    return f"linear-gradient(135deg, hsl({h1},60%,22%) 0%, hsl({h2},65%,38%) 100%)"


# ============================================================
#                  THREADED COMMENTS
# ============================================================
def add_comment(slug: str, author: str, text: str,
                parent_id: Optional[int] = None) -> dict:
    global _comment_id
    _comment_id += 1
    c = {
        "id": _comment_id,
        "author": author.strip()[:24],
        "text": text.strip()[:1000],
        "time": datetime.now(timezone.utc).strftime("%H:%M UTC"),
        "ts": time.time(),
        "parent_id": int(parent_id) if parent_id else None,
    }
    _comments.setdefault(slug, []).append(c)
    if len(_comments[slug]) > 500:
        _comments[slug] = _comments[slug][-500:]
    return c


def get_comments(slug: str) -> list:
    return _comments.get(slug, [])


def build_comment_tree(comments: list) -> list:
    by_id = {c["id"]: {**c, "replies": []} for c in comments}
    roots: list = []
    for c in comments:
        node = by_id[c["id"]]
        pid = c.get("parent_id")
        if pid and pid in by_id:
            by_id[pid]["replies"].append(node)
        else:
            roots.append(node)
    return roots


def count_all(comments: list) -> int:
    return len(comments)


# ============================================================
#                  HTML TEMPLATE  (Haunted Edition)
# ============================================================
HTML = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="{{ site_description }}">
<title>{{ page_title }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=Space+Grotesk:wght@500;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
/* ============= THEME TOKENS ============= */
:root, [data-theme="dark"] {
  --bg:#05070c; --bg-2:#0a0d16; --surface:#0f1420; --surface-2:#141a28;
  --surface-hover:#1a2235; --border:#1b2333; --border-hi:#2a3550;
  --text:#e9eef8; --text-dim:#96a3ba; --text-faint:#5b6884;
  --accent:#a78bfa; --accent-2:#22d3ee; --accent-3:#f472b6;
  --accent-soft: rgba(167,139,250,.13); --accent-glow: rgba(167,139,250,.45);
  --ghost-glow: rgba(34,211,238,.35);
  --gold:#fbbf24; --danger:#f43f5e; --success:#22c55e;
  --radius:16px; --radius-sm:11px;
  --shadow-sm: 0 2px 8px rgba(0,0,0,.35);
  --shadow-md: 0 10px 32px rgba(0,0,0,.5);
  --shadow-lg: 0 24px 60px rgba(0,0,0,.6);
  --shadow-glow: 0 0 40px var(--accent-glow);
}
[data-theme="light"] {
  --bg:#f6f7fc; --bg-2:#eef1f9; --surface:#ffffff; --surface-2:#f7f8fd;
  --surface-hover:#eef1fa; --border:#e3e7f1; --border-hi:#cbd3e6;
  --text:#12172a; --text-dim:#57617a; --text-faint:#8a93ac;
  --accent:#6d28d9; --accent-2:#0891b2; --accent-3:#db2777;
  --accent-soft: rgba(109,40,217,.10); --accent-glow: rgba(109,40,217,.28);
  --ghost-glow: rgba(8,145,178,.22);
  --gold:#d97706; --danger:#dc2626; --success:#16a34a;
  --shadow-sm: 0 2px 6px rgba(20,26,43,.06);
  --shadow-md: 0 10px 24px rgba(20,26,43,.09);
  --shadow-lg: 0 22px 50px rgba(20,26,43,.14);
  --shadow-glow: 0 0 30px var(--accent-glow);
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{
  font-family:'Inter',system-ui,-apple-system,sans-serif;
  background:var(--bg); color:var(--text);
  line-height:1.65; min-height:100vh;
  transition:background .3s, color .3s;
  overflow-x:hidden;
  position:relative;
}

/* ============= FOG + ORBS (fixed, GPU-friendly) ============= */
.bg-deco{position:fixed;inset:0;z-index:-2;pointer-events:none;overflow:hidden}
.bg-deco::before, .bg-deco::after{
  content:"";position:absolute;width:760px;height:760px;border-radius:50%;
  will-change:transform,opacity;
}
.bg-deco::before{
  top:-280px;left:-240px;
  background:radial-gradient(circle, var(--accent-glow), transparent 65%);
  animation:drift1 28s ease-in-out infinite;
}
.bg-deco::after{
  bottom:-300px;right:-260px;
  background:radial-gradient(circle, var(--ghost-glow), transparent 65%);
  animation:drift2 34s ease-in-out infinite;
}
[data-theme="light"] .bg-deco::before{background:radial-gradient(circle, rgba(109,40,217,.20), transparent 65%)}
[data-theme="light"] .bg-deco::after{background:radial-gradient(circle, rgba(8,145,178,.18), transparent 65%)}
@keyframes drift1{0%,100%{transform:translate(0,0) scale(1);opacity:.55}50%{transform:translate(80px,100px) scale(1.1);opacity:.8}}
@keyframes drift2{0%,100%{transform:translate(0,0) scale(1);opacity:.5}50%{transform:translate(-90px,-70px) scale(1.12);opacity:.75}}

/* ============= GHOST SWARM (5 floating ghosts) ============= */
.ghost-swarm{position:fixed;inset:0;z-index:-1;pointer-events:none;overflow:hidden}
.ghost-swarm span{
  position:absolute;
  font-size:26px;
  opacity:.07;
  filter:blur(1px) drop-shadow(0 0 24px var(--accent));
  animation:ghostFloat 22s ease-in-out infinite;
  will-change:transform,opacity;
}
.ghost-swarm span:nth-child(1){left:8%;top:18%;animation-delay:0s;font-size:30px}
.ghost-swarm span:nth-child(2){left:82%;top:32%;animation-delay:-6s;font-size:20px}
.ghost-swarm span:nth-child(3){left:28%;top:68%;animation-delay:-11s;font-size:34px}
.ghost-swarm span:nth-child(4){left:66%;top:82%;animation-delay:-16s;font-size:18px}
.ghost-swarm span:nth-child(5){left:46%;top:8%;animation-delay:-3s;font-size:22px}
@keyframes ghostFloat{
  0%,100%{transform:translate(0,0) rotate(-6deg);opacity:.05}
  33%{transform:translate(14px,-30px) rotate(4deg);opacity:.14}
  66%{transform:translate(-10px,-14px) rotate(-3deg);opacity:.09}
}

a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:none}
img{max-width:100%;display:block}

/* ============= NAV ============= */
nav{
  position:sticky;top:0;z-index:50;
  background:color-mix(in srgb, var(--bg) 72%, transparent);
  backdrop-filter:blur(18px) saturate(180%);
  -webkit-backdrop-filter:blur(18px) saturate(180%);
  border-bottom:1px solid var(--border);
}
.nav-inner{max-width:1120px;margin:0 auto;padding:13px 22px;
  display:flex;align-items:center;gap:18px}
.nav-logo{display:flex;align-items:center;gap:11px;font-weight:800;
  font-size:16px;color:var(--text);letter-spacing:-.3px;flex-shrink:0}
.nav-logo img{width:36px;height:36px;border-radius:10px;object-fit:cover}
.nav-logo .ghost-icon{width:36px;height:36px;border-radius:10px;
  background:linear-gradient(135deg,var(--accent),var(--accent-2));
  display:flex;align-items:center;justify-content:center;font-size:19px;
  box-shadow:0 4px 20px var(--accent-glow);
  animation:ghostBob 3.4s ease-in-out infinite}
@keyframes ghostBob{0%,100%{transform:translateY(0) rotate(-2deg)}50%{transform:translateY(-3px) rotate(2deg)}}
.nav-spacer{flex:1}
.nav-links{display:flex;gap:6px;align-items:center}
.nav-links a{padding:8px 14px;border-radius:10px;font-size:13.5px;font-weight:600;
  color:var(--text-dim);transition:.2s;position:relative}
.nav-links a:hover{color:var(--text);background:var(--surface-hover)}
.nav-links a.active{color:var(--accent);background:var(--accent-soft)}
.nav-chat{display:inline-flex;align-items:center;gap:7px;padding:9px 16px;
  border-radius:11px;font-size:13.5px;font-weight:700;
  background:linear-gradient(135deg,var(--accent),var(--accent-2));
  color:#fff !important; box-shadow:0 4px 20px var(--accent-glow);
  transition:.25s;white-space:nowrap}
.nav-chat:hover{transform:translateY(-1px);box-shadow:0 8px 28px var(--accent-glow)}
.theme-toggle{width:38px;height:38px;border-radius:11px;border:1px solid var(--border);
  background:var(--surface);color:var(--text);font-size:16px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;transition:.25s}
.theme-toggle:hover{background:var(--surface-hover);border-color:var(--accent);
  color:var(--accent);transform:rotate(180deg)}
@media(max-width:640px){ .nav-links a:not(.nav-chat){display:none} }

/* ============= LAYOUT ============= */
.container{max-width:1120px;margin:0 auto;padding:36px 22px 80px;position:relative}

/* ============= HERO ============= */
.hero{padding:52px 0 46px;border-bottom:1px solid var(--border);
  margin-bottom:44px;position:relative}
.hero::before{
  content:"";position:absolute;
  top:-80px;left:-60px;right:-60px;bottom:-40px;
  background:radial-gradient(ellipse at 30% 40%, var(--accent-glow), transparent 60%);
  filter:blur(70px);opacity:.35;z-index:-1;pointer-events:none;
  animation:heroPulse 7s ease-in-out infinite;
}
@keyframes heroPulse{0%,100%{opacity:.25;transform:scale(1)}50%{opacity:.5;transform:scale(1.05)}}
.hero .tagline{display:inline-block;font-family:'JetBrains Mono',monospace;font-size:11.5px;
  color:var(--accent-2);letter-spacing:4px;text-transform:uppercase;margin-bottom:16px;
  padding:5px 12px;border-radius:20px;
  background:rgba(34,211,238,.08);border:1px solid rgba(34,211,238,.25);
  box-shadow:0 0 24px rgba(34,211,238,.15)}
.hero h1{font-family:'Space Grotesk','Inter',sans-serif;
  font-size:clamp(34px,5.5vw,60px);font-weight:700;
  letter-spacing:-1.5px;line-height:1.05;margin-bottom:18px;
  background:linear-gradient(135deg,var(--text) 15%,var(--accent) 55%,var(--accent-2) 90%);
  -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;
  filter:drop-shadow(0 0 30px var(--accent-glow))}
.hero p{color:var(--text-dim);font-size:16.5px;max-width:660px;line-height:1.65}
.hero .ghost-big{position:absolute;right:20px;top:30px;font-size:96px;
  opacity:.09;filter:blur(.5px) drop-shadow(0 0 40px var(--accent));
  animation:ghostBob 5s ease-in-out infinite;pointer-events:none}
@media(max-width:640px){.hero .ghost-big{font-size:64px;right:6px;top:20px;opacity:.06}}

/* ============= TOOLBAR ============= */
.toolbar{display:flex;flex-wrap:wrap;gap:14px;align-items:flex-start;
  justify-content:space-between;margin-bottom:32px}
.search-box{position:relative;flex:1;min-width:220px;max-width:420px}
.search-box input{width:100%;padding:12px 16px 12px 42px;background:var(--surface);
  border:1px solid var(--border);border-radius:12px;color:var(--text);
  font-size:14px;outline:none;transition:.22s;font-family:inherit}
.search-box input::placeholder{color:var(--text-faint)}
.search-box input:focus{border-color:var(--accent);box-shadow:0 0 0 4px var(--accent-soft),
  0 0 24px var(--accent-glow);background:var(--surface-2)}
.search-box svg{position:absolute;left:15px;top:50%;transform:translateY(-50%);
  width:16px;height:16px;color:var(--text-faint);pointer-events:none}
.tag-cloud{display:flex;flex-wrap:wrap;gap:7px;flex:1;justify-content:flex-end;
  max-height:120px;overflow-y:auto;padding:2px}
.tag-btn{padding:6px 13px;border-radius:20px;border:1px solid var(--border);
  background:var(--surface);color:var(--text-dim);font-size:12px;font-weight:600;
  cursor:pointer;transition:.2s;display:inline-flex;align-items:center;gap:6px;
  font-family:inherit;white-space:nowrap}
.tag-btn:hover{border-color:var(--accent);color:var(--text);transform:translateY(-1px);
  box-shadow:0 0 20px var(--accent-glow)}
.tag-btn.active{background:linear-gradient(135deg,var(--accent),var(--accent-2));
  border-color:transparent;color:#fff;box-shadow:0 4px 18px var(--accent-glow)}
.tag-btn .count{font-family:'JetBrains Mono',monospace;font-size:10px;opacity:.75;
  padding:1px 6px;border-radius:9px;background:rgba(255,255,255,.08)}
.tag-btn.active .count{background:rgba(255,255,255,.22);opacity:1}

/* ============= CARDS ============= */
.grid{display:grid;gap:24px}
@media(min-width:720px){.grid{grid-template-columns:repeat(2,1fr)}}
.card{background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);overflow:hidden;display:flex;flex-direction:column;
  transition:transform .35s cubic-bezier(.2,.7,.3,1), box-shadow .35s, border-color .35s;
  position:relative}
.card::before{content:"";position:absolute;inset:0;border-radius:var(--radius);
  padding:1px;background:linear-gradient(135deg,var(--accent),var(--accent-2),var(--accent-3));
  -webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);
  -webkit-mask-composite:xor;mask-composite:exclude;
  opacity:0;transition:opacity .35s;pointer-events:none;z-index:5}
.card:hover{transform:translateY(-6px);
  box-shadow:var(--shadow-lg), 0 0 60px -8px var(--accent-glow);
  border-color:transparent}
.card:hover::before{opacity:1}

/* ----- card cover: FULL IMAGE (no crop) with blurred ambient backdrop ----- */
.card-cover{
  position:relative;
  aspect-ratio:16/10;
  overflow:hidden;
  display:flex;align-items:center;justify-content:center;
  background:linear-gradient(135deg,var(--surface-hover),var(--bg-2));
  padding:16px;
}
.card-cover::before{
  content:"";position:absolute;inset:-40px;
  background-image:var(--cover, none);
  background-size:cover;background-position:center;
  filter:blur(32px) brightness(.5) saturate(1.8);
  opacity:.85;z-index:0;
}
.card-cover::after{
  content:"";position:absolute;inset:0;
  background:linear-gradient(180deg,transparent 45%,rgba(0,0,0,.4));
  z-index:2;pointer-events:none;
}
.card-cover img{
  position:relative;z-index:1;
  max-width:100%;max-height:100%;
  width:auto;height:auto;
  object-fit:contain;
  border-radius:8px;
  box-shadow:0 14px 40px rgba(0,0,0,.55), 0 0 30px var(--accent-glow);
  transition:transform .7s cubic-bezier(.2,.7,.3,1);
}
.card:hover .card-cover img{transform:scale(1.03)}
.card-cover .cover-placeholder{
  position:relative;z-index:1;
  width:100%;height:100%;
  display:flex;align-items:center;justify-content:center;
  font-size:64px;opacity:.4;
  border-radius:8px;
}
.card-featured{position:absolute;top:14px;left:14px;z-index:3;
  background:linear-gradient(135deg,var(--gold),#f59e0b);color:#000;
  font-size:10px;font-weight:800;padding:4px 11px;border-radius:20px;
  letter-spacing:1.2px;text-transform:uppercase;
  box-shadow:0 4px 16px rgba(251,191,36,.5)}

.card-body{padding:20px 22px 22px;flex:1;display:flex;flex-direction:column}
.card-meta{display:flex;align-items:center;gap:10px;font-size:11.5px;
  color:var(--text-faint);font-family:'JetBrains Mono',monospace;margin-bottom:12px}
.card-meta .dot{width:3px;height:3px;border-radius:50%;background:var(--text-faint)}
.card-title{font-family:'Space Grotesk','Inter',sans-serif;
  font-size:20px;font-weight:700;line-height:1.28;letter-spacing:-.4px;margin-bottom:10px}
.card-title a{color:var(--text);transition:.2s}
.card-title a:hover{color:var(--accent);text-shadow:0 0 24px var(--accent-glow)}
.card-excerpt{color:var(--text-dim);font-size:14px;line-height:1.65;flex:1;margin-bottom:16px}
.card-footer{display:flex;align-items:center;gap:11px;padding-top:15px;
  border-top:1px solid var(--border);font-size:12.5px}
.avatar{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;
  justify-content:center;font-size:13px;font-weight:700;color:#fff;flex-shrink:0;
  box-shadow:0 2px 10px rgba(0,0,0,.3), 0 0 20px -4px currentColor}
.card-author{color:var(--text-dim);font-weight:600;font-size:12.5px}
.card-rank{color:var(--text-faint);font-size:11px;margin-top:1px}
.card-tags{display:flex;gap:5px;margin-left:auto;flex-wrap:wrap;justify-content:flex-end}
.card-tags span{font-size:10px;padding:3px 9px;border-radius:12px;
  background:var(--accent-soft);color:var(--accent);font-weight:600;
  font-family:'JetBrains Mono',monospace;letter-spacing:.2px;
  border:1px solid transparent;transition:.2s}
.card:hover .card-tags span{border-color:var(--accent-soft)}

/* ============= POST VIEW ============= */
.post-header{max-width:780px;margin:0 auto 36px}
.post-tags-row{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px}
.post-tags-row span{font-size:11px;padding:4px 11px;border-radius:20px;
  background:var(--accent-soft);color:var(--accent);font-weight:600;
  font-family:'JetBrains Mono',monospace}
.post-header h1{font-family:'Space Grotesk','Inter',sans-serif;
  font-size:clamp(30px,5vw,48px);font-weight:700;letter-spacing:-1.3px;
  line-height:1.15;margin:0 0 22px}
.post-meta{display:flex;align-items:center;flex-wrap:wrap;gap:16px;
  padding:18px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border)}
.post-meta .author-block{display:flex;align-items:center;gap:12px}
.post-meta .avatar{width:44px;height:44px;font-size:17px}
.post-meta .author-name{font-weight:700;font-size:14.5px;color:var(--text)}
.post-meta .author-rank{font-size:12px;color:var(--text-dim);margin-top:1px}
.post-meta .meta-right{margin-left:auto;text-align:right;
  font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--text-faint)}
.post-meta .meta-right .read{color:var(--accent-2);margin-top:3px}

/* ----- post cover: FULL IMAGE, natural height, no crop ----- */
.post-cover{
  position:relative;
  border-radius:var(--radius);
  overflow:hidden;
  margin-bottom:36px;
  border:1px solid var(--border);
  box-shadow:var(--shadow-md), 0 0 60px -20px var(--accent-glow);
  background:var(--bg-2);
  display:flex;align-items:center;justify-content:center;
}
.post-cover::before{
  content:"";position:absolute;inset:-40px;
  background-image:var(--cover, none);
  background-size:cover;background-position:center;
  filter:blur(46px) brightness(.42) saturate(1.8);
  opacity:.85;z-index:0;
}
.post-cover img{
  position:relative;z-index:1;
  width:100%;
  height:auto;
  max-height:82vh;
  object-fit:contain;
  display:block;
}
.post-cover.no-img{
  height:200px;
  background:var(--grad, linear-gradient(135deg,#4c1d95,#0891b2));
}
.post-cover.no-img::before{display:none}
.post-cover.no-img span{position:relative;z-index:1;font-size:72px;opacity:.3;
  filter:grayscale(.2)}

.prose{max-width:780px;margin:0 auto;font-size:16.5px;line-height:1.85;color:var(--text)}
.prose>*+*{margin-top:1.1em}
.prose h2{font-family:'Space Grotesk','Inter',sans-serif;
  font-size:27px;font-weight:700;letter-spacing:-.5px;
  margin:42px 0 16px;padding-bottom:10px;border-bottom:1px solid var(--border);
  position:relative}
.prose h2::after{content:"";position:absolute;left:0;bottom:-1px;width:60px;height:2px;
  background:linear-gradient(90deg,var(--accent),transparent);
  box-shadow:0 0 16px var(--accent-glow)}
.prose h3{font-size:20px;font-weight:700;margin:32px 0 12px;letter-spacing:-.2px}
.prose h4{font-size:17px;font-weight:700;margin:26px 0 10px}
.prose p{margin-bottom:18px}
.prose ul, .prose ol{margin:0 0 20px 26px}
.prose li{margin-bottom:7px}
.prose li::marker{color:var(--accent)}
.prose blockquote{border-left:3px solid var(--accent);padding:14px 20px;
  margin:24px 0;background:var(--surface);border-radius:0 12px 12px 0;
  color:var(--text-dim);font-style:italic;position:relative;
  box-shadow:-4px 0 24px -8px var(--accent-glow)}
.prose blockquote::before{content:'"';position:absolute;top:-12px;left:12px;
  font-size:52px;line-height:1;color:var(--accent);opacity:.25;
  font-family:Georgia,serif}
.prose blockquote p:last-child{margin-bottom:0}
.prose code{font-family:'JetBrains Mono',monospace;background:var(--surface-hover);
  padding:2px 8px;border-radius:6px;font-size:14px;color:var(--accent-2);
  border:1px solid var(--border)}
.prose pre{background:var(--bg-2);border:1px solid var(--border);
  border-radius:12px;padding:18px 20px;overflow-x:auto;margin:24px 0;
  position:relative;box-shadow:var(--shadow-sm)}
.prose pre code{background:none;padding:0;color:var(--text);font-size:13.5px;
  line-height:1.65;border:none}
.prose img{border-radius:12px;margin:24px 0;border:1px solid var(--border);
  box-shadow:var(--shadow-md);width:100%;height:auto}
.prose a{font-weight:600;border-bottom:1px solid var(--accent-soft);transition:.2s}
.prose a:hover{border-bottom-color:var(--accent);text-shadow:0 0 16px var(--accent-glow)}
.prose hr{border:none;border-top:1px solid var(--border);margin:36px 0}
.prose table{width:100%;border-collapse:collapse;margin:24px 0;font-size:14.5px;
  border:1px solid var(--border);border-radius:12px;overflow:hidden}
.prose th, .prose td{padding:12px 16px;border-bottom:1px solid var(--border);text-align:left}
.prose th{background:var(--surface-2);font-weight:700;font-size:13px;
  text-transform:uppercase;letter-spacing:.5px;color:var(--text-dim)}
.prose tr:last-child td{border-bottom:none}

/* ============= RELATED ============= */
.related{max-width:780px;margin:56px auto 0}
.related h3{font-family:'Space Grotesk','Inter',sans-serif;
  font-size:17px;font-weight:700;margin-bottom:18px;
  display:flex;align-items:center;gap:9px}
.related-list{display:grid;gap:14px}
@media(min-width:600px){.related-list{grid-template-columns:repeat(3,1fr)}}
.related-item{background:var(--surface);border:1px solid var(--border);
  border-radius:14px;padding:16px;transition:.3s;display:flex;flex-direction:column;gap:8px}
.related-item:hover{border-color:var(--accent);transform:translateY(-4px);
  box-shadow:var(--shadow-md), 0 0 30px -8px var(--accent-glow)}
.related-item a{color:var(--text);font-weight:700;font-size:14px;
  line-height:1.4;transition:.2s}
.related-item a:hover{color:var(--accent)}
.related-item .date{font-size:11px;color:var(--text-faint);
  font-family:'JetBrains Mono',monospace;margin-top:auto}

/* ============= COMMENTS ============= */
.comments{max-width:780px;margin:64px auto 0;padding-top:36px;
  border-top:1px solid var(--border)}
.comments h3{font-family:'Space Grotesk','Inter',sans-serif;
  font-size:19px;font-weight:700;margin-bottom:24px;
  display:flex;align-items:center;gap:10px}
.comments h3 .count{background:var(--accent-soft);color:var(--accent);
  font-size:12px;padding:3px 11px;border-radius:14px;
  font-family:'JetBrains Mono',monospace;font-weight:700;
  box-shadow:0 0 20px -4px var(--accent-glow)}

.comment-form{margin-bottom:32px;background:var(--surface);border:1px solid var(--border);
  border-radius:16px;padding:18px 20px;transition:.25s}
.comment-form:focus-within{border-color:var(--accent);
  box-shadow:0 0 0 4px var(--accent-soft), 0 0 40px -10px var(--accent-glow)}
.comment-form .row{display:grid;grid-template-columns:1fr;gap:10px}
.comment-form input, .comment-form textarea{width:100%;padding:12px 15px;
  background:var(--bg-2);border:1px solid var(--border);border-radius:10px;
  color:var(--text);font-size:14px;font-family:inherit;outline:none;transition:.2s}
.comment-form input:focus, .comment-form textarea:focus{border-color:var(--accent)}
.comment-form textarea{resize:vertical;min-height:84px;line-height:1.6}
.comment-form button{padding:11px 26px;border-radius:10px;border:none;
  background:linear-gradient(135deg,var(--accent),var(--accent-2));
  color:#fff;font-weight:700;font-size:13.5px;cursor:pointer;
  transition:.22s;font-family:inherit;
  box-shadow:0 4px 20px var(--accent-glow)}
.comment-form button:hover:not(:disabled){transform:translateY(-1px);
  box-shadow:0 6px 26px var(--accent-glow)}
.comment-form button:disabled{opacity:.5;cursor:not-allowed}

.comment-list{display:flex;flex-direction:column;gap:20px}
.comment-thread{position:relative}
.comment{background:var(--surface);border:1px solid var(--border);
  border-radius:14px;padding:16px 18px;transition:.2s;position:relative}
.comment:hover{border-color:var(--border-hi);box-shadow:0 0 30px -14px var(--accent-glow)}
.comment.reply{margin-left:0;background:var(--surface-2);
  border-left:3px solid var(--accent);border-radius:0 12px 12px 0}
.comment-head{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.comment-head .avatar{width:32px;height:32px;font-size:13px}
.comment-author{font-weight:700;font-size:13.5px;color:var(--text)}
.comment-time{font-size:11px;color:var(--text-faint);
  font-family:'JetBrains Mono',monospace;margin-left:auto}
.comment-text{font-size:14.5px;color:var(--text);line-height:1.7;
  white-space:pre-wrap;word-break:break-word}
.comment-actions{margin-top:10px;display:flex;gap:8px}
.comment-actions button{background:none;border:none;color:var(--text-faint);
  font-size:12px;font-weight:600;cursor:pointer;padding:4px 10px;
  border-radius:8px;transition:.2s;font-family:inherit;
  display:inline-flex;align-items:center;gap:5px}
.comment-actions button:hover{color:var(--accent);background:var(--accent-soft)}
.comment-replies{margin-top:14px;margin-left:20px;padding-left:16px;
  border-left:2px solid var(--border);display:flex;flex-direction:column;gap:12px;
  position:relative}
.comment-replies::before{content:"";position:absolute;left:-2px;top:0;bottom:0;
  width:2px;background:linear-gradient(180deg,var(--accent),transparent 60%);
  opacity:.6;box-shadow:0 0 12px var(--accent-glow)}
.reply-slot{}

.reply-form{margin-top:12px;margin-left:20px;padding:14px 16px;
  background:var(--surface-2);border:1px solid var(--accent);
  border-radius:12px;display:flex;flex-direction:column;gap:9px;
  animation:slideIn .22s ease;
  box-shadow:0 0 30px -10px var(--accent-glow)}
@keyframes slideIn{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}
.reply-form input, .reply-form textarea{width:100%;padding:10px 13px;
  background:var(--bg-2);border:1px solid var(--border);border-radius:9px;
  color:var(--text);font-size:13.5px;font-family:inherit;outline:none;transition:.2s}
.reply-form input:focus, .reply-form textarea:focus{border-color:var(--accent)}
.reply-form textarea{resize:vertical;min-height:62px;line-height:1.55}
.reply-form-actions{display:flex;gap:8px;justify-content:flex-end}
.reply-form-actions button{padding:8px 18px;border-radius:9px;
  font-size:12.5px;font-weight:700;cursor:pointer;font-family:inherit;
  border:1px solid var(--border);background:var(--surface);color:var(--text-dim);
  transition:.2s}
.reply-form-actions button:hover{background:var(--surface-hover);color:var(--text)}
.reply-form-actions .btn-submit{background:linear-gradient(135deg,var(--accent),var(--accent-2));
  border-color:transparent;color:#fff}
.reply-form-actions .btn-submit:hover{transform:translateY(-1px);color:#fff}
.no-comments{color:var(--text-faint);font-size:13.5px;text-align:center;
  padding:28px;background:var(--surface);border:1px dashed var(--border);
  border-radius:14px}

/* ============= UTILITY ============= */
.empty{text-align:center;padding:80px 20px;color:var(--text-faint)}
.empty .icon{font-size:52px;margin-bottom:16px;filter:grayscale(.3)}
.empty p{font-size:15px}
.back-link{display:inline-flex;align-items:center;gap:7px;font-size:13.5px;
  font-weight:600;color:var(--text-dim);margin-bottom:26px;
  padding:8px 14px;border-radius:10px;transition:.2s}
.back-link:hover{color:var(--accent);background:var(--accent-soft);
  text-decoration:none;box-shadow:0 0 20px -6px var(--accent-glow)}
footer{border-top:1px solid var(--border);padding:34px 22px;text-align:center;
  color:var(--text-faint);font-size:12.5px;
  font-family:'JetBrains Mono',monospace;line-height:1.9;position:relative}
footer .ghost{color:var(--accent);font-size:14px;
  display:inline-block;animation:ghostBob 3.6s ease-in-out infinite}
footer .sub{opacity:.55;font-size:11px;margin-top:4px}
.chat-cta{display:inline-flex;align-items:center;gap:9px;padding:13px 26px;
  border-radius:12px;background:linear-gradient(135deg,var(--accent),var(--accent-2));
  color:#fff !important;font-weight:700;font-size:14px;transition:.25s;
  margin-top:22px;box-shadow:0 8px 32px var(--accent-glow)}
.chat-cta:hover{transform:translateY(-2px);box-shadow:0 14px 40px var(--accent-glow);
  text-decoration:none}
::-webkit-scrollbar{width:9px;height:9px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border-hi);border-radius:5px;
  border:2px solid var(--bg)}
::-webkit-scrollbar-thumb:hover{background:var(--accent)}
::selection{background:var(--accent);color:#fff}

@media(max-width:640px){
  .container{padding:24px 16px 60px}
  .hero{padding:32px 0 30px;margin-bottom:32px}
  .nav-inner{padding:11px 16px;gap:12px}
  .nav-logo span:last-child{display:none}
  .nav-chat{padding:8px 14px;font-size:12.5px}
  .card-body{padding:16px 18px 18px}
  .card-title{font-size:18px}
  .toolbar{flex-direction:column;align-items:stretch}
  .tag-cloud{justify-content:flex-start;max-height:none}
  .comment-replies{margin-left:10px;padding-left:12px}
  .reply-form{margin-left:10px}
  .card-cover{padding:10px}
}
</style>
</head>
<body>
<div class="bg-deco"></div>
<div class="ghost-swarm" aria-hidden="true">
  <span>👻</span><span>👻</span><span>👻</span><span>👻</span><span>👻</span>
</div>

<nav>
  <div class="nav-inner">
    <a href="/" class="nav-logo">{{ logo_html }}<span>{{ site_name }}</span></a>
    <div class="nav-spacer"></div>
    {{ chat_link }}
    <div class="nav-links">
      <a href="/" class="{{ active_home }}">Blog</a>
      <a href="/about" class="{{ active_about }}">About</a>
      <button class="theme-toggle" id="themeBtn" title="Toggle theme" aria-label="Toggle theme">◐</button>
    </div>
  </div>
</nav>

<div class="container">
{{ content|safe }}
</div>

<footer>
  <div><span class="ghost">👻</span> {{ site_name }} — {{ site_tagline }}</div>
  <div class="sub">Powered by FastAPI · Content via remote JSON</div>
</footer>

<script>
(function(){
  var saved = localStorage.getItem('ghost_theme') || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
  var b = document.getElementById('themeBtn');
  if (b) b.textContent = saved === 'dark' ? '◐' : '☀';
})();
document.getElementById('themeBtn').addEventListener('click', function(){
  var cur = document.documentElement.getAttribute('data-theme');
  var next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('ghost_theme', next);
  this.textContent = next === 'dark' ? '◐' : '☀';
});

/* ---------- SEARCH & TAGS ---------- */
var searchInput = document.getElementById('searchInput');
if (searchInput) {
  var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
  var tagBtns = Array.prototype.slice.call(document.querySelectorAll('.tag-btn'));
  var activeTag = 'all';
  function applyFilter(){
    var q = searchInput.value.trim().toLowerCase();
    var shown = 0;
    cards.forEach(function(c){
      var text = (c.dataset.title + ' ' + c.dataset.excerpt + ' ' + c.dataset.tags).toLowerCase();
      var matchQ = !q || text.indexOf(q) !== -1;
      var tags = (c.dataset.tags || '').split(',').map(function(t){return t.trim()}).filter(Boolean);
      var matchT = activeTag === 'all' || tags.indexOf(activeTag) !== -1;
      var vis = matchQ && matchT;
      c.style.display = vis ? '' : 'none';
      if (vis) shown++;
    });
    var empty = document.getElementById('searchEmpty');
    if (empty) empty.style.display = shown ? 'none' : 'block';
  }
  searchInput.addEventListener('input', applyFilter);
  tagBtns.forEach(function(b){
    b.addEventListener('click', function(){
      tagBtns.forEach(function(x){ x.classList.remove('active'); });
      b.classList.add('active');
      activeTag = b.dataset.tag;
      applyFilter();
    });
  });
}

/* ---------- MAIN COMMENT FORM ---------- */
var cForm = document.getElementById('commentForm');
if (cForm) {
  cForm.addEventListener('submit', function(e){
    e.preventDefault();
    var author = document.getElementById('cAuthor').value.trim();
    var text = document.getElementById('cText').value.trim();
    if (!author || !text) return;
    submitComment(cForm.dataset.slug, author, text, null, cForm, function(ok){
      if (ok) document.getElementById('cText').value = '';
    });
  });
}

/* ---------- THREADED COMMENT SUBMIT ---------- */
function submitComment(slug, author, text, parentId, formEl, done){
  var btn = formEl.querySelector('button[type="submit"]');
  if (btn) { btn.disabled = true; btn.dataset.orig = btn.textContent; btn.textContent = 'Posting…'; }
  fetch('/api/comment', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({slug: slug, author: author, text: text, parent_id: parentId})
  })
  .then(function(r){ return r.json(); })
  .then(function(j){
    if (!j.ok) throw new Error(j.error || 'Error');
    if (done) done(true);
    insertComment(j.comment, parentId);
  })
  .catch(function(err){
    alert('Could not post comment: ' + err.message);
    if (done) done(false);
  })
  .finally(function(){
    if (btn) { btn.disabled = false; btn.textContent = btn.dataset.orig || 'Post Comment'; }
  });
}

function insertComment(c, parentId){
  var list = document.getElementById('commentList');
  if (!list) return;
  var emptyEl = list.querySelector('.no-comments');
  if (emptyEl) emptyEl.remove();

  var html =
    '<div class="comment-head">' +
      '<span class="avatar" style="background:' + c.color + '">' + esc(c.initial) + '</span>' +
      '<span class="comment-author">' + esc(c.author) + '</span>' +
      '<span class="comment-time">' + c.time + '</span>' +
    '</div>' +
    '<div class="comment-text">' + esc(c.text) + '</div>' +
    '<div class="comment-actions">' +
      '<button type="button" onclick="showReplyForm(' + c.id + ', \'' + escAttr(c.author) + '\')">↩ Reply</button>' +
    '</div>' +
    '<div class="reply-slot" id="reply-slot-' + c.id + '"></div>';

  var div = document.createElement('div');
  div.className = 'comment' + (parentId ? ' reply' : '');
  div.id = 'comment-' + c.id;
  div.innerHTML = html;

  if (parentId) {
    var parent = document.getElementById('comment-' + parentId);
    if (parent) {
      var replies = parent.parentElement.querySelector(':scope > .comment-replies');
      if (!replies) {
        replies = document.createElement('div');
        replies.className = 'comment-replies';
        parent.parentElement.appendChild(replies);
      }
      replies.appendChild(div);
    } else {
      list.appendChild(div);
    }
  } else {
    var wrap = document.createElement('div');
    wrap.className = 'comment-thread';
    wrap.appendChild(div);
    list.appendChild(wrap);
  }
  div.scrollIntoView({ behavior:'smooth', block:'center' });

  var cnt = document.getElementById('commentCount');
  if (cnt) cnt.textContent = parseInt(cnt.textContent) + 1;
}

function showReplyForm(commentId, authorName){
  document.querySelectorAll('.reply-form').forEach(function(f){ f.remove(); });
  var slot = document.getElementById('reply-slot-' + commentId);
  if (!slot) return;

  var form = document.createElement('form');
  form.className = 'reply-form';
  form.innerHTML =
    '<input placeholder="Your name" maxlength="24" required>' +
    '<textarea placeholder="Reply to ' + escAttr(authorName) + '…" maxlength="1000" required></textarea>' +
    '<div class="reply-form-actions">' +
      '<button type="button" class="btn-cancel">Cancel</button>' +
      '<button type="submit" class="btn-submit">Reply</button>' +
    '</div>';
  slot.appendChild(form);
  var input = form.querySelector('input');
  input.focus();

  form.querySelector('.btn-cancel').addEventListener('click', function(){ form.remove(); });
  form.addEventListener('submit', function(e){
    e.preventDefault();
    var author = form.querySelector('input').value.trim();
    var text = form.querySelector('textarea').value.trim();
    if (!author || !text) return;
    var slug = (document.getElementById('commentForm') || {}).dataset?.slug || '';
    submitComment(slug, author, text, commentId, form, function(ok){
      if (ok) form.remove();
    });
  });
}

function esc(s){
  return String(s).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}
function escAttr(s){ return esc(s).replace(/\\/g, '\\\\'); }

/* ---------- SMOOTH REVEAL (lightweight) ---------- */
if ('IntersectionObserver' in window) {
  var obs = new IntersectionObserver(function(entries){
    entries.forEach(function(en){
      if (en.isIntersecting) { en.target.style.opacity = '1';
        en.target.style.transform = 'none'; obs.unobserve(en.target); }
    });
  }, { rootMargin:'0px 0px -40px 0px', threshold:.05 });
  document.querySelectorAll('.card').forEach(function(c,i){
    c.style.opacity = '0';
    c.style.transform = 'translateY(14px)';
    c.style.transition = 'opacity .5s ' + (Math.min(i,10)*40) + 'ms, transform .5s ' + (Math.min(i,10)*40) + 'ms';
    obs.observe(c);
  });
}
</script>
</body>
</html>
"""


# ============================================================
#                  TEMPLATE RENDERING
# ============================================================
def render_page(content: str, site: dict, active: str = "home", page_title: str = "") -> str:
    name = site.get("name", "Ghost Union")
    title = f"{page_title} · {name}" if page_title else name
    tagline = site.get("tagline", "Silent. Precise. Unified.")
    description = site.get("description", "")

    logo_html = (
        f'<img src="{html.escape(site["logo"])}" alt="">'
        if site.get("logo")
        else '<span class="ghost-icon">👻</span>'
    )
    chat_link = (
        f'<a class="nav-chat" href="{html.escape(site["chat_url"])}" target="_blank" rel="noopener">💬 Chat Room ↗</a>'
        if site.get("chat_url")
        else ""
    )

    out = HTML
    out = out.replace("{{ site_description }}", html.escape(description, quote=True))
    out = out.replace("{{ page_title }}", html.escape(title))
    out = out.replace("{{ logo_html }}", logo_html)
    out = out.replace("{{ site_name }}", html.escape(name))
    out = out.replace("{{ site_tagline }}", html.escape(tagline))
    out = out.replace("{{ chat_link }}", chat_link)
    out = out.replace("{{ active_home }}", "active" if active == "home" else "")
    out = out.replace("{{ active_about }}", "active" if active == "about" else "")
    out = out.replace("{{ content|safe }}", content)
    return out


# ============================================================
#                  HTML BUILDERS
# ============================================================
def build_home(data: dict) -> str:
    site = get_site(data)
    posts = get_posts(data)
    tags = tag_counts(posts)

    parts = [f"""
    <div class="hero">
      <span class="ghost-big" aria-hidden="true">👻</span>
      <div class="tagline">{html.escape(site.get('tagline', 'Silent. Precise. Unified.'))}</div>
      <h1>{html.escape(site.get('name', 'Ghost Union'))}</h1>
      <p>{html.escape(site.get('description', 'Field notes from the operators of the Ghost Union.'))}</p>
      {f'<a class="chat-cta" href="{html.escape(site["chat_url"])}" target="_blank" rel="noopener">💬 Join the Chat Room</a>' if site.get('chat_url') else ''}
    </div>
    """]

    parts.append('<div class="toolbar"><div class="search-box">'
                 '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
                 'stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>'
                 '<input id="searchInput" placeholder="Search posts..." autocomplete="off">'
                 '</div>')

    if tags:
        parts.append('<div class="tag-cloud">')
        parts.append('<button class="tag-btn active" data-tag="all">All</button>')
        for t, n in tags.items():
            parts.append(
                f'<button class="tag-btn" data-tag="{html.escape(t, quote=True)}">'
                f'{html.escape(t)} <span class="count">{n}</span></button>'
            )
        parts.append('</div>')

    parts.append('</div>')

    if not posts:
        parts.append("""
        <div class="empty">
          <div class="icon">👻</div>
          <p>No posts yet. The Ghost Union is silent… for now.</p>
        </div>
        """)
    else:
        parts.append('<div class="grid">')
        for p in posts:
            cover = p.get("cover", "")
            slug = p.get("slug", "")
            cover_style = ""
            if cover:
                # Full image displayed with blurred ambient backdrop
                cover_esc = html.escape(cover, quote=True)
                cover_html = f'<img src="{cover_esc}" alt="" loading="lazy">'
                cover_style = f' style="--cover:url(&quot;{cover_esc}&quot;)"'
            else:
                grad = cover_gradient(slug)
                cover_html = (f'<div class="cover-placeholder" '
                              f'style="background:{grad}">👻</div>')
            featured = '<span class="card-featured">Featured</span>' if p.get("featured") else ""
            author = p.get("author", "Unknown")
            color = author_avatar(author)
            initial = author_initial(author)
            tags_html = "".join(f'<span>{html.escape(t)}</span>' for t in p.get("tags", [])[:3])
            title = html.escape(p.get("title", "Untitled"))
            ex = html.escape(excerpt(p), quote=True)
            date = html.escape(format_date(p.get("date", "")))
            rank = html.escape(p.get("rank", ""))
            tags_data = html.escape(",".join(p.get("tags", [])), quote=True)
            rt = reading_time(p)
            parts.append(f"""
            <article class="card" data-title="{title}" data-excerpt="{ex}" data-tags="{tags_data}">
              <div class="card-cover"{cover_style}>{cover_html}{featured}</div>
              <div class="card-body">
                <div class="card-meta"><span>{date}</span><span class="dot"></span><span>{rt} min read</span></div>
                <h2 class="card-title"><a href="/post/{html.escape(slug)}">{title}</a></h2>
                <p class="card-excerpt">{ex}</p>
                <div class="card-footer">
                  <span class="avatar" style="background:{color}">{initial}</span>
                  <div>
                    <div class="card-author">{html.escape(author)}</div>
                    <div class="card-rank">{rank}</div>
                  </div>
                  <div class="card-tags">{tags_html}</div>
                </div>
              </div>
            </article>
            """)
        parts.append("</div>")
        parts.append('<div class="empty" id="searchEmpty" style="display:none">'
                     '<div class="icon">🔍</div><p>No posts match your search.</p></div>')

    return render_page("\n".join(parts), site, active="home")


def render_comment_node(c: dict) -> str:
    c_color = author_avatar(c["author"])
    c_initial = author_initial(c["author"])
    author_attr = html.escape(c["author"], quote=True).replace("'", "\\'")

    replies_html = ""
    if c.get("replies"):
        replies_html = '<div class="comment-replies">' + "".join(
            render_comment_node(r) for r in c["replies"]
        ) + '</div>'

    return f"""
    <div class="comment{' reply' if c.get('parent_id') else ''}" id="comment-{c['id']}">
      <div class="comment-head">
        <span class="avatar" style="background:{c_color}">{c_initial}</span>
        <span class="comment-author">{html.escape(c['author'])}</span>
        <span class="comment-time">{html.escape(c['time'])}</span>
      </div>
      <div class="comment-text">{html.escape(c['text'])}</div>
      <div class="comment-actions">
        <button type="button" onclick="showReplyForm({c['id']}, '{author_attr}')">↩ Reply</button>
      </div>
      <div class="reply-slot" id="reply-slot-{c['id']}"></div>
      {replies_html}
    </div>
    """


def build_post(data: dict, post: dict) -> str:
    site = get_site(data)
    author = post.get("author", "Unknown")
    color = author_avatar(author)
    initial = author_initial(author)
    tags_html = "".join(f'<span>{html.escape(t)}</span>' for t in post.get("tags", []))
    cover = post.get("cover", "")
    slug = post.get("slug", "")

    if cover:
        # Full image, natural height, no crop, with blurred ambient backdrop
        cover_esc = html.escape(cover, quote=True)
        cover_html = (f'<div class="post-cover" style="--cover:url(&quot;{cover_esc}&quot;)">'
                      f'<img src="{cover_esc}" alt=""></div>')
    else:
        grad = cover_gradient(slug)
        cover_html = (f'<div class="post-cover no-img" style="--grad:{grad}">'
                      f'<span>👻</span></div>')

    content_html = render_markdown(post.get("content", ""))
    comments = get_comments(slug)
    total_comments = count_all(comments)
    tree = build_comment_tree(comments)
    related = related_posts(data, post)
    rt = reading_time(post)

    parts = [f"""
    <a class="back-link" href="/">← Back to all posts</a>
    <article>
      <header class="post-header">
        <div class="post-tags-row">{tags_html}</div>
        <h1>{html.escape(post.get('title', 'Untitled'))}</h1>
        <div class="post-meta">
          <div class="author-block">
            <span class="avatar" style="background:{color}">{initial}</span>
            <div>
              <div class="author-name">{html.escape(author)}</div>
              <div class="author-rank">{html.escape(post.get('rank', ''))} · {html.escape(post.get('team', ''))}</div>
            </div>
          </div>
          <div class="meta-right">
            <div>{html.escape(format_date(post.get('date', '')))}</div>
            <div class="read">◷ {rt} min read</div>
          </div>
        </div>
      </header>
      {cover_html}
      <div class="prose">{content_html}</div>
    </article>
    """]

    if related:
        parts.append('<div class="related"><h3>🔗 Related Posts</h3><div class="related-list">')
        for r in related:
            parts.append(f"""
            <div class="related-item">
              <a href="/post/{html.escape(r.get('slug',''))}">{html.escape(r.get('title','Untitled'))}</a>
              <div class="date">{html.escape(format_date(r.get('date','')))}</div>
            </div>
            """)
        parts.append("</div></div>")

    parts.append(f"""
    <section class="comments">
      <h3>💬 Comments <span class="count" id="commentCount">{total_comments}</span></h3>
      <form class="comment-form" id="commentForm" data-slug="{html.escape(slug)}">
        <div class="row">
          <input id="cAuthor" placeholder="Your name" maxlength="24" required>
          <textarea id="cText" placeholder="Share your thoughts with the Union…" maxlength="1000" required></textarea>
        </div>
        <div style="margin-top:10px">
          <button type="submit">Post Comment</button>
        </div>
      </form>
      <div class="comment-list" id="commentList">
    """)
    if tree:
        for node in tree:
            parts.append('<div class="comment-thread">' + render_comment_node(node) + '</div>')
    else:
        parts.append('<div class="no-comments">No comments yet. Be the first to speak.</div>')
    parts.append("</div></section>")

    return render_page("\n".join(parts), site, active="home", page_title=post.get("title", "Post"))


def build_about(data: dict) -> str:
    site = get_site(data)
    chat_btn = (
        f'<a class="chat-cta" href="{html.escape(site["chat_url"])}" '
        f'target="_blank" rel="noopener">💬 Enter the Chat Room</a>'
        if site.get("chat_url") else ""
    )
    parts = [f"""
    <div class="hero">
      <span class="ghost-big" aria-hidden="true">👻</span>
      <div class="tagline">About</div>
      <h1>Ghost Union</h1>
      <p>{html.escape(site.get('description', ''))}</p>
    </div>
    <div class="prose" style="max-width:780px;margin:0 auto">
      <h2>Our Values</h2>
      <p><strong>Silence.</strong> We move without noise. No egos, no theatrics — just precision.</p>
      <p><strong>Precision.</strong> Every action is deliberate. Every word has weight.</p>
      <p><strong>Unity.</strong> We are one organism. The Union comes before the individual.</p>
      <p><strong>Trust.</strong> Earned through action, never demanded. Layers of clearance exist for a reason.</p>
      <h2>The Field Notes</h2>
      <p>This blog is where operators of the Ghost Union share protocols, operations, and lessons from the field. No fluff. No noise. Only what matters.</p>
      <blockquote>Stay silent. Stay sharp. Stay unified.</blockquote>
      {chat_btn}
    </div>
    """]
    return render_page("\n".join(parts), site, active="about", page_title="About")


# ============================================================
#                  CACHE-BUSTING HELPER
# ============================================================
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def html_no_cache(body: str) -> HTMLResponse:
    return HTMLResponse(content=body, headers=NO_CACHE_HEADERS)


# ============================================================
#                  ROUTES
# ============================================================
@app.get("/", response_class=HTMLResponse)
async def home():
    data = await fetch_content()
    return html_no_cache(build_home(data))


@app.get("/post/{slug}", response_class=HTMLResponse)
async def post_view(slug: str):
    data = await fetch_content()
    post = find_post(data, slug)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return html_no_cache(build_post(data, post))


@app.get("/about", response_class=HTMLResponse)
async def about():
    data = await fetch_content()
    return html_no_cache(build_about(data))


@app.get("/api/posts")
async def api_posts():
    data = await fetch_content()
    return JSONResponse(
        {"site": get_site(data), "posts": get_posts(data)},
        headers=NO_CACHE_HEADERS,
    )


@app.get("/api/posts/{slug}")
async def api_post(slug: str):
    data = await fetch_content()
    post = find_post(data, slug)
    if not post:
        return JSONResponse(
            {"ok": False, "error": "Not found"},
            status_code=404,
            headers=NO_CACHE_HEADERS,
        )
    return JSONResponse({"ok": True, "post": post}, headers=NO_CACHE_HEADERS)


@app.post("/api/comment")
async def api_comment(payload: dict):
    slug = (payload.get("slug") or "").strip()
    author = (payload.get("author") or "").strip()
    text = (payload.get("text") or "").strip()
    parent_id = payload.get("parent_id")
    if not slug or not author or not text:
        return JSONResponse({"ok": False, "error": "Missing fields"}, status_code=400)
    if len(author) > 24 or len(text) > 1000:
        return JSONResponse({"ok": False, "error": "Too long"}, status_code=400)

    pid = None
    if parent_id is not None:
        try:
            pid = int(parent_id)
        except (TypeError, ValueError):
            pid = None
        if pid is not None:
            exists = any(c["id"] == pid for c in get_comments(slug))
            if not exists:
                pid = None

    c = add_comment(slug, author, text, pid)
    return JSONResponse({
        "ok": True,
        "comment": {
            "id": c["id"], "author": c["author"], "text": c["text"],
            "time": c["time"], "parent_id": c["parent_id"],
            "color": author_avatar(c["author"]),
            "initial": author_initial(c["author"]),
        }
    })


@app.get("/api/comments/{slug}")
async def api_comments(slug: str):
    return JSONResponse(
        {"ok": True, "comments": get_comments(slug)},
        headers=NO_CACHE_HEADERS,
    )


@app.get("/refresh")
async def refresh():
    try:
        await fetch_content(force=True)
    except Exception as e:
        print(f"⚠️  Refresh failed: {e}")
    return RedirectResponse("/?refreshed=1", status_code=303)


@app.on_event("startup")
async def startup():
    try:
        await fetch_content(force=True)
        print("👻 Ghost Union Blog ready — content cached from remote JSON")
    except Exception as e:
        print(f"⚠️  Could not fetch content on startup: {e}")


# ============================================================
#                  RUN
# ============================================================
if __name__ == "__main__":
    import uvicorn
    print("👻 Ghost Union Blog v2.1 — Haunted Edition")
    print("→ http://0.0.0.0:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)