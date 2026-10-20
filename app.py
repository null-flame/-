"""
👻 Ghost Union Blog v2.7 — Light + Reliable Media Edition
============================================================
  ⚙ Remote JSON content, threaded comments, view counter, admin panel.
  ⚙ Light & Dark themes.
  ⚙ Full RTL / LTR with Bidi-Invisible isolation.
  ⚙ ★ MEDIA (v2.7):
       - ![alt](url.ogg)  → native <audio> player (plays everywhere it can)
       - Explicit ⬇ Download button next to player (always visible)
       - Multi-source: url1.ogg|url2.m4a  → first supported is used
       - Download goes through /download proxy → bypasses CDN CORS/hotlink
       - Streamed response (zero RAM), attachment Content-Disposition
  ⚙ ★ LIGHTER (v2.7):
       - Removed SVG-noise overlay, fixed backgrounds, ghost swarm,
         scroll-progress bar, card reveal animations, conic-gradient logo
       - Fonts reduced from 11 to 4 family/weight combos
  ⚙ Zero extra dependencies.
============================================================
"""
import asyncio
import hashlib
import html
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse,
)

app = FastAPI(title="Ghost Union Blog")

# ============================================================
#                     CONFIG
# ============================================================
CONTENT_URL = "https://raw.githubusercontent.com/null-flame/-/refs/heads/main/a.json"
CACHE_TTL = 50

ADMIN_PASSWORD = "Gh0st#9x7"
ADMIN_SESSION_TTL = 4 * 3600

RTL_LANGS = {"fa", "ar", "he", "ur", "ps", "sd", "yi", "dv"}

# ============================================================
#                     IN-MEMORY STORES
# ============================================================
_cache: dict = {"data": None, "ts": 0, "etag": None}
_comments: dict = {}
_comment_id = 0
_views: dict = {}
_ips: dict = {}
_admin_sessions: dict = {}


# ============================================================
#                    ANALYTICS HELPERS
# ============================================================
def get_client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    real = request.headers.get("x-real-ip") or request.headers.get("cf-connecting-ip")
    if real:
        return real.strip()
    return request.client.host if request.client else "unknown"


def record_visit(ip: str, ua: str, path: str):
    now = time.time()
    entry = _ips.get(ip)
    if entry is None:
        _ips[ip] = {
            "first_seen": now, "last_seen": now, "views": 1,
            "name": None, "comments": [], "user_agent": ua[:220],
            "pages": [path],
        }
    else:
        entry["last_seen"] = now
        entry["views"] = entry.get("views", 0) + 1
        entry.setdefault("pages", []).append(path)
        if len(entry["pages"]) > 60:
            entry["pages"] = entry["pages"][-60:]
        if not entry.get("user_agent"):
            entry["user_agent"] = ua[:220]


def record_commenter(ip: str, name: str, text: str):
    now = time.time()
    entry = _ips.get(ip)
    if entry is None:
        _ips[ip] = {
            "first_seen": now, "last_seen": now, "views": 0,
            "name": name, "comments": [text[:300]],
            "user_agent": "", "pages": [],
        }
    else:
        entry["name"] = name
        entry["last_seen"] = now
        entry.setdefault("comments", []).append(text[:300])
        if len(entry["comments"]) > 25:
            entry["comments"] = entry["comments"][-25:]


def bump_view(slug: str):
    _views[slug] = _views.get(slug, 0) + 1


# ============================================================
#              CONTENT FETCHING
# ============================================================
def _blocking_fetch(etag: Optional[str], bust: bool = False):
    url = CONTENT_URL
    if bust:
        url = f"{CONTENT_URL}?v={int(time.time() * 1000)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "GhostUnionBlog/2.7",
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

_AUDIO_RE = re.compile(
    r"\.(ogg|oga|opus|mp3|wav|m4a|aac|flac|weba)(?:[?#].*)?$",
    re.IGNORECASE,
)
_VIDEO_RE = re.compile(
    r"\.(mp4|m4v|webm|ogv|mov)(?:[?#].*)?$",
    re.IGNORECASE,
)


def _safe_url(url: str) -> str:
    u = url.strip()
    if u.lower().startswith(("javascript:", "vbscript:", "data:text/html")):
        return "#"
    return html.escape(u, quote=True)


def _attr(s: str) -> str:
    return (s or "").replace('"', "&quot;").replace("'", "&#39;")


def _download_url(url: str) -> str:
    """Same-origin download link through /download proxy (SSRF-safe)."""
    filename = url.split("/")[-1].split("?")[0] or "download"
    qs = urllib.parse.urlencode({"url": url, "name": filename})
    return f"/download?{qs}"


def _render_media_inline(alt: str, raw_url: str) -> str:
    urls = [u.strip() for u in raw_url.split("|") if u.strip()]
    if not urls:
        return ""
    first = urls[0]
    safe_first = _safe_url(first)
    alt_safe = _attr(alt)
    dl = html.escape(_download_url(first), quote=True)

    if _AUDIO_RE.search(first):
        sources = "".join(f'<source src="{_safe_url(u)}">' for u in urls)
        return (
            f'<div class="media-wrap">'
            f'<audio controls preload="metadata" class="audio-player" '
            f'aria-label="{alt_safe}">{sources}</audio>'
            f'<div class="media-actions">'
            f'<a class="dl-btn" href="{dl}" download rel="noopener">⬇ دانلود صدا</a>'
            f'</div></div>'
        )

    if _VIDEO_RE.search(first):
        sources = "".join(f'<source src="{_safe_url(u)}">' for u in urls)
        return (
            f'<div class="media-wrap">'
            f'<video controls preload="metadata" class="video-player" '
            f'aria-label="{alt_safe}">{sources}</video>'
            f'<div class="media-actions">'
            f'<a class="dl-btn" href="{dl}" download rel="noopener">⬇ دانلود ویدیو</a>'
            f'</div></div>'
        )

    return f'<img src="{safe_first}" alt="{alt_safe}" loading="lazy">'


