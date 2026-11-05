"""Blog content, markdown, comments, analytics, visitor tracking."""
import asyncio
import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Request

CONTENT_URL = "https://raw.githubusercontent.com/null-flame/-/refs/heads/main/a.json"
CACHE_TTL = 50
RTL_LANGS = {"fa", "ar", "he", "ur", "ps", "sd", "yi", "dv"}

_cache: dict = {"data": None, "ts": 0, "etag": None}
_comments: dict = {}
_comment_id = 0
_views: dict = {}
_ips: dict = {}


# ============================================================
#   Content fetching
# ============================================================
def _blocking_fetch(etag: Optional[str], bust: bool = False):
    url = CONTENT_URL
    if bust:
        url = f"{CONTENT_URL}?v={int(time.time() * 1000)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "GhostUnion/4.0",
        "Accept": "application/json",
        "Cache-Control": "no-cache, no-store, must-revalidate",
    })
    if etag and not bust:
        req.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return ("ok", json.loads(resp.read().decode()), resp.headers.get("ETag"))
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return ("not_modified", None, None)
        raise


async def fetch_content(force: bool = False) -> dict:
    now = time.time()
    if not force and _cache["data"] and (now - _cache["ts"]) < CACHE_TTL:
        return _cache["data"]
    try:
        status, data, etag = await asyncio.to_thread(_blocking_fetch, _cache["etag"], force)
        if status == "ok":
            _cache["data"] = data
            _cache["etag"] = etag
        _cache["ts"] = now
        return _cache["data"]
    except Exception as e:
        if _cache["data"]:
            return _cache["data"]
        raise HTTPException(502, f"Content fetch failed: {e}")


# ============================================================
#   Data helpers
# ============================================================
def get_site(data): return data.get("site", {})
def get_posts(data): return sorted(data.get("posts", []), key=lambda p: p.get("date", ""), reverse=True)
def find_post(data, slug):
    for p in get_posts(data):
        if p.get("slug") == slug:
            return p
    return None


def excerpt(post, length=165):
    ex = post.get("excerpt") or ""
    if not ex:
        raw = re.sub(r"[#*`>\[\]()!_-]", "", post.get("content", ""))
        ex = raw[:length].strip()
        if len(raw) > length:
            ex += "…"
    return ex


def reading_time(post):
    text = re.sub(r"\s+", " ", post.get("content", "") or "")
    return max(1, round(len(text.split()) / 220))


def format_date(iso):
    try:
        dt = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y")
    except Exception:
        return iso or ""


def tag_counts(posts):
    counts = {}
    for p in posts:
        for t in p.get("tags", []):
            counts[t] = counts.get(t, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower())))


def related_posts(data, post, limit=3):
    tags = set(post.get("tags", []))
    if not tags: return []
    scored = []
    for p in get_posts(data):
        if p.get("slug") == post.get("slug"): continue
        common = len(tags & set(p.get("tags", [])))
        if common: scored.append((common, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:limit]]


def author_avatar(name):
    h = int(hashlib.md5((name or "?").encode()).hexdigest(), 16)
    return f"hsl({h % 360}, 65%, 48%)"


def author_initial(name):
    return (name or "?").strip()[:1].upper()


def cover_gradient(slug):
    h = int(hashlib.md5((slug or "?").encode()).hexdigest(), 16)
    return f"linear-gradient(135deg, hsl({h % 360},55%,22%), hsl({(h+70) % 360},60%,36%))"


def detect_dir(site, override=None):
    if override in ("rtl", "ltr"): return override
    explicit = (site.get("dir") or "").lower().strip()
    if explicit in ("rtl", "ltr"): return explicit
    lang = (site.get("lang") or "en").lower().split("-")[0].strip()
    return "rtl" if lang in RTL_LANGS else "ltr"


# ============================================================
#   Markdown renderer
# ============================================================
_BLOCK_START = re.compile(r"^(#{1,6}\s|```|>|\s*[-*+]\s|\s*\d+\.\s)")
_AUDIO_RE = re.compile(r"\.(ogg|oga|opus|mp3|wav|m4a|aac|flac|weba)(?:[?#].*)?$", re.IGNORECASE)
_VIDEO_RE = re.compile(r"\.(mp4|m4v|webm|ogv|mov)(?:[?#].*)?$", re.IGNORECASE)


def _safe_url(url):
    u = url.strip()
    if u.lower().startswith(("javascript:", "vbscript:", "data:text/html")):
        return "#"
    return html.escape(u, quote=True)


def _attr(s):
    return (s or "").replace('"', "&quot;").replace("'", "&#39;")


def _download_url(url):
    fn = url.split("/")[-1].split("?")[0] or "download"
    return f"/download?{urllib.parse.urlencode({'url': url, 'name': fn})}"


def _render_media_inline(alt, raw_url):
    urls = [u.strip() for u in raw_url.split("|") if u.strip()]
    if not urls: return ""
    first = urls[0]
    safe_first = _safe_url(first)
    alt_safe = _attr(alt)
    dl = html.escape(_download_url(first), quote=True)
    if _AUDIO_RE.search(first):
        sources = "".join(f'<source src="{_safe_url(u)}">' for u in urls)
        return (f'<div class="media-wrap">'
                f'<audio controls preload="metadata" class="audio-player" '
                f'aria-label="{alt_safe}">{sources}</audio>'
                f'<div class="media-actions"><a class="dl-btn" href="{dl}" '
                f'download rel="noopener">⬇ دانلود صدا</a></div></div>')
    if _VIDEO_RE.search(first):
        sources = "".join(f'<source src="{_safe_url(u)}">' for u in urls)
        return (f'<div class="media-wrap">'
                f'<video controls preload="metadata" class="video-player" '
                f'aria-label="{alt_safe}">{sources}</video>'
                f'<div class="media-actions"><a class="dl-btn" href="{dl}" '
                f'download rel="noopener">⬇ دانلود ویدیو</a></div></div>')
    return f'<img src="{safe_first}" alt="{alt_safe}" loading="lazy">'


