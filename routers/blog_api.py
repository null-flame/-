"""Blog REST API — all data for the SPA."""
from fastapi import APIRouter, HTTPException, Request

from services import content_service as cs

router = APIRouter(prefix="/api/blog")


@router.get("/site")
async def site():
    data = await cs.fetch_content()
    s = cs.get_site(data)
    return {
        "name": s.get("name", "Ghost Union"),
        "tagline": s.get("tagline", "Silent. Precise. Unified."),
        "description": s.get("description", ""),
        "lang": s.get("lang", "en"),
        "dir": cs.detect_dir(s),
        "logo": s.get("logo"),
        "has_chat": True,  # always available at /chat
    }


@router.get("/posts")
async def list_posts():
    data = await cs.fetch_content()
    posts = cs.get_posts(data)
    tags = cs.tag_counts(posts)
    out = []
    for p in posts:
        slug = p.get("slug", "")
        cover = p.get("cover", "")
        author = p.get("author", "Unknown")
        out.append({
            "slug": slug,
            "title": p.get("title", ""),
            "excerpt": cs.excerpt(p),
            "date": p.get("date", ""),
            "date_formatted": cs.format_date(p.get("date", "")),
            "author": author,
            "author_initial": cs.author_initial(author),
            "author_color": cs.author_avatar(author),
            "rank": p.get("rank", ""),
            "team": p.get("team", ""),
            "cover": cover,
            "cover_gradient": cs.cover_gradient(slug) if not cover else None,
            "tags": p.get("tags", []),
            "featured": bool(p.get("featured")),
            "reading_time": cs.reading_time(p),
            "views": cs.get_views(slug),
        })
    return {"posts": out, "tags": tags}


@router.get("/posts/{slug}")
async def get_post(slug: str):
    data = await cs.fetch_content()
    post = cs.find_post(data, slug)
    if not post:
        raise HTTPException(404, "Post not found")
    cs.bump_view(slug)
    author = post.get("author", "Unknown")
    related = cs.related_posts(data, post)
    return {
        "slug": post.get("slug"),
        "title": post.get("title", ""),
        "content_html": cs.render_markdown(post.get("content", "")),
        "excerpt": cs.excerpt(post),
        "date": post.get("date", ""),
        "date_formatted": cs.format_date(post.get("date", "")),
        "author": author,
        "author_initial": cs.author_initial(author),
        "author_color": cs.author_avatar(author),
        "rank": post.get("rank", ""),
        "team": post.get("team", ""),
        "cover": post.get("cover", ""),
        "cover_gradient": cs.cover_gradient(slug) if not post.get("cover") else None,
        "tags": post.get("tags", []),
        "reading_time": cs.reading_time(post),
        "views": cs.get_views(slug),
        "related": [
            {"slug": r.get("slug"), "title": r.get("title"),
             "date_formatted": cs.format_date(r.get("date", ""))}
            for r in related
        ],
    }


@router.get("/posts/{slug}/comments")
async def get_comments(slug: str):
    comments = cs.get_comments(slug)
    return {"comments": cs.build_comment_tree(comments), "count": cs.count_all(comments)}


@router.post("/posts/{slug}/comments")
async def post_comment(slug: str, payload: dict, request: Request):
    author = (payload.get("author") or "").strip()
    text = (payload.get("text") or "").strip()
    parent_id = payload.get("parent_id")
    if not author or not text:
        raise HTTPException(400, "Missing fields")
    if len(author) > 24 or len(text) > 1000:
        raise HTTPException(400, "Too long")

    pid = None
    if parent_id is not None:
        try: pid = int(parent_id)
        except (TypeError, ValueError): pid = None
        if pid is not None and not any(c["id"] == pid for c in cs.get_comments(slug)):
            pid = None

    try:
        cs.record_commenter(cs.get_client_ip(request), author, text)
    except Exception: pass

    c = cs.add_comment(slug, author, text, pid)
    return {"id": c["id"], "author": c["author"], "text": c["text"],
            "time": c["time"], "parent_id": c["parent_id"],
            "color": cs.author_avatar(c["author"]),
            "initial": cs.author_initial(c["author"])}