def _render_inline(text: str) -> str:
    text = html.escape(text, quote=False)
    code_spans = []

    def _stash_code(m):
        code_spans.append(m.group(1))
        return f"\x00CODE{len(code_spans) - 1}\x00"

    text = re.sub(r"`([^`]+)`", _stash_code, text)
    text = re.sub(
        r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)",
        lambda m: _render_media_inline(m.group(1), m.group(2)),
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
                items.append(f"<li>{_render_inline(re.sub(r'^\s*[-*+]\s+', '', lines[i]))}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue

        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < n and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(f"<li>{_render_inline(re.sub(r'^\s*\d+\.\s+', '', lines[i]))}</li>")
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
    return max(1, round(len(text.split()) / 220))


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
    return f"hsl({h % 360}, 65%, 48%)"


def author_initial(name: str) -> str:
    return (name or "?").strip()[:1].upper()


def cover_gradient(slug: str) -> str:
    h = int(hashlib.md5((slug or "?").encode()).hexdigest(), 16)
    return f"linear-gradient(135deg, hsl({h % 360},55%,22%), hsl({(h + 70) % 360},60%,36%))"


def detect_dir(site: dict, override: Optional[str] = None) -> str:
    if override in ("rtl", "ltr"):
        return override
    explicit = (site.get("dir") or "").lower().strip()
    if explicit in ("rtl", "ltr"):
        return explicit
    lang = (site.get("lang") or "en").lower().split("-")[0].strip()
    return "rtl" if lang in RTL_LANGS else "ltr"


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
#               ADMIN AUTH HELPERS
# ============================================================
def admin_authed(request: Request) -> bool:
    token = request.cookies.get("ghost_admin")
    if not token:
        return False
    exp = _admin_sessions.get(token)
    if not exp or time.time() > exp:
        _admin_sessions.pop(token, None)
        return False
    return True


# ============================================================
#     HTML TEMPLATE  (Light + Media Edition)
# ============================================================
HTML = r"""<!DOCTYPE html>
<html lang="{{ lang }}" dir="{{ dir }}" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="{{ site_description }}">
<meta name="color-scheme" content="dark light">
<title>{{ page_title }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;700&family=JetBrains+Mono:wght@400&family=Space+Grotesk:wght@700&display=swap" rel="stylesheet">
<style>
:root, [data-theme="dark"] {
  --bg:#05070c; --bg-2:#0a0d16; --surface:#0f1420; --surface-2:#141a28;
  --surface-hover:#1a2235; --border:#1b2333; --border-hi:#2a3550;
  --text:#e9eef8; --text-dim:#96a3ba; --text-faint:#5b6884;
  --accent:#a78bfa; --accent-2:#22d3ee;
  --accent-soft:rgba(167,139,250,.13); --accent-glow:rgba(167,139,250,.32);
  --gold:#fbbf24;
  --radius:14px;
  --shadow-sm:0 2px 6px rgba(0,0,0,.32);
  --shadow-md:0 8px 22px rgba(0,0,0,.4);
}
[data-theme="light"] {
  --bg:#f6f7fc; --bg-2:#eef1f9; --surface:#ffffff; --surface-2:#f7f8fd;
  --surface-hover:#eef1fa; --border:#e3e7f1; --border-hi:#cbd3e6;
  --text:#12172a; --text-dim:#57617a; --text-faint:#8a93ac;
  --accent:#6d28d9; --accent-2:#0891b2;
  --accent-soft:rgba(109,40,217,.10); --accent-glow:rgba(109,40,217,.2);
  --gold:#d97706;
  --shadow-sm:0 2px 6px rgba(20,26,43,.06);
  --shadow-md:0 8px 18px rgba(20,26,43,.08);
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth;-webkit-text-size-adjust:100%}
body{
  font-family:'Vazirmatn',system-ui,-apple-system,'Segoe UI',Tahoma,sans-serif;
  color:var(--text); line-height:1.7; min-height:100vh;
  overflow-x:hidden; position:relative;
  text-rendering:optimizeLegibility;
  background-color:var(--bg);
  background-image:radial-gradient(ellipse 900px 700px at 12% 8%, var(--accent-glow) 0%, transparent 55%);
  transition:background-color .25s, color .25s;
}
html[dir="rtl"] body{ direction:rtl; text-align:right; }
html[dir="ltr"] body{ direction:ltr; text-align:left; }
a{color:var(--accent);text-decoration:none}
img{max-width:100%;display:block}
h1,h2,h3,h4{text-wrap:balance;}
p{text-wrap:pretty;}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:6px;}
:focus:not(:focus-visible){outline:none}
::selection{background:var(--accent);color:#fff}

nav{position:sticky;top:0;z-index:50;
  background:color-mix(in srgb, var(--bg) 92%, transparent);
  border-bottom:1px solid var(--border);}
.nav-inner{max-width:1120px;margin:0 auto;padding:13px 22px;
  display:flex;align-items:center;gap:18px}
.nav-logo{display:flex;align-items:center;gap:11px;font-weight:700;
  font-size:16px;color:var(--text);letter-spacing:-.3px;flex-shrink:0}
.nav-logo img{width:36px;height:36px;border-radius:10px;object-fit:cover}
.nav-logo .ghost-icon{width:36px;height:36px;border-radius:10px;
  background:linear-gradient(135deg,var(--accent),var(--accent-2));
  display:flex;align-items:center;justify-content:center;font-size:19px;}
.nav-spacer{flex:1}
.nav-links{display:flex;gap:6px;align-items:center}
.nav-links a{padding:8px 14px;border-radius:10px;font-size:13.5px;font-weight:600;
  color:var(--text-dim);transition:color .15s, background .15s;}
.nav-links a:hover{color:var(--text);background:var(--surface-hover)}
.nav-links a.active{color:var(--accent);background:var(--accent-soft)}
.nav-chat{display:inline-flex;align-items:center;gap:7px;padding:9px 16px;
  border-radius:11px;font-size:13.5px;font-weight:700;
  background:linear-gradient(135deg,var(--accent),var(--accent-2));
  color:#fff !important; white-space:nowrap;
  transition:transform .15s;}
.nav-chat:hover{transform:translateY(-1px);}
.icon-btn{width:38px;height:38px;border-radius:11px;border:1px solid var(--border);
  background:var(--surface);color:var(--text);font-size:15px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;
  transition:background .15s, border-color .15s, color .15s;
  font-family:'JetBrains Mono',monospace;font-weight:700}
.icon-btn:hover{background:var(--surface-hover);border-color:var(--accent);color:var(--accent)}
.dir-toggle{font-family:'Vazirmatn',sans-serif;font-size:12.5px;font-weight:700}
@media(max-width:700px){ .nav-links a:not(.nav-chat){display:none} }

.container{max-width:1120px;margin:0 auto;padding:36px 22px 80px;position:relative;z-index:2}
.hero{padding:48px 0 38px;border-bottom:1px solid var(--border);
  margin-bottom:38px;position:relative}
.hero .tagline{display:inline-block;font-family:'JetBrains Mono',monospace;font-size:11.5px;
  color:var(--accent-2);letter-spacing:3px;text-transform:uppercase;margin-bottom:14px;
  padding:4px 11px;border-radius:20px;
  background:rgba(34,211,238,.08);border:1px solid rgba(34,211,238,.25);}
html[dir="rtl"] .hero .tagline{ letter-spacing:1px; }
.hero h1{font-family:'Vazirmatn','Space Grotesk',sans-serif;
  font-size:clamp(32px,5vw,54px);font-weight:700;
  letter-spacing:-1.2px;line-height:1.15;margin-bottom:16px;
  background:linear-gradient(135deg,var(--text) 20%,var(--accent) 70%);
  -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.hero p{color:var(--text-dim);font-size:16px;max-width:660px;line-height:1.7}
.hero .ghost-big{position:absolute;inset-inline-end:20px;top:30px;font-size:88px;
  opacity:.08;pointer-events:none;user-select:none;line-height:1}
@media(max-width:640px){.hero .ghost-big{font-size:56px;inset-inline-end:6px;top:18px}}

.toolbar{display:flex;flex-wrap:wrap;gap:14px;align-items:flex-start;
  justify-content:space-between;margin-bottom:28px}
.search-box{position:relative;flex:1;min-width:220px;max-width:420px}
.search-box input{width:100%;padding:11px 16px 11px 42px;background:var(--surface);
  border:1px solid var(--border);border-radius:12px;color:var(--text);
  font-size:14px;outline:none;
  transition:border-color .15s, box-shadow .15s;font-family:inherit}
html[dir="rtl"] .search-box input{ padding:11px 42px 11px 16px; }
.search-box input::placeholder{color:var(--text-faint)}
.search-box input:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.search-box svg{position:absolute;inset-inline-start:15px;top:50%;transform:translateY(-50%);
  width:16px;height:16px;color:var(--text-faint);pointer-events:none}
.tag-cloud{display:flex;flex-wrap:wrap;gap:7px;flex:1;justify-content:flex-end;
  max-height:120px;overflow-y:auto;padding:2px}
.tag-btn{padding:6px 13px;border-radius:20px;border:1px solid var(--border);
  background:var(--surface);color:var(--text-dim);font-size:12px;font-weight:600;
  cursor:pointer;display:inline-flex;align-items:center;gap:6px;
  font-family:inherit;white-space:nowrap;
  transition:border-color .15s, color .15s;}
.tag-btn:hover{border-color:var(--accent);color:var(--text)}
.tag-btn.active{background:linear-gradient(135deg,var(--accent),var(--accent-2));
  border-color:transparent;color:#fff}
.tag-btn .count{font-family:'JetBrains Mono',monospace;font-size:10px;opacity:.75;
  padding:1px 6px;border-radius:9px;background:rgba(255,255,255,.08)}

.grid{display:grid;gap:22px}
@media(min-width:720px){.grid{grid-template-columns:repeat(2,1fr)}}
.card{background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);overflow:hidden;display:flex;flex-direction:column;
  transition:transform .2s, border-color .2s;
  position:relative;content-visibility:auto;contain-intrinsic-size:auto 440px}
.card:hover{transform:translateY(-3px);border-color:var(--accent);}
.card-cover{position:relative;aspect-ratio:16/10;overflow:hidden;
  display:flex;align-items:center;justify-content:center;
  background:linear-gradient(135deg,var(--surface-hover),var(--bg-2));
  padding:16px;}
.card-cover::before{content:"";position:absolute;inset:0;
  background:var(--cover, none);background-size:cover;background-position:center;
  opacity:.32;z-index:0;}
.card-cover img{position:relative;z-index:1;max-width:100%;max-height:100%;
  width:auto;height:auto;object-fit:contain;border-radius:8px;
  box-shadow:0 8px 22px rgba(0,0,0,.42);}
.card-cover .cover-placeholder{position:relative;z-index:1;width:100%;height:100%;
  display:flex;align-items:center;justify-content:center;
  font-size:64px;opacity:.4;border-radius:8px;}
.card-featured{position:absolute;top:14px;inset-inline-start:14px;z-index:3;
  background:linear-gradient(135deg,var(--gold),#f59e0b);color:#000;
  font-size:10px;font-weight:700;padding:4px 11px;border-radius:20px;
  letter-spacing:1px;text-transform:uppercase}
html[dir="rtl"] .card-featured{ letter-spacing:0; }
.card-body{padding:20px 22px 22px;flex:1;display:flex;flex-direction:column}
.card-meta{display:flex;align-items:center;gap:10px;font-size:11.5px;
  color:var(--text-faint);font-family:'JetBrains Mono',monospace;margin-bottom:12px}
.card-meta .dot{width:3px;height:3px;border-radius:50%;background:var(--text-faint)}
.card-title{font-family:'Vazirmatn','Space Grotesk',sans-serif;
  font-size:20px;font-weight:700;line-height:1.35;letter-spacing:-.3px;margin-bottom:10px}
.card-title a{color:var(--text);transition:color .15s}
.card-title a:hover{color:var(--accent)}
.card-excerpt{color:var(--text-dim);font-size:14px;line-height:1.7;flex:1;margin-bottom:16px}
.card-footer{display:flex;align-items:center;gap:11px;padding-top:14px;
  border-top:1px solid var(--border);font-size:12.5px}
.avatar{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;
  justify-content:center;font-size:13px;font-weight:700;color:#fff;flex-shrink:0;
  box-shadow:0 0 0 2px var(--surface), 0 0 0 3px var(--accent-soft)}
.card-author{color:var(--text-dim);font-weight:600;font-size:12.5px}
.card-rank{color:var(--text-faint);font-size:11px;margin-top:1px}
.card-tags{display:flex;gap:5px;margin-inline-start:auto;flex-wrap:wrap;justify-content:flex-end}
.card-tags span{font-size:10px;padding:3px 9px;border-radius:12px;
  background:var(--accent-soft);color:var(--accent);font-weight:600;
  font-family:'JetBrains Mono',monospace}

.post-header{max-width:780px;margin:0 auto 32px}
.post-tags-row{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px}
.post-tags-row span{font-size:11px;padding:4px 11px;border-radius:20px;
  background:var(--accent-soft);color:var(--accent);font-weight:600;
  font-family:'JetBrains Mono',monospace}
.post-header h1{font-family:'Vazirmatn','Space Grotesk',sans-serif;
  font-size:clamp(28px,4.5vw,44px);font-weight:700;letter-spacing:-.8px;
  line-height:1.25;margin:0 0 22px}
.post-meta{display:flex;align-items:center;flex-wrap:wrap;gap:16px;
  padding:18px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border)}
.post-meta .author-block{display:flex;align-items:center;gap:12px}
.post-meta .avatar{width:44px;height:44px;font-size:17px;
  box-shadow:0 0 0 3px var(--surface), 0 0 0 4px var(--accent-soft)}
.post-meta .author-name{font-weight:700;font-size:14.5px;color:var(--text)}
.post-meta .author-rank{font-size:12px;color:var(--text-dim);margin-top:1px}
.post-meta .meta-right{margin-inline-start:auto;text-align:end;
  font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--text-faint)}
.post-meta .meta-right .read{color:var(--accent-2);margin-top:3px}
.post-cover{position:relative;border-radius:var(--radius);overflow:hidden;
  margin-bottom:32px;border:1px solid var(--border);
  box-shadow:var(--shadow-md);background:var(--bg-2);
  display:flex;align-items:center;justify-content:center;}
.post-cover::before{content:"";position:absolute;inset:0;
  background:var(--cover, none);background-size:cover;background-position:center;
  opacity:.28;z-index:0;}
.post-cover img{position:relative;z-index:1;width:100%;height:auto;max-height:82vh;
  object-fit:contain;display:block}
.post-cover.no-img{height:200px;background:var(--grad, linear-gradient(135deg,#4c1d95,#0891b2))}
.post-cover.no-img::before{display:none}
.post-cover.no-img span{position:relative;z-index:1;font-size:72px;opacity:.3}

.prose{max-width:780px;margin:0 auto;font-size:16.5px;line-height:1.9;color:var(--text)}
.prose>*+*{margin-top:1.05em}
.prose p{margin-bottom:18px}
.prose h2{font-family:'Vazirmatn','Space Grotesk',sans-serif;
  font-size:26px;font-weight:700;letter-spacing:-.3px;
  margin:40px 0 14px;padding-bottom:10px;border-bottom:1px solid var(--border)}
.prose h3{font-size:19px;font-weight:700;margin:30px 0 10px}
.prose h4{font-size:17px;font-weight:700;margin:24px 0 8px}
.prose ul{list-style:none;margin:0 0 18px 0;padding:0}
.prose ul>li{padding-inline-start:22px;position:relative;margin-bottom:8px}
.prose ul>li::before{content:"◆";position:absolute;inset-inline-start:0;top:0;color:var(--accent);
  font-size:9px;line-height:2.6;opacity:.85;}
.prose ol{list-style:none;margin:0 0 18px 0;padding:0;counter-reset:ol-c}
.prose ol>li{padding-inline-start:36px;position:relative;margin-bottom:10px;counter-increment:ol-c}
.prose ol>li::before{content:counter(ol-c);position:absolute;inset-inline-start:0;top:2px;
  width:24px;height:24px;border-radius:50%;background:var(--accent-soft);
  color:var(--accent);border:1px solid var(--accent-soft);
  font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;
  font-family:'JetBrains Mono',monospace;}
.prose blockquote{border-inline-start:3px solid var(--accent);padding:14px 20px;margin:22px 0;
  background:var(--surface);border-radius:0 10px 10px 0;color:var(--text-dim);
  font-style:italic;}
html[dir="rtl"] .prose blockquote{ border-radius:10px 0 0 10px; }
.prose blockquote p:last-child{margin-bottom:0}
.prose code{font-family:'JetBrains Mono',monospace;background:var(--surface-2);
  padding:2px 7px;border-radius:6px;font-size:13.5px;color:var(--accent-2);
  border:1px solid var(--border);font-weight:600;
  direction:ltr;unicode-bidi:embed;}
.prose pre{background:var(--bg-2);border:1px solid var(--border);border-radius:12px;
  padding:16px 20px;overflow-x:auto;margin:22px 0;
  box-shadow:var(--shadow-sm);direction:ltr;text-align:left;}
.prose pre code{background:none;padding:0;color:var(--text);
  font-size:13.5px;line-height:1.65;border:none;font-weight:400;}
.prose img{border-radius:10px;margin:22px 0;border:1px solid var(--border);
  box-shadow:var(--shadow-sm);width:100%;height:auto}
.prose a{font-weight:600;text-decoration:underline;
  text-decoration-color:var(--accent-soft);text-decoration-thickness:1px;
  text-underline-offset:3px;transition:text-decoration-color .15s;}
.prose a:hover{text-decoration-color:var(--accent);}
.prose hr{border:none;margin:44px 0;height:1px;
  background:linear-gradient(90deg,transparent,var(--border-hi) 25%,var(--border-hi) 75%,transparent);}
.prose table{width:100%;border-collapse:collapse;margin:22px 0;font-size:14.5px;
  border:1px solid var(--border);border-radius:10px;overflow:hidden;}
.prose th, .prose td{padding:12px 16px;border-bottom:1px solid var(--border);text-align:start}
.prose th{background:var(--surface-2);font-weight:700;font-size:13px;
  text-transform:uppercase;letter-spacing:.5px;color:var(--text-dim);}
.prose tbody tr:nth-child(even){background:var(--surface-2)}
.prose tr:last-child td{border-bottom:none}

/* ★ Media (audio / video) — v2.7 */
.prose .media-wrap{
  margin:24px 0;padding:12px;
  background:var(--surface);border:1px solid var(--border);border-radius:14px;
  display:flex;flex-direction:column;gap:10px;
  box-shadow:var(--shadow-sm);
}
.prose .audio-player,
.prose .video-player{
  display:block;width:100%;
  background:transparent;border-radius:10px;
  outline:none;direction:ltr;
}
.prose .video-player{max-height:520px;object-fit:contain;background:#000;}
.prose .audio-player:focus-visible,
.prose .video-player:focus-visible{outline:2px solid var(--accent);outline-offset:2px;}
.prose .media-actions{
  display:flex;justify-content:flex-end;gap:8px;
}
.prose .dl-btn{
  display:inline-flex;align-items:center;gap:6px;
  padding:9px 18px;border-radius:10px;
  background:linear-gradient(135deg,var(--accent),var(--accent-2));
  color:#fff !important;font-weight:700;font-size:13px;
  text-decoration:none !important;
  transition:transform .15s, box-shadow .15s;
  box-shadow:0 3px 12px var(--accent-glow);
}
.prose .dl-btn:hover{transform:translateY(-1px);box-shadow:0 5px 16px var(--accent-glow);}
.prose .dl-btn:active{transform:translateY(0);}

.related{max-width:780px;margin:52px auto 0}
.related h3{font-family:'Vazirmatn','Space Grotesk',sans-serif;
  font-size:17px;font-weight:700;margin-bottom:16px;display:flex;align-items:center;gap:9px}
.related-list{display:grid;gap:14px}
@media(min-width:600px){.related-list{grid-template-columns:repeat(3,1fr)}}
.related-item{background:var(--surface);border:1px solid var(--border);
  border-radius:12px;padding:16px;display:flex;flex-direction:column;gap:8px;
  transition:border-color .15s, transform .15s;}
.related-item:hover{border-color:var(--accent);transform:translateY(-2px);}
.related-item a{color:var(--text);font-weight:700;font-size:14px;
  line-height:1.4;transition:color .15s}
.related-item a:hover{color:var(--accent)}
.related-item .date{font-size:11px;color:var(--text-faint);
  font-family:'JetBrains Mono',monospace;margin-top:auto}

.comments{max-width:780px;margin:60px auto 0;padding-top:32px;
  border-top:1px solid var(--border)}
.comments h3{font-family:'Vazirmatn','Space Grotesk',sans-serif;
  font-size:19px;font-weight:700;margin-bottom:22px;display:flex;align-items:center;gap:10px}
.comments h3 .count{background:var(--accent-soft);color:var(--accent);
  font-size:12px;padding:3px 11px;border-radius:14px;
  font-family:'JetBrains Mono',monospace;font-weight:700}
.comment-form{margin-bottom:28px;background:var(--surface);border:1px solid var(--border);
  border-radius:14px;padding:16px 18px;transition:border-color .15s}
.comment-form:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.comment-form .row{display:grid;grid-template-columns:1fr;gap:10px}
.comment-form input, .comment-form textarea{width:100%;padding:11px 14px;
  background:var(--bg-2);border:1px solid var(--border);border-radius:10px;
  color:var(--text);font-size:14px;font-family:inherit;outline:none;
  transition:border-color .15s}
.comment-form input:focus, .comment-form textarea:focus{border-color:var(--accent)}
.comment-form textarea{resize:vertical;min-height:84px;line-height:1.7}
.comment-form button{padding:10px 24px;border-radius:10px;border:none;
  background:linear-gradient(135deg,var(--accent),var(--accent-2));
  color:#fff;font-weight:700;font-size:13.5px;cursor:pointer;
  transition:transform .15s;font-family:inherit;}
.comment-form button:hover:not(:disabled){transform:translateY(-1px);}
.comment-form button:disabled{opacity:.5;cursor:not-allowed}
.comment-list{display:flex;flex-direction:column;gap:18px}
.comment-thread{content-visibility:auto;contain-intrinsic-size:auto 120px}
.comment{background:var(--surface);border:1px solid var(--border);
  border-radius:12px;padding:16px 18px;transition:border-color .15s}
.comment:hover{border-color:var(--border-hi)}
.comment.reply{background:var(--surface-2);
  border-inline-start:3px solid var(--accent);border-radius:0 10px 10px 0}
html[dir="rtl"] .comment.reply{ border-radius:10px 0 0 10px; }
.comment-head{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.comment-head .avatar{width:32px;height:32px;font-size:13px}
.comment-author{font-weight:700;font-size:13.5px;color:var(--text)}
.comment-time{font-size:11px;color:var(--text-faint);
  font-family:'JetBrains Mono',monospace;margin-inline-start:auto}
.comment-text{font-size:14.5px;color:var(--text);line-height:1.75;
  white-space:pre-wrap;word-break:break-word}
.comment-actions{margin-top:10px;display:flex;gap:8px}
.comment-actions button{background:none;border:none;color:var(--text-faint);
  font-size:12px;font-weight:600;cursor:pointer;padding:4px 10px;
  border-radius:8px;transition:color .15s, background .15s;font-family:inherit;}
.comment-actions button:hover{color:var(--accent);background:var(--accent-soft)}
.comment-replies{margin-top:14px;margin-inline-start:20px;padding-inline-start:16px;
  border-inline-start:2px solid var(--border);display:flex;flex-direction:column;gap:12px;}
.reply-form{margin-top:12px;margin-inline-start:20px;padding:14px 16px;
  background:var(--surface-2);border:1px solid var(--accent);
  border-radius:12px;display:flex;flex-direction:column;gap:9px;}
.reply-form input, .reply-form textarea{width:100%;padding:10px 13px;
  background:var(--bg-2);border:1px solid var(--border);border-radius:9px;
  color:var(--text);font-size:13.5px;font-family:inherit;outline:none;
  transition:border-color .15s}
.reply-form input:focus, .reply-form textarea:focus{border-color:var(--accent)}
.reply-form textarea{resize:vertical;min-height:62px;line-height:1.6}
.reply-form-actions{display:flex;gap:8px;justify-content:flex-end}
.reply-form-actions button{padding:8px 18px;border-radius:9px;
  font-size:12.5px;font-weight:700;cursor:pointer;font-family:inherit;
  border:1px solid var(--border);background:var(--surface);color:var(--text-dim);
  transition:background .15s, color .15s}
.reply-form-actions button:hover{background:var(--surface-hover);color:var(--text)}
.reply-form-actions .btn-submit{background:linear-gradient(135deg,var(--accent),var(--accent-2));
  border-color:transparent;color:#fff}
.no-comments{color:var(--text-faint);font-size:13.5px;text-align:center;
  padding:28px;background:var(--surface);border:1px dashed var(--border);border-radius:14px}
.empty{text-align:center;padding:80px 20px;color:var(--text-faint)}
.empty .icon{font-size:52px;margin-bottom:16px}
.empty p{font-size:15px}
.back-link{display:inline-flex;align-items:center;gap:7px;font-size:13.5px;
  font-weight:600;color:var(--text-dim);margin-bottom:24px;
  padding:8px 14px;border-radius:10px;transition:color .15s, background .15s}
.back-link:hover{color:var(--accent);background:var(--accent-soft)}
.back-link .arrow{display:inline-block;transition:transform .15s}
html[dir="rtl"] .back-link .arrow{ transform:scaleX(-1); }
.back-link:hover .arrow{ transform:translateX(-3px); }
html[dir="rtl"] .back-link:hover .arrow{ transform:scaleX(-1) translateX(-3px); }
footer{border-top:1px solid var(--border);padding:32px 22px;text-align:center;
  color:var(--text-faint);font-size:12.5px;
  font-family:'JetBrains Mono',monospace;line-height:1.9}
footer .ghost{color:var(--accent);font-size:14px}
footer .sub{opacity:.55;font-size:11px;margin-top:4px}
.chat-cta{display:inline-flex;align-items:center;gap:9px;padding:12px 24px;
  border-radius:12px;background:linear-gradient(135deg,var(--accent),var(--accent-2));
  color:#fff !important;font-weight:700;font-size:14px;
  margin-top:20px;box-shadow:0 4px 14px var(--accent-glow);
  transition:transform .15s;}
.chat-cta:hover{transform:translateY(-2px)}
::-webkit-scrollbar{width:9px;height:9px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border-hi);border-radius:5px}
::-webkit-scrollbar-thumb:hover{background:var(--accent)}
@media(max-width:640px){
  .container{padding:24px 16px 60px}
  .hero{padding:28px 0 26px;margin-bottom:28px}
  .nav-inner{padding:11px 16px;gap:12px}
  .nav-logo span:last-child{display:none}
  .nav-chat{padding:8px 14px;font-size:12.5px}
  .card-body{padding:16px 18px 18px}
  .card-title{font-size:18px}
  .toolbar{flex-direction:column;align-items:stretch}
  .tag-cloud{justify-content:flex-start;max-height:none}
  .comment-replies{margin-inline-start:10px;padding-inline-start:12px}
  .reply-form{margin-inline-start:10px}
  .card-cover{padding:10px}
}
@media (prefers-reduced-motion: reduce){
  *, *::before, *::after{
    animation-duration:.01ms !important;
    transition-duration:.01ms !important;
    scroll-behavior:auto !important;
  }
}

/* ★ BIDI FIX — invisible RTL/LTR isolation */
.prose p, .prose li, .prose h1, .prose h2, .prose h3, .prose h4,
.prose h5, .prose h6, .prose td, .prose th, .prose blockquote,
.prose dd, .prose dt,
.card-title, .card-excerpt, .card-author, .card-rank, .card-meta,
.comment-text, .comment-author, .comment-time,
.hero p, .post-header h1, .post-meta .author-name, .post-meta .author-rank,
.post-meta .meta-right, .related-item a,
.back-link, .tagline, .empty p, .no-comments,
footer, nav .nav-logo {
  unicode-bidi: plaintext;
}
bdi, .bidi-ltr {
  unicode-bidi: isolate;
  background: transparent !important;
  border: 0 !important;
  padding: 0 !important;
  margin: 0 !important;
  display: inline;
  font: inherit;
  color: inherit;
}
bdi[dir="ltr"], .bidi-ltr {
  direction: ltr;
  unicode-bidi: isolate;
}
</style>
</head>
<body>

<nav>
  <div class="nav-inner">
    <a href="/" class="nav-logo">{{ logo_html }}<span>{{ site_name }}</span></a>
    <div class="nav-spacer"></div>
    {{ chat_link }}
    <div class="nav-links">
      <a href="/" class="{{ active_home }}">{{ t_blog }}</a>
      <a href="/about" class="{{ active_about }}">{{ t_about }}</a>
      <button class="icon-btn dir-toggle" id="dirBtn" title="{{ t_dir_toggle }}" aria-label="{{ t_dir_toggle }}">RTL</button>
      <button class="icon-btn" id="themeBtn" title="{{ t_theme_toggle }}" aria-label="{{ t_theme_toggle }}">◐</button>
    </div>
  </div>
</nav>

<div class="container">
{{ content|safe }}
</div>

<footer>
  <div><span class="ghost">👻</span> {{ site_name }} — {{ site_tagline }}</div>
  <div class="sub">{{ t_footer }}</div>
</footer>

<script>
(function(){
  var root = document.documentElement;
  var savedTheme = localStorage.getItem('ghost_theme');
  if (!savedTheme) {
    savedTheme = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }
  root.setAttribute('data-theme', savedTheme);
  var tb = document.getElementById('themeBtn');
  if (tb) tb.textContent = savedTheme === 'dark' ? '◐' : '☀';

  var serverDir = root.getAttribute('dir') || 'ltr';
  var params = new URLSearchParams(window.location.search);
  var urlDir = params.get('dir');
  var savedDir = localStorage.getItem('ghost_dir');
  var finalDir = (urlDir === 'rtl' || urlDir === 'ltr') ? urlDir
               : (savedDir === 'rtl' || savedDir === 'ltr') ? savedDir
               : serverDir;
  root.setAttribute('dir', finalDir);
  if (savedDir !== finalDir) localStorage.setItem('ghost_dir', finalDir);
})();
document.getElementById('themeBtn').addEventListener('click', function(){
  var cur = document.documentElement.getAttribute('data-theme');
  var next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('ghost_theme', next);
  this.textContent = next === 'dark' ? '◐' : '☀';
});
(function(){
  var db = document.getElementById('dirBtn');
  function updateDirBtn(){
    var cur = document.documentElement.getAttribute('dir');
    if (db) db.textContent = cur === 'rtl' ? 'RTL' : 'LTR';
  }
  updateDirBtn();
  if (db) {
    db.addEventListener('click', function(){
      var cur = document.documentElement.getAttribute('dir');
      var next = cur === 'rtl' ? 'ltr' : 'rtl';
      document.documentElement.setAttribute('dir', next);
      localStorage.setItem('ghost_dir', next);
      updateDirBtn();
    });
  }
})();

/* ★ Bidi isolator — wraps Latin runs in <bdi dir="ltr"> */
(function(){
  'use strict';
  var LATIN = 'A-Za-z\\u00C0-\\u024F\\u1E00-\\u1EFF';
  var LATIN_TEST = /[A-Za-z\u00C0-\u024F\u1E00-\u1EFF]/;
  var INNER = '0-9\\s.,!?;:\'"()\\[\\]{}\\-_@#$%^&*+=/\\\\|<>~`';
  var LTR_RUN = new RegExp(
    '[' + LATIN + '](?:[' + LATIN + INNER + ']*[' + LATIN + '0-9])?', 'g'
  );
  function isInsideSkipped(el){
    var cur = el;
    while (cur) {
      var tag = cur.tagName;
      if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'CODE' ||
          tag === 'PRE' || tag === 'BDI' || tag === 'TEXTAREA' ||
          tag === 'INPUT' || tag === 'SELECT' || tag === 'OPTION' ||
          tag === 'AUDIO' || tag === 'VIDEO') return true;
      cur = cur.parentElement;
    }
    return false;
  }
  function wrapTextNode(node){
    var text = node.nodeValue;
    if (!text || !LATIN_TEST.test(text)) return;
    LTR_RUN.lastIndex = 0;
    var parts = [], lastIdx = 0, m, changed = false;
    while ((m = LTR_RUN.exec(text)) !== null) {
      var raw = m[0];
      var trimmed = raw.replace(/[\s]+$/, '');
      var trailing = raw.slice(trimmed.length);
      if (!trimmed || !LATIN_TEST.test(trimmed)) continue;
      if (m.index > lastIdx) {
        parts.push(document.createTextNode(text.slice(lastIdx, m.index)));
      }
      var bdi = document.createElement('bdi');
      bdi.setAttribute('dir', 'ltr');
      bdi.className = 'bidi-ltr';
      bdi.textContent = trimmed;
      parts.push(bdi);
      if (trailing) parts.push(document.createTextNode(trailing));
      lastIdx = m.index + raw.length;
      changed = true;
    }
    if (!changed) return;
    if (lastIdx < text.length) {
      parts.push(document.createTextNode(text.slice(lastIdx)));
    }
    var frag = document.createDocumentFragment();
    for (var i = 0; i < parts.length; i++) frag.appendChild(parts[i]);
    try { node.parentNode.replaceChild(frag, node); } catch (e) {}
  }
  function isolateBidi(root){
    if (!root || !root.nodeType) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
    var nodes = [], n;
    while ((n = walker.nextNode())) {
      if (!isInsideSkipped(n.parentElement)) nodes.push(n);
    }
    for (var i = 0; i < nodes.length; i++) {
      try { wrapTextNode(nodes[i]); } catch (e) {}
    }
  }
  window.GhostBidi = { isolate: isolateBidi };
  function ready(fn){
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }
  ready(function(){ isolateBidi(document.body); });
})();

var searchInput = document.getElementById('searchInput');
if (searchInput) {
  var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
  var tagBtns = Array.prototype.slice.call(document.querySelectorAll('.tag-btn'));
  var activeTag = 'all';
  var rafId = null;
  function applyFilter(){
    rafId = null;
    var q = searchInput.value.trim().toLowerCase();
    var shown = 0;
    for (var i = 0; i < cards.length; i++) {
      var c = cards[i];
      var text = (c.dataset.title + ' ' + c.dataset.excerpt + ' ' + c.dataset.tags).toLowerCase();
      var matchQ = !q || text.indexOf(q) !== -1;
      var matchT = activeTag === 'all' ||
        (',' + c.dataset.tags + ',').indexOf(',' + activeTag + ',') !== -1;
      var vis = matchQ && matchT;
      if (vis) { if (c.style.display === 'none') c.style.display = ''; shown++; }
      else { if (c.style.display !== 'none') c.style.display = 'none'; }
    }
    var empty = document.getElementById('searchEmpty');
    if (empty) empty.style.display = shown ? 'none' : 'block';
  }
  function scheduleFilter(){
    if (rafId === null) rafId = requestAnimationFrame(applyFilter);
  }
  searchInput.addEventListener('input', scheduleFilter);
  tagBtns.forEach(function(b){
    b.addEventListener('click', function(){
      tagBtns.forEach(function(x){ x.classList.remove('active'); });
      b.classList.add('active');
      activeTag = b.dataset.tag;
      scheduleFilter();
    });
  });
}
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
function submitComment(slug, author, text, parentId, formEl, done){
  var btn = formEl.querySelector('button[type="submit"]');
  if (btn) { btn.disabled = true; btn.dataset.orig = btn.textContent; btn.textContent = '…'; }
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
    if (btn) { btn.disabled = false; btn.textContent = btn.dataset.orig || '✓'; }
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
      var wrapper = parent.parentElement;
      var replies = wrapper.querySelector(':scope > .comment-replies');
      if (!replies) {
        replies = document.createElement('div');
        replies.className = 'comment-replies';
        wrapper.appendChild(replies);
      }
      replies.appendChild(div);
    } else { list.appendChild(div); }
  } else {
    var wrap = document.createElement('div');
    wrap.className = 'comment-thread';
    wrap.appendChild(div);
    list.appendChild(wrap);
  }
  if (window.GhostBidi) { try { window.GhostBidi.isolate(div); } catch (e) {} }
  div.scrollIntoView({ behavior:'smooth', block:'center' });
  var cnt = document.getElementById('commentCount');
  if (cnt) cnt.textContent = parseInt(cnt.textContent, 10) + 1;
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
  form.querySelector('input').focus();
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
</script>
</body>
</html>
"""

# ============================================================
#                  ADMIN TEMPLATES
# ============================================================
ADMIN_LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="en" dir="ltr" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>👻 Ghost Union · Admin</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:'Inter',system-ui,sans-serif;
  min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:#05070c;color:#e9eef8;padding:20px;
  background-image:radial-gradient(ellipse 800px 600px at 20% 10%, rgba(167,139,250,.18) 0%, transparent 55%);
}
.box{
  background:#0f1420;border:1px solid #1b2333;border-radius:16px;
  padding:36px 32px;max-width:420px;width:100%;
  box-shadow:0 16px 40px rgba(0,0,0,.5);position:relative;overflow:hidden;
}
.box::before{
  content:"";position:absolute;top:0;left:0;right:0;height:2px;
  background:linear-gradient(90deg,#a78bfa,#22d3ee);
}
.icon{font-size:48px;text-align:center;margin-bottom:14px;
  filter:drop-shadow(0 0 12px rgba(167,139,250,.6))}
h1{font-size:22px;font-weight:700;text-align:center;margin-bottom:6px;letter-spacing:-.4px}
.sub{font-size:12.5px;color:#5b6884;text-align:center;margin-bottom:26px;
  font-family:'JetBrains Mono',monospace}
label{display:block;font-size:12px;font-weight:600;color:#96a3ba;
  margin-bottom:8px;letter-spacing:.3px}
input{
  width:100%;padding:12px 15px;background:#0a0d16;border:1px solid #1b2333;
  border-radius:10px;color:#e9eef8;font-size:14px;font-family:inherit;
  outline:none;transition:border-color .15s;
}
input:focus{border-color:#a78bfa;box-shadow:0 0 0 3px rgba(167,139,250,.13)}
button{
  width:100%;padding:12px;margin-top:16px;border:none;border-radius:10px;
  background:linear-gradient(135deg,#a78bfa,#22d3ee);
  color:#fff;font-weight:700;font-size:14px;cursor:pointer;
  font-family:inherit;transition:transform .15s;
}
button:hover{transform:translateY(-1px)}
.error{
  background:rgba(244,63,94,.12);color:#f43f5e;padding:11px 14px;
  border-radius:10px;font-size:13px;margin-bottom:16px;text-align:center;
  border:1px solid rgba(244,63,94,.3);font-weight:600;
}
.back{display:block;text-align:center;margin-top:16px;color:#5b6884;
  font-size:12px;text-decoration:none}
.back:hover{color:#a78bfa}
</style>
</head>
<body>
<form class="box" method="post" action="/admin/login">
  <div class="icon">👻</div>
  <h1>Ghost Admin</h1>
  <div class="sub">RESTRICTED AREA</div>
  __ERROR__
  <label for="password">Password</label>
  <input type="password" id="password" name="password" required autofocus>
  <button type="submit">Unlock</button>
  <a class="back" href="/">← back to blog</a>
</form>
</body>
</html>
"""

ADMIN_DASH_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:'Vazirmatn','Inter',system-ui,sans-serif;
  background:#05070c;color:#e9eef8;min-height:100vh;
  background-image:radial-gradient(ellipse 900px 700px at 10% 5%, rgba(167,139,250,.14) 0%, transparent 55%);
  padding:28px 20px 60px;
}
.wrap{max-width:1240px;margin:0 auto}
.head{
  display:flex;align-items:center;gap:14px;margin-bottom:28px;
  padding-bottom:20px;border-bottom:1px solid #1b2333;
}
.head .icon{font-size:34px}
.head h1{font-size:22px;font-weight:700;letter-spacing:-.4px;flex:1}
.head .badge{
  font-family:'JetBrains Mono',monospace;font-size:10.5px;
  padding:5px 12px;border-radius:20px;
  background:rgba(34,197,94,.12);color:#22c55e;font-weight:700;
  border:1px solid rgba(34,197,94,.3);
}
.head a{
  padding:9px 16px;border-radius:10px;font-size:12.5px;font-weight:700;
  text-decoration:none;color:#96a3ba;border:1px solid #1b2333;
  background:#0f1420;transition:all .15s;
}
.head a:hover{color:#f43f5e;border-color:#f43f5e}
.stats{
  display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
  gap:14px;margin-bottom:30px;
}
.stat{
  background:#0f1420;border:1px solid #1b2333;border-radius:12px;
  padding:20px;position:relative;overflow:hidden;
}
.stat::before{
  content:"";position:absolute;inset-inline-start:0;top:0;bottom:0;width:3px;
  background:linear-gradient(180deg,#a78bfa,#22d3ee);
}
.stat .label{font-size:11px;color:#5b6884;font-weight:600;
  text-transform:uppercase;letter-spacing:1px;margin-bottom:8px}
.stat .val{font-size:30px;font-weight:700;letter-spacing:-1px;
  font-family:'JetBrains Mono',monospace;
  background:linear-gradient(135deg,#e9eef8,#a78bfa);
  -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.stat .hint{font-size:11px;color:#5b6884;margin-top:4px;
  font-family:'JetBrains Mono',monospace}
h2{font-size:15px;font-weight:700;margin:32px 0 14px;
  display:flex;align-items:center;gap:9px;letter-spacing:-.2px}
h2 .count{font-size:11px;padding:3px 10px;border-radius:12px;
  background:rgba(167,139,250,.13);color:#a78bfa;
  font-family:'JetBrains Mono',monospace;font-weight:700}
table{
  width:100%;border-collapse:collapse;font-size:13px;
  background:#0f1420;border:1px solid #1b2333;border-radius:12px;
  overflow:hidden;
}
th,td{padding:12px 16px;text-align:start;border-bottom:1px solid #1b2333}
th{background:#0a0d16;font-weight:700;font-size:11px;
  text-transform:uppercase;letter-spacing:.6px;color:#96a3ba}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover{background:#141a28}
td.mono{font-family:'JetBrains Mono',monospace;font-size:12.5px;color:#22d3ee;
  direction:ltr;unicode-bidi:embed}
td.name{color:#fbbf24;font-weight:700}
td.snippet{color:#96a3ba;max-width:340px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:12.5px}
td .pill{
  display:inline-block;padding:3px 10px;border-radius:12px;
  background:rgba(167,139,250,.13);color:#a78bfa;
  font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;
}
td .yes{color:#22c55e}
td .no{color:#5b6884}
.ua{font-size:11px;color:#5b6884;font-family:'JetBrains Mono',monospace;
  max-width:340px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  direction:ltr;unicode-bidi:embed}
@media(max-width:700px){
  table{font-size:12px}
  th,td{padding:9px 10px}
  td.snippet,td .ua{max-width:160px}
}
"""


def build_admin_dashboard() -> str:
    total_views = sum(_views.values())
    total_ips = len(_ips)
    total_comments = sum(len(v) for v in _comments.values())
    commenters = sum(1 for e in _ips.values() if e.get("name"))

    post_rows = []
    for slug, cnt in sorted(_views.items(), key=lambda kv: -kv[1]):
        post_rows.append(
            f"<tr><td class='mono'>/post/{html.escape(slug)}</td>"
            f"<td><span class='pill'>{cnt}</span></td></tr>"
        )
    posts_table = (
        "<table><thead><tr><th>Post</th><th>Views</th></tr></thead><tbody>"
        + ("".join(post_rows) if post_rows else
           "<tr><td colspan='2' style='text-align:center;color:#5b6884'>No views recorded yet.</td></tr>")
        + "</tbody></table>"
    )

    ip_rows = []
    for ip, e in sorted(_ips.items(), key=lambda kv: -kv[1].get("last_seen", 0)):
        name = e.get("name") or "—"
        name_cls = "name" if e.get("name") else ""
        comments = e.get("comments") or []
        if comments:
            snippet = html.escape(comments[-1])
            comment_marker = f"<span class='yes'>✓ {len(comments)}</span>"
        else:
            snippet = "—"
            comment_marker = "<span class='no'>✗</span>"
        last_seen = datetime.fromtimestamp(
            e.get("last_seen", 0), tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")
        ua = html.escape((e.get("user_agent") or "")[:120])
        ip_rows.append(
            f"<tr>"
            f"<td class='mono'>{html.escape(ip)}</td>"
            f"<td class='{name_cls}'>{html.escape(name)}</td>"
            f"<td><span class='pill'>{e.get('views', 0)}</span></td>"
            f"<td>{comment_marker}</td>"
            f"<td class='snippet' title='{snippet}'>{snippet}</td>"
            f"<td class='mono' style='font-size:11.5px'>{last_seen}</td>"
            f"<td class='ua' title='{ua}'>{ua}</td>"
            f"</tr>"
        )
    ips_table = (
        "<table><thead><tr>"
        "<th>IP Address</th><th>Name</th><th>Visits</th>"
        "<th>Commented</th><th>Last comment</th><th>Last seen</th><th>User-Agent</th>"
        "</tr></thead><tbody>"
        + ("".join(ip_rows) if ip_rows else
           "<tr><td colspan='7' style='text-align:center;color:#5b6884'>No visitors recorded yet.</td></tr>")
        + "</tbody></table>"
    )

    return f"""<!DOCTYPE html>
<html lang="en" dir="ltr" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>👻 Ghost Union · Admin Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;700&family=JetBrains+Mono:wght@400&display=swap" rel="stylesheet">
<style>{ADMIN_DASH_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <span class="icon">👻</span>
    <h1>Ghost Admin Dashboard</h1>
    <span class="badge">● LIVE</span>
    <a href="/admin/logout">Logout</a>
  </div>
  <div class="stats">
    <div class="stat">
      <div class="label">Total Views</div>
      <div class="val">{total_views}</div>
      <div class="hint">all posts combined</div>
    </div>
    <div class="stat">
      <div class="label">Unique IPs</div>
      <div class="val">{total_ips}</div>
      <div class="hint">distinct visitors</div>
    </div>
    <div class="stat">
      <div class="label">Commenters</div>
      <div class="val">{commenters}</div>
      <div class="hint">named identities</div>
    </div>
    <div class="stat">
      <div class="label">Comments</div>
      <div class="val">{total_comments}</div>
      <div class="hint">total posted</div>
    </div>
  </div>
  <h2>👁 Views per post <span class="count">{len(_views)}</span></h2>
  {posts_table}
  <h2>🌐 Visitor IP log <span class="count">{total_ips}</span></h2>
  {ips_table}
</div>
</body>
</html>"""


# ============================================================
#                  TEMPLATE RENDERING
# ============================================================
I18N = {
    "en": {
        "blog": "Blog", "about": "About",
        "theme_toggle": "Toggle theme",
        "dir_toggle": "Toggle direction (LTR/RTL)",
        "footer": "Powered by FastAPI · Content via remote JSON",
    },
    "fa": {
        "blog": "وبلاگ", "about": "درباره",
        "theme_toggle": "تغییر پوسته",
        "dir_toggle": "تغییر جهت نوشتار",
        "footer": "قدرت‌گرفته از FastAPI · محتوا از JSON راه دور",
    },
}


def _i18n(lang: str) -> dict:
    lang = (lang or "en").lower().split("-")[0]
    return I18N.get(lang, I18N["en"])


def render_page(content: str, site: dict, active: str = "home",
                page_title: str = "", dir_override: Optional[str] = None) -> str:
    name = site.get("name", "Ghost Union")
    title = f"{page_title} · {name}" if page_title else name
    tagline = site.get("tagline", "Silent. Precise. Unified.")
    description = site.get("description", "")
    lang = (site.get("lang") or "en").strip() or "en"
    direction = detect_dir(site, dir_override)
    t = _i18n(lang)

    logo_html = (
        f'<img src="{html.escape(site["logo"])}" alt="">'
        if site.get("logo")
        else '<span class="ghost-icon">👻</span>'
    )
    chat_link = (
        f'<a class="nav-chat" href="{html.escape(site["chat_url"])}" target="_blank" rel="noopener">💬 Chat ↗</a>'
        if site.get("chat_url") else ""
    )

    out = HTML
    out = out.replace("{{ lang }}", html.escape(lang, quote=True))
    out = out.replace("{{ dir }}", direction)
    out = out.replace("{{ site_description }}", html.escape(description, quote=True))
    out = out.replace("{{ page_title }}", html.escape(title))
    out = out.replace("{{ logo_html }}", logo_html)
    out = out.replace("{{ site_name }}", html.escape(name))
    out = out.replace("{{ site_tagline }}", html.escape(tagline))
    out = out.replace("{{ chat_link }}", chat_link)
    out = out.replace("{{ active_home }}", "active" if active == "home" else "")
    out = out.replace("{{ active_about }}", "active" if active == "about" else "")
    out = out.replace("{{ t_blog }}", html.escape(t["blog"]))
    out = out.replace("{{ t_about }}", html.escape(t["about"]))
    out = out.replace("{{ t_theme_toggle }}", html.escape(t["theme_toggle"]))
    out = out.replace("{{ t_dir_toggle }}", html.escape(t["dir_toggle"]))
    out = out.replace("{{ t_footer }}", html.escape(t["footer"]))
    out = out.replace("{{ content|safe }}", content)
    return out


# ============================================================
#                  HTML BUILDERS
# ============================================================
def build_home(data: dict, dir_override: Optional[str] = None) -> str:
    site = get_site(data)
    posts = get_posts(data)
    tags = tag_counts(posts)

    parts = [f"""
    <div class="hero">
      <span class="ghost-big" aria-hidden="true">👻</span>
      <div class="tagline">{html.escape(site.get('tagline', 'Silent. Precise. Unified.'))}</div>
      <h1>{html.escape(site.get('name', 'Ghost Union'))}</h1>
      <p>{html.escape(site.get('description', 'Field notes from the operators of the Ghost Union.'))}</p>
      {f'<a class="chat-cta" href="{html.escape(site["chat_url"])}" target="_blank" rel="noopener">💬 Chat Room ↗</a>' if site.get('chat_url') else ''}
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
        </div>""")
    else:
        parts.append('<div class="grid">')
        for p in posts:
            cover = p.get("cover", "")
            slug = p.get("slug", "")
            cover_style = ""
            if cover:
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
            pviews = _views.get(slug, 0)
            parts.append(f"""
            <article class="card" data-title="{title}" data-excerpt="{ex}" data-tags="{tags_data}">
              <div class="card-cover"{cover_style}>{cover_html}{featured}</div>
              <div class="card-body">
                <div class="card-meta"><span>{date}</span><span class="dot"></span><span>{rt} min</span><span class="dot"></span><span>👁 {pviews}</span></div>
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

    return render_page("\n".join(parts), site, active="home", dir_override=dir_override)


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


def build_post(data: dict, post: dict, dir_override: Optional[str] = None) -> str:
    site = get_site(data)
    author = post.get("author", "Unknown")
    color = author_avatar(author)
    initial = author_initial(author)
    tags_html = "".join(f'<span>{html.escape(t)}</span>' for t in post.get("tags", []))
    cover = post.get("cover", "")
    slug = post.get("slug", "")

    if cover:
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
    pviews = _views.get(slug, 0)

    parts = [f"""
    <a class="back-link" href="/"><span class="arrow">←</span> Back to all posts</a>
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
            <div class="read">◷ {rt} min · 👁 {pviews}</div>
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

    return render_page("\n".join(parts), site, active="home",
                       page_title=post.get("title", "Post"), dir_override=dir_override)


def build_about(data: dict, dir_override: Optional[str] = None) -> str:
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
      <p><strong>Trust.</strong> Earned through action, never demanded.</p>
      <h2>The Field Notes</h2>
      <p>This blog is where operators of the Ghost Union share protocols, operations, and lessons from the field. No fluff. No noise. Only what matters.</p>
      <blockquote>Stay silent. Stay sharp. Stay unified.</blockquote>
      {chat_btn}
    </div>
    """]
    return render_page("\n".join(parts), site, active="about",
                       page_title="About", dir_override=dir_override)


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


def get_dir_override(request: Request) -> Optional[str]:
    d = (request.query_params.get("dir") or "").lower().strip()
    return d if d in ("rtl", "ltr") else None


# ============================================================
#                  VISITOR TRACKING MIDDLEWARE
# ============================================================
@app.middleware("http")
async def visitor_tracker(request: Request, call_next):
    path = request.url.path
    if (request.method == "GET"
            and not path.startswith(("/admin", "/api", "/refresh", "/download"))
            and not path.startswith("/static")):
        try:
            ip = get_client_ip(request)
            ua = request.headers.get("user-agent", "")
            record_visit(ip, ua, path)
        except Exception:
            pass
    return await call_next(request)


# ============================================================
#                  DOWNLOAD PROXY (v2.7)
# ============================================================
_PRIVATE_PREFIXES = (
    "10.", "127.", "169.254.", "192.168.",
    "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.",
    "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.",
    "172.28.", "172.29.", "172.30.", "172.31.",
)


@app.get("/download")
async def download_proxy(url: str = "", name: str = ""):
    """Same-origin download proxy.

    Streams remote file to client with Content-Disposition: attachment.
    This bypasses:
      - CDN CORS restrictions (server-side fetch, no browser origin)
      - CDN hotlink protection (browser UA sent)
      - The <a download> cross-origin ignore rule
      - imgurl.ir's inline-only serving (we force attachment)
    """
    if not url:
        raise HTTPException(400, "Missing url")

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "Invalid URL scheme")

    host = (parsed.hostname or "").lower()
    if (not host or host in ("localhost", "0.0.0.0", "::1", "[::1]")
            or host.startswith(_PRIVATE_PREFIXES)):
        raise HTTPException(403, "Forbidden host")

    # Filename resolution + sanitization
    if not name:
        name = url.split("/")[-1].split("?")[0] or "download"
    safe_name = "".join(c for c in name if c.isalnum() or c in "._-")[:120] or "download"
    if "." not in safe_name:
        low = url.lower()
        for ext in (".ogg", ".oga", ".opus", ".mp3", ".wav", ".m4a",
                    ".aac", ".flac", ".weba", ".mp4", ".webm", ".mov",
                    ".jpg", ".jpeg", ".png", ".gif", ".webp"):
            if ext in low:
                safe_name += ext
                break
        else:
            safe_name += ".bin"

    fetch_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"{parsed.scheme}://{host}/",
    }

    def stream():
        try:
            req = urllib.request.Request(url, headers=fetch_headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        return
                    yield chunk
        except Exception as e:
            print(f"⚠️  Download stream error for {url}: {e}")

    return StreamingResponse(
        stream(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "Cache-Control": "public, max-age=3600",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
#                  ROUTES
# ============================================================
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    data = await fetch_content()
    return html_no_cache(build_home(data, get_dir_override(request)))


@app.get("/post/{slug}", response_class=HTMLResponse)
async def post_view(slug: str, request: Request):
    data = await fetch_content()
    post = find_post(data, slug)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    bump_view(slug)
    return html_no_cache(build_post(data, post, get_dir_override(request)))


@app.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    data = await fetch_content()
    return html_no_cache(build_about(data, get_dir_override(request)))


# ============================================================
#                  ADMIN PANEL ROUTES
# ============================================================
@app.get("/admin", response_class=HTMLResponse)
async def admin_root(request: Request):
    if admin_authed(request):
        return HTMLResponse(build_admin_dashboard(), headers=NO_CACHE_HEADERS)
    err_html = ""
    if request.query_params.get("error"):
        err_html = '<div class="error">✗ Wrong password. Try again.</div>'
    body = ADMIN_LOGIN_HTML.replace("__ERROR__", err_html)
    return HTMLResponse(body, headers=NO_CACHE_HEADERS)


@app.post("/admin/login")
async def admin_login(request: Request):
    form = await request.form()
    pw = (form.get("password") or "").strip()
    if pw == ADMIN_PASSWORD:
        token = secrets.token_urlsafe(32)
        _admin_sessions[token] = time.time() + ADMIN_SESSION_TTL
        resp = RedirectResponse("/admin", status_code=303)
        resp.set_cookie(
            "ghost_admin", token,
            max_age=ADMIN_SESSION_TTL,
            httponly=True, samesite="lax",
        )
        return resp
    return RedirectResponse("/admin?error=1", status_code=303)


@app.get("/admin/logout")
async def admin_logout(request: Request):
    token = request.cookies.get("ghost_admin")
    if token:
        _admin_sessions.pop(token, None)
    resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie("ghost_admin")
    return resp


# ============================================================
#                  JSON API ROUTES
# ============================================================
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
            {"ok": False, "error": "Not found"}, status_code=404,
            headers=NO_CACHE_HEADERS,
        )
    return JSONResponse({"ok": True, "post": post}, headers=NO_CACHE_HEADERS)


@app.post("/api/comment")
async def api_comment(payload: dict, request: Request):
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
            if not any(c["id"] == pid for c in get_comments(slug)):
                pid = None

    try:
        ip = get_client_ip(request)
        record_commenter(ip, author, text)
    except Exception:
        pass

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
        print("👻 Ghost Union Blog v2.7 — Light + Reliable Media Edition")
        print("🔐 Admin panel: /admin")
        print("⬇ Download proxy: /download?url=...&name=...")
    except Exception as e:
        print(f"⚠️  Could not fetch content on startup: {e}")


# ============================================================
#                  RUN
# ============================================================
if __name__ == "__main__":
    import uvicorn
    print("👻 Ghost Union Blog v2.7 — Light + Reliable Media Edition")
    print("→ Blog:  http://0.0.0.0:8000")
    print("→ Admin: http://0.0.0.0:8000/admin")
    uvicorn.run(app, host="0.0.0.0", port=8000)