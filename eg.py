#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ghost Union — ویرایشگر پست (نسخه FastAPI)
-----------------------------------------
• فایل a.json را از کنار اسکریپت می‌خواند
• رابط وب کامل روی http://127.0.0.1:8000
• CRUD کامل برای پست‌ها + تنظیمات سایت
• خروجی دقیقاً با ساختار اصلی JSON ذخیره می‌شود

اجرا:
    pip install -r requirements.txt
    python ghost_editor_api.py
سپس مرورگر:
    http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

# ---------------------------------------------------------------- constants
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "a.json")

SITE_KEYS = ["name", "tagline", "description", "logo", "chat_url"]
POST_KEYS = [
    "id", "slug", "title", "excerpt", "content", "cover",
    "author", "team", "rank", "date", "tags", "featured",
]

DEFAULT_DATA = {
    "site": {
        "name": "Ghost Union",
        "tagline": "Silent. Precise. Unified.",
        "description": "Field notes from the operators of the Ghost Union.",
        "logo": "",
        "chat_url": "",
    },
    "posts": [],
}

# ---------------------------------------------------------------- helpers
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"[^\w\-\u0600-\u06FF]", "", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


def normalize_post(p: dict) -> dict:
    out = {}
    for k in POST_KEYS:
        v = p.get(k)
        if k == "tags":
            if isinstance(v, list):
                v = [str(t).strip() for t in v if str(t).strip()]
            else:
                v = [t.strip() for t in str(v or "").split(",") if t.strip()]
        elif k == "featured":
            v = bool(v)
        else:
            v = "" if v is None else str(v)
        out[k] = v
    return out


def normalize_data(raw: dict | None) -> dict:
    raw = raw or {}
    site_in = raw.get("site") or {}
    site = {k: ("" if site_in.get(k) is None else str(site_in.get(k))) for k in SITE_KEYS}
    posts = [normalize_post(p) for p in (raw.get("posts") or []) if isinstance(p, dict)]
    return {"site": site, "posts": posts}


def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        save_data(DEFAULT_DATA)
        return normalize_data(DEFAULT_DATA)
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return normalize_data(json.load(f))


def save_data(data: dict) -> None:
    data = normalize_data(data)
    if os.path.exists(DATA_FILE):
        try:
            shutil.copy2(DATA_FILE, DATA_FILE + ".bak")
        except OSError:
            pass
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)


def next_id(posts: list[dict]) -> str:
    nums = []
    for p in posts:
        m = re.fullmatch(r"p(\d+)", str(p.get("id", "")))
        if m:
            nums.append(int(m.group(1)))
    return f"p{max(nums) + 1 if nums else 1}"