def _render_inline(text):
    text = html.escape(text, quote=False)
    spans = []

    def _stash(m):
        spans.append(m.group(1))
        return f"\x00C{len(spans)-1}\x00"

    text = re.sub(r"`([^`]+)`", _stash, text)
    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)",
                  lambda m: _render_media_inline(m.group(1), m.group(2)), text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)",
                  lambda m: f'<a href="{_safe_url(m.group(2))}" target="_blank" rel="noopener">{m.group(1)}</a>',
                  text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", text)
    text = re.sub(r"(?<![\w_])_([^_\n]+)_(?![\w_])", r"<em>\1</em>", text)
    text = re.sub(r"~~([^~]+)~~", r"<del>\1</del>", text)
    for i, c in enumerate(spans):
        text = text.replace(f"\x00C{i}\x00", f"<code>{c}</code>")
    return text


def render_markdown(text):
    if not text: return ""
    lines = text.replace("\r\n", "\n").split("\n")
    out, i, n = [], 0, len(lines)

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
            la = f' class="language-{html.escape(lang, quote=True)}"' if lang else ""
            out.append(f"<pre><code{la}>{html.escape(code)}</code></pre>")
            continue

        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_render_inline(m.group(2))}</h{lvl}>")
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
                and re.match(r"^\s*\|?[\s\-:|]+\|?\s*$", lines[i+1])
                and "-" in lines[i+1]):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            th = "<tr>" + "".join(f"<th>{_render_inline(c)}</th>" for c in header) + "</tr>"
            tb = "".join("<tr>" + "".join(f"<td>{_render_inline(c)}</td>" for c in r) + "</tr>"
                         for r in rows)
            out.append(f"<table><thead>{th}</thead><tbody>{tb}</tbody></table>")
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

        if not line.strip():
            i += 1
            continue

        buf = []
        while (i < n and lines[i].strip() and not _BLOCK_START.match(lines[i])):
            if ("|" in lines[i] and i + 1 < n
                    and re.match(r"^\s*\|?[\s\-:|]+\|?\s*$", lines[i+1])
                    and "-" in lines[i+1]):
                break
            buf.append(lines[i]); i += 1
        if buf:
            rendered = _render_inline("\n".join(buf)).replace("\n", "<br>\n")
            out.append(f"<p>{rendered}</p>")

    result = "\n".join(out)
    result = re.sub(r"<\s*(script|style|iframe|object|embed|form)[^>]*>.*?<\s*/\s*\1\s*>",
                    "", result, flags=re.DOTALL | re.IGNORECASE)
    result = re.sub(r"<\s*(script|style|iframe|object|embed|form)[^>]*/?>",
                    "", result, flags=re.IGNORECASE)
    return result


# ============================================================
#   Comments
# ============================================================
def add_comment(slug, author, text, parent_id=None):
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


def get_comments(slug): return _comments.get(slug, [])
def count_all(comments): return len(comments)


def build_comment_tree(comments):
    by_id = {c["id"]: {**c, "replies": []} for c in comments}
    roots = []
    for c in comments:
        node = by_id[c["id"]]
        pid = c.get("parent_id")
        if pid and pid in by_id:
            by_id[pid]["replies"].append(node)
        else:
            roots.append(node)
    return roots


# ============================================================
#   Views + analytics
# ============================================================
def bump_view(slug): _views[slug] = _views.get(slug, 0) + 1
def get_views(slug): return _views.get(slug, 0)


def get_client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff: return xff.split(",")[0].strip()
    real = request.headers.get("x-real-ip") or request.headers.get("cf-connecting-ip")
    if real: return real.strip()
    return request.client.host if request.client else "unknown"


def record_visit(ip, ua, path):
    now = time.time()
    e = _ips.get(ip)
    if e is None:
        _ips[ip] = {"first_seen": now, "last_seen": now, "views": 1,
                    "name": None, "comments": [], "user_agent": ua[:220], "pages": [path]}
    else:
        e["last_seen"] = now
        e["views"] = e.get("views", 0) + 1
        e.setdefault("pages", []).append(path)
        if len(e["pages"]) > 60: e["pages"] = e["pages"][-60:]
        if not e.get("user_agent"): e["user_agent"] = ua[:220]


def record_commenter(ip, name, text):
    now = time.time()
    e = _ips.get(ip)
    if e is None:
        _ips[ip] = {"first_seen": now, "last_seen": now, "views": 0,
                    "name": name, "comments": [text[:300]], "user_agent": "", "pages": []}
    else:
        e["name"] = name
        e["last_seen"] = now
        e.setdefault("comments", []).append(text[:300])
        if len(e["comments"]) > 25: e["comments"] = e["comments"][-25:]


# ============================================================
#   Middleware + startup
# ============================================================
def setup_middleware(app):
    @app.middleware("http")
    async def visitor_tracker(request: Request, call_next):
        path = request.url.path
        if (request.method == "GET"
                and not path.startswith(("/api", "/static", "/assets", "/chat",
                                         "/ws", "/download", "/admin"))):
            try:
                record_visit(get_client_ip(request),
                             request.headers.get("user-agent", ""), path)
            except Exception:
                pass
        return await call_next(request)


async def on_startup():
    try:
        await fetch_content(force=True)
        print("📚 Blog content loaded")
    except Exception as e:
        print(f"⚠️  Blog content fetch failed: {e}")