# ---------------------------------------------------------------- app
app = FastAPI(title="Ghost Union Editor")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Ghost Union — ویرایشگر پست</title>
<style>
  :root{
    --bg:#0d1117; --panel:#161b22; --panel2:#1c2230;
    --border:#30363d; --text:#e6edf3; --muted:#8b949e;
    --accent:#3fb950; --accent2:#1f6feb; --danger:#f85149;
  }
  *{box-sizing:border-box}
  body{
    margin:0; background:var(--bg); color:var(--text);
    font-family:"Segoe UI","Vazirmatn",Tahoma,sans-serif;
    height:100vh; overflow:hidden;
  }
  header{
    display:flex; align-items:center; gap:10px;
    padding:10px 16px; background:var(--panel);
    border-bottom:1px solid var(--border);
  }
  header h1{font-size:16px;margin:0;color:var(--accent);font-weight:700}
  header .muted{color:var(--muted);font-size:12px}
  .btn{
    background:var(--panel2); color:var(--text); border:1px solid var(--border);
    padding:6px 12px; border-radius:6px; cursor:pointer; font-size:13px;
    transition:.15s;
  }
  .btn:hover{background:#232b3a;border-color:#4a5568}
  .btn.primary{background:var(--accent);color:#062b0f;border-color:var(--accent);font-weight:700}
  .btn.primary:hover{background:#4ad063}
  .btn.blue{background:var(--accent2);border-color:var(--accent2);color:#fff}
  .btn.blue:hover{background:#3b82f6}
  .btn.danger{background:transparent;border-color:var(--danger);color:var(--danger)}
  .btn.danger:hover{background:var(--danger);color:#fff}
  .btn.small{padding:3px 8px;font-size:12px}

  main{
    display:grid; grid-template-columns: 320px 1fr;
    height: calc(100vh - 52px);
  }

  aside{
    background:var(--panel); border-left:1px solid var(--border);
    padding:10px; overflow-y:auto;
  }
  aside h3{margin:2px 0 10px;font-size:13px;color:var(--muted);font-weight:600}
  .post-item{
    padding:8px 10px; border-radius:6px; cursor:pointer;
    border:1px solid transparent; margin-bottom:4px;
    display:flex; justify-content:space-between; align-items:center; gap:6px;
    font-size:13px;
  }
  .post-item:hover{background:var(--panel2)}
  .post-item.active{background:var(--panel2);border-color:var(--accent2)}
  .post-item .title{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .post-item .id{color:var(--muted);font-size:11px}
  .post-item .badge{
    background:var(--accent);color:#062b0f;font-size:10px;
    padding:1px 6px;border-radius:10px;font-weight:700;
  }

  section{
    padding:16px; overflow-y:auto; display:flex; flex-direction:column;
  }
  .toolbar{
    display:flex; gap:6px; flex-wrap:wrap; margin-bottom:14px;
    padding-bottom:12px; border-bottom:1px solid var(--border);
  }
  .grid{
    display:grid; grid-template-columns: 140px 1fr 140px 1fr;
    gap:10px 12px; align-items:center;
  }
  .grid label{color:var(--muted);font-size:12px;text-align:left}
  .grid .full{grid-column: 2 / -1}
  .grid .lbl-full{grid-column: 1 / 2}
  input[type=text], input[type=url], textarea{
    background:var(--panel); color:var(--text);
    border:1px solid var(--border); border-radius:6px;
    padding:7px 10px; font-size:13px; width:100%;
    font-family:inherit;
  }
  input:focus, textarea:focus{outline:none;border-color:var(--accent2)}
  textarea{resize:vertical;min-height:120px;line-height:1.7}
  textarea.content{min-height:340px;font-family:"Consolas",monospace;direction:ltr;text-align:left}
  .row-check{display:flex;align-items:center;gap:8px}
  input[type=checkbox]{width:16px;height:16px;accent-color:var(--accent)}

  .status{
    position:fixed; bottom:14px; left:14px;
    background:var(--panel); border:1px solid var(--border);
    padding:8px 14px; border-radius:8px; font-size:13px;
    opacity:0; transition:.25s; pointer-events:none;
  }
  .status.show{opacity:1}
  .status.ok{border-color:var(--accent);color:var(--accent)}
  .status.err{border-color:var(--danger);color:var(--danger)}

  .empty{
    color:var(--muted);text-align:center;padding:60px 20px;font-size:14px;
  }

  dialog{
    background:var(--panel); color:var(--text);
    border:1px solid var(--border); border-radius:10px;
    padding:20px; min-width:520px; max-width:90vw;
  }
  dialog::backdrop{background:rgba(0,0,0,.6)}
  dialog h2{margin-top:0;color:var(--accent);font-size:15px}
  dialog .grid{grid-template-columns:140px 1fr}
  dialog menu{display:flex;justify-content:flex-end;gap:8px;margin:16px 0 0;padding:0}
</style>
</head>
<body>
<header>
  <h1>👻 Ghost Union Editor</h1>
  <span class="muted" id="filePath"></span>
  <span style="flex:1"></span>
  <button class="btn blue" onclick="openSite()">⚙️ تنظیمات سایت</button>
  <button class="btn primary" onclick="saveAll()">💾 ذخیره a.json</button>
  <button class="btn" onclick="loadAll()">↻ بازخوانی</button>
</header>

<main>
  <aside>
    <h3>پست‌ها (<span id="count">0</span>)</h3>
    <div id="list"></div>
  </aside>

  <section>
    <div class="toolbar">
      <button class="btn primary" onclick="newPost()">＋ پست جدید</button>
      <button class="btn" onclick="duplicatePost()">⧉ کپی</button>
      <button class="btn danger" onclick="deletePost()">🗑 حذف</button>
      <button class="btn small" onclick="movePost(-1)">▲ بالا</button>
      <button class="btn small" onclick="movePost(1)">▼ پایین</button>
      <span style="flex:1"></span>
      <button class="btn" onclick="fillSlug()">اسلاگ خودکار</button>
      <button class="btn" onclick="fillNow()">تاریخ اکنون</button>
    </div>

    <div id="editor" style="display:none">
      <div class="grid">
        <label>شناسه (id)</label>
        <input type="text" id="f_id"/>

        <label>اسلاگ (slug)</label>
        <input type="text" id="f_slug"/>

        <label>نویسنده</label>
        <input type="text" id="f_author"/>

        <label>تیم</label>
        <input type="text" id="f_team"/>

        <label>رتبه</label>
        <input type="text" id="f_rank"/>

        <label>تاریخ (ISO)</label>
        <input type="text" id="f_date"/>

        <label>عنوان</label>
        <input type="text" id="f_title" class="full"/>

        <label>خلاصه</label>
        <input type="text" id="f_excerpt" class="full"/>

        <label>کاور (URL)</label>
        <input type="text" id="f_cover" class="full"/>

        <label>برچسب‌ها (با کاما)</label>
        <input type="text" id="f_tags" class="full"/>

        <label>ویژه</label>
        <div class="row-check">
          <input type="checkbox" id="f_featured"/>
          <span class="muted" style="font-size:12px">پست ویژه در صفحه اصلی</span>
        </div>

        <label class="lbl-full" style="align-self:flex-start;padding-top:8px">متن (Markdown)</label>
        <textarea id="f_content" class="content full"></textarea>
      </div>
    </div>

    <div class="empty" id="emptyMsg">
      هنوز پستی وجود ندارد. روی «＋ پست جدید» بزن.
    </div>
  </section>
</main>

<dialog id="siteDlg">
  <h2>تنظیمات سایت</h2>
  <div class="grid">
    <label>نام</label>          <input type="text" id="s_name"/>
    <label>شعار</label>         <input type="text" id="s_tagline"/>
    <label>توضیحات</label>      <input type="text" id="s_description"/>
    <label>لوگو (URL)</label>   <input type="text" id="s_logo"/>
    <label>چت (URL)</label>     <input type="text" id="s_chat_url"/>
  </div>
  <menu>
    <button class="btn primary" onclick="applySite()">تایید</button>
    <button class="btn" onclick="siteDlg.close()">انصراف</button>
  </menu>
</dialog>

<div class="status" id="status"></div>

<script>
const POST_KEYS = ["id","slug","title","excerpt","content","cover",
                   "author","team","rank","date","tags","featured"];
const SITE_KEYS = ["name","tagline","description","logo","chat_url"];

let state = { site:{}, posts:[] };
let current = -1;
const siteDlg = document.getElementById("siteDlg");

function showStatus(msg, kind="ok"){
  const el = document.getElementById("status");
  el.textContent = msg;
  el.className = "status show " + kind;
  clearTimeout(el._t);
  el._t = setTimeout(()=>{el.className = "status " + kind;}, 2500);
}

async function api(path, opts={}){
  const r = await fetch(path, {
    headers:{"Content-Type":"application/json"},
    ...opts,
  });
  if(!r.ok){
    const t = await r.text();
    throw new Error(t || r.statusText);
  }
  return r.headers.get("content-type")?.includes("json") ? r.json() : r.text();
}

async function loadAll(){
  try{
    state = await api("/api/data");
    document.getElementById("filePath").textContent = "→ a.json";
    document.getElementById("count").textContent = state.posts.length;
    renderList();
    if(state.posts.length){
      if(current < 0 || current >= state.posts.length) current = 0;
      openPost(current);
    }else{
      current = -1;
      document.getElementById("editor").style.display = "none";
      document.getElementById("emptyMsg").style.display = "block";
    }
  }catch(e){ showStatus("خطا در بارگذاری: "+e.message, "err"); }
}

function renderList(){
  const box = document.getElementById("list");
  box.innerHTML = "";
  state.posts.forEach((p,i)=>{
    const d = document.createElement("div");
    d.className = "post-item" + (i===current ? " active" : "");
    d.onclick = ()=> openPost(i);
    d.innerHTML = `
      <div class="title">${escapeHtml(p.title || "(بدون عنوان)")}</div>
      <div style="display:flex;gap:6px;align-items:center">
        ${p.featured ? '<span class="badge">ویژه</span>' : ''}
        <span class="id">${escapeHtml(p.id || "")}</span>
      </div>`;
    box.appendChild(d);
  });
  document.getElementById("count").textContent = state.posts.length;
}

function escapeHtml(s){
  return String(s ?? "").replace(/[&<>"']/g, c=>({
    "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
  }[c]));
}

function openPost(i){
  current = i;
  const p = state.posts[i];
  document.getElementById("editor").style.display = "block";
  document.getElementById("emptyMsg").style.display = "none";
  POST_KEYS.forEach(k=>{
    const el = document.getElementById("f_"+k);
    if(!el) return;
    if(k==="tags")      el.value = (p.tags||[]).join(", ");
    else if(k==="featured") el.checked = !!p.featured;
    else                 el.value = p[k] ?? "";
  });
  renderList();
}

function collectForm(){
  const p = {};
  POST_KEYS.forEach(k=>{
    const el = document.getElementById("f_"+k);
    if(!el) return;
    if(k==="tags")
      p.tags = el.value.split(",").map(s=>s.trim()).filter(Boolean);
    else if(k==="featured")
      p.featured = el.checked;
    else
      p[k] = el.value;
  });
  return p;
}

function syncCurrent(){
  if(current < 0) return;
  state.posts[current] = collectForm();
  renderList();
}

POST_KEYS.forEach(k=>{
  const el = document.getElementById("f_"+k);
  if(!el) return;
  el.addEventListener("input", syncCurrent);
  el.addEventListener("change", syncCurrent);
});

async function saveAll(){
  try{
    syncCurrent();
    state = await api("/api/data", {method:"PUT", body:JSON.stringify(state)});
    showStatus("ذخیره شد ✓");
    renderList();
  }catch(e){ showStatus("ذخیره ناموفق: "+e.message, "err"); }
}

function newPost(){
  const ids = state.posts.map(p=>{
    const m = /^p(\d+)$/.exec(p.id||""); return m?parseInt(m[1]):0;
  });
  const nid = "p" + ((Math.max(0,...ids))+1);
  const post = {
    id: nid, slug:"", title:"", excerpt:"", content:"", cover:"",
    author:"AMIR", team:"BLUE", rank:"Apex Defender",
    date: new Date().toISOString().replace(/\.\d+Z$/,"Z"),
    tags: [], featured:false
  };
  state.posts.push(post);
  openPost(state.posts.length-1);
  document.getElementById("f_title").focus();
}

function duplicatePost(){
  if(current < 0) return;
  syncCurrent();
  const ids = state.posts.map(p=>{
    const m = /^p(\d+)$/.exec(p.id||""); return m?parseInt(m[1]):0;
  });
  const nid = "p" + ((Math.max(0,...ids))+1);
  const clone = JSON.parse(JSON.stringify(state.posts[current]));
  clone.id = nid;
  clone.slug = (clone.slug||"") + "-copy";
  clone.title = (clone.title||"") + " (کپی)";
  state.posts.splice(current+1, 0, clone);
  openPost(current+1);
}

function deletePost(){
  if(current < 0) return;
  if(!confirm("این پست حذف شود؟")) return;
  state.posts.splice(current, 1);
  if(state.posts.length === 0){
    current = -1;
    document.getElementById("editor").style.display = "none";
    document.getElementById("emptyMsg").style.display = "block";
  }else{
    current = Math.min(current, state.posts.length-1);
    openPost(current);
  }
  renderList();
}

function movePost(d){
  if(current < 0) return;
  syncCurrent();
  const n = current + d;
  if(n < 0 || n >= state.posts.length) return;
  [state.posts[current], state.posts[n]] = [state.posts[n], state.posts[current]];
  current = n;
  renderList();
  openPost(n);
}

function fillNow(){
  document.getElementById("f_date").value =
    new Date().toISOString().replace(/\.\d+Z$/,"Z");
  syncCurrent();
}

function fillSlug(){
  const t = document.getElementById("f_title").value || "";
  const s = t.trim().toLowerCase()
    .replace(/[\s_]+/g,"-")
    .replace(/[^\w\-\u0600-\u06FF]/g,"")
    .replace(/-{2,}/g,"-")
    .replace(/^-|-$/g,"");
  document.getElementById("f_slug").value = s;
  syncCurrent();
}

function openSite(){
  SITE_KEYS.forEach(k=>{
    document.getElementById("s_"+k).value = state.site[k] ?? "";
  });
  siteDlg.showModal();
}

function applySite(){
  SITE_KEYS.forEach(k=>{
    state.site[k] = document.getElementById("s_"+k).value;
  });
  siteDlg.close();
  showStatus("تنظیمات سایت به‌روزرسانی شد (برای ذخیره روی 💾 بزن)");
}

document.addEventListener("keydown", e=>{
  if((e.ctrlKey||e.metaKey) && e.key.toLowerCase()==="s"){
    e.preventDefault(); saveAll();
  }
});

loadAll();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- routes
@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(INDEX_HTML)


@app.get("/api/data")
def get_data():
    return JSONResponse(load_data())


@app.put("/api/data")
async def put_data(request: Request):
    try:
        payload: Any = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"JSON نامعتبر: {exc}") from exc

    data = normalize_data(payload)
    # اطمینان از وجود id یکتا برای پست‌ها
    seen = set()
    for p in data["posts"]:
        if not p.get("id") or p["id"] in seen:
            p["id"] = next_id([{"id": x} for x in seen] + data["posts"])
        seen.add(p["id"])

    try:
        save_data(data)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"خطای ذخیره: {exc}") from exc

    return JSONResponse(normalize_data(data))


# ---------------------------------------------------------------- main
def main():
    print("┌──────────────────────────────────────────────┐")
    print("│  👻  Ghost Union Editor (FastAPI)            │")
    print(f"│  📄  {DATA_FILE}")
    print("│  🌐  http://127.0.0.1:8000                   │")
    print("└──────────────────────────────────────────────┘")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()