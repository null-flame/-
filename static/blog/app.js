/* Ghost Union — blog SPA client (REST API only) */
'use strict';

const $ = id => document.getElementById(id);
const APP = $('app');

const state = {
  site: null,
  posts: [],
  tags: {},
  activeTag: 'all',
  query: '',
};


/* ══════════════════════════════════════════════════════
   BOOT
   ══════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  applyTheme();
  applyDirection();
  wireToggles();
  loadSite();
  
  window.addEventListener('popstate', route);
  
  document.body.addEventListener('click', e => {
    const a = e.target.closest('a[href]');
    if (!a) return;
    const href = a.getAttribute('href');
    if (!href) return;

    // SPA should only intercept routes it can actually render.
    // Everything else (/chat, /admin, external links) must trigger a real browser navigation.
    const isSpaRoute =
      href === '/' ||
      href === '/about' ||
      href.startsWith('/post/');

    if (isSpaRoute && !a.target && !href.startsWith('//')) {
      e.preventDefault();
      history.pushState(null, '', href);
      route();
    }
  });
  
  route();
});


/* ══════════════════════════════════════════════════════
   THEME + DIRECTION
   ══════════════════════════════════════════════════════ */
function applyTheme() {
  let t = localStorage.getItem('ghost_theme');
  if (!t) t = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', t);
  const btn = $('themeBtn');
  if (btn) btn.textContent = t === 'dark' ? '◐' : '☀';
}

function applyDirection() {
  const saved = localStorage.getItem('ghost_dir');
  const server = document.documentElement.getAttribute('dir') || 'ltr';
  const dir = (saved === 'rtl' || saved === 'ltr') ? saved : server;
  document.documentElement.setAttribute('dir', dir);
  updateDirBtn();
}

function updateDirBtn() {
  const dir = document.documentElement.getAttribute('dir');
  const btn = $('dirBtn');
  if (btn) btn.textContent = dir === 'rtl' ? 'RTL' : 'LTR';
}

function wireToggles() {
  const themeBtn = $('themeBtn');
  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      const cur = document.documentElement.getAttribute('data-theme');
      const next = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('ghost_theme', next);
      themeBtn.textContent = next === 'dark' ? '◐' : '☀';
    });
  }

  const dirBtn = $('dirBtn');
  if (dirBtn) {
    dirBtn.addEventListener('click', () => {
      const cur = document.documentElement.getAttribute('dir');
      const next = cur === 'rtl' ? 'ltr' : 'rtl';
      document.documentElement.setAttribute('dir', next);
      localStorage.setItem('ghost_dir', next);
      updateDirBtn();
    });
  }
}


/* ══════════════════════════════════════════════════════
   SITE META
   ══════════════════════════════════════════════════════ */
async function loadSite() {
  try {
    const s = await fetchJSON('/api/blog/site');
    state.site = s;
    const sn = $('site-name');
    if (sn) sn.textContent = s.name;
    const fn = $('footer-name');
    if (fn) fn.textContent = s.name;
    document.title = s.name;
    if (s.logo) {
      const icon = document.querySelector('.nav-logo .ghost-icon');
      if (icon) {
        icon.outerHTML = `<img src="${esc(s.logo)}" alt="" style="width:36px;height:36px;border-radius:10px;object-fit:cover">`;
      }
    }
  } catch (e) { console.warn('Site load failed', e); }
}


/* ══════════════════════════════════════════════════════
   ROUTER
   ══════════════════════════════════════════════════════ */
function route() {
  const path = window.location.pathname;
  document.querySelectorAll('#nav-home, #nav-about').forEach(a =>
    a.classList.toggle('active', a.getAttribute('href') === path));

  if (path === '/' || path === '') return renderHome();
  if (path === '/about')            return renderAbout();
  if (path.startsWith('/post/'))    return renderPost(decodeURIComponent(path.slice(6)));
  renderNotFound();
}


/* ══════════════════════════════════════════════════════
   HOME
   ══════════════════════════════════════════════════════ */
async function renderHome() {
  APP.innerHTML = '<div class="empty"><div class="icon">👻</div><p>Loading…</p></div>';
  try {
    const [site, data] = await Promise.all([
      state.site ? Promise.resolve(state.site) : fetchJSON('/api/blog/site'),
      fetchJSON('/api/blog/posts'),
    ]);
    state.site = site;
    state.posts = data.posts;
    state.tags = data.tags;
    document.title = site.name;
    APP.innerHTML = buildHomeHTML(site, data);
    wireHomeEvents();
    if (window.GhostBidi) window.GhostBidi.isolate(APP);
  } catch (e) {
    APP.innerHTML = `<div class="empty"><div class="icon">⚠</div><p>${esc(e.message)}</p></div>`;
  }
}

function buildHomeHTML(site, data) {
  const posts = data.posts;
  const tags = data.tags;
  const cards = posts.map(cardHTML).join('') ||
    '<div class="empty"><div class="icon">👻</div><p>No posts yet.</p></div>';
  const tagBtns = Object.entries(tags)
    .map(([t, n]) =>
      `<button class="tag-btn" data-tag="${esc(t)}">${esc(t)} <span class="count">${n}</span></button>`)
    .join('');

  return `
    <div class="hero">
      <span class="ghost-big" aria-hidden="true">👻</span>
      <div class="tagline">${esc(site.tagline || 'Silent. Precise. Unified.')}</div>
      <h1>${esc(site.name)}</h1>
      <p>${esc(site.description || '')}</p>
      <a class="chat-cta" href="/chat">💬 Chat Room</a>
    </div>
    <div class="toolbar">
      <div class="search-box">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
          <circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>
        </svg>
        <input id="searchInput" placeholder="Search posts..." autocomplete="off">
      </div>
      <div class="tag-cloud">
        <button class="tag-btn active" data-tag="all">All</button>
        ${tagBtns}
      </div>
    </div>
    <div class="grid" id="grid">${cards}</div>
    <div class="empty" id="searchEmpty" style="display:none">
      <div class="icon">🔍</div><p>No posts match your search.</p>
    </div>`;
}

function cardHTML(p) {
  const cover = p.cover
    ? `<img src="${esc(p.cover)}" alt="" loading="lazy">`
    : `<div class="cover-placeholder" style="background:${esc(p.cover_gradient || '#222')}">👻</div>`;
  const coverStyle = p.cover ? ` style="--cover:url(&quot;${esc(p.cover)}&quot;)"` : '';
  const featured = p.featured ? '<span class="card-featured">Featured</span>' : '';
  const tagsHtml = p.tags.slice(0, 3).map(t => `<span>${esc(t)}</span>`).join('');

  return `
    <article class="card"
             data-title="${esc(p.title)}"
             data-excerpt="${esc(p.excerpt)}"
             data-tags="${esc(p.tags.join(','))}">
      <div class="card-cover"${coverStyle}>${cover}${featured}</div>
      <div class="card-body">
        <div class="card-meta">
          <span>${esc(p.date_formatted)}</span>
          <span class="dot"></span>
          <span>${p.reading_time} min</span>
          <span class="dot"></span>
          <span>👁 ${p.views}</span>
        </div>
        <h2 class="card-title">
          <a href="/post/${esc(p.slug)}">${esc(p.title)}</a>
        </h2>
        <p class="card-excerpt">${esc(p.excerpt)}</p>
        <div class="card-footer">
          <span class="avatar" style="background:${esc(p.author_color)}">${esc(p.author_initial)}</span>
          <div>
            <div class="card-author">${esc(p.author)}</div>
            <div class="card-rank">${esc(p.rank || '')}</div>
          </div>
          <div class="card-tags">${tagsHtml}</div>
        </div>
      </div>
    </article>`;
}

function wireHomeEvents() {
  const inp = $('searchInput');
  const tagBtns = Array.from(document.querySelectorAll('.tag-btn'));
  const cards = Array.from(document.querySelectorAll('.card'));
  const empty = $('searchEmpty');
  let raf = null;

  if (!inp || !empty) return;

  const apply = () => {
    raf = null;
    const q = inp.value.trim().toLowerCase();
    let shown = 0;
    for (const c of cards) {
      const text = (c.dataset.title + ' ' + c.dataset.excerpt + ' ' + c.dataset.tags).toLowerCase();
      const okQ = !q || text.includes(q);
      const okT = state.activeTag === 'all' ||
                  (',' + c.dataset.tags + ',').includes(',' + state.activeTag + ',');
      const vis = okQ && okT;
      c.style.display = vis ? '' : 'none';
      if (vis) shown++;
    }
    empty.style.display = shown ? 'none' : 'block';
  };
  const sched = () => { if (raf === null) raf = requestAnimationFrame(apply); };

  inp.addEventListener('input', sched);
  tagBtns.forEach(b => b.addEventListener('click', () => {
    tagBtns.forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    state.activeTag = b.dataset.tag;
    sched();
  }));
}


/* ══════════════════════════════════════════════════════
   ABOUT
   ══════════════════════════════════════════════════════ */
async function renderAbout() {
  APP.innerHTML = '<div class="empty"><div class="icon">👻</div><p>Loading…</p></div>';
  const site = state.site || await fetchJSON('/api/blog/site');
  document.title = 'About · ' + site.name;
  APP.innerHTML = `
    <div class="hero">
      <span class="ghost-big" aria-hidden="true">👻</span>
      <div class="tagline">About</div>
      <h1>${esc(site.name)}</h1>
      <p>${esc(site.description || '')}</p>
    </div>
    <div class="prose" style="max-width:780px;margin:0 auto">
      <h2>Our Values</h2>
      <p><strong>Silence.</strong> We move without noise. No egos, no theatrics — just precision.</p>
      <p><strong>Precision.</strong> Every action is deliberate.</p>
      <p><strong>Unity.</strong> We are one organism.</p>
      <p><strong>Trust.</strong> Earned through action.</p>
      <blockquote>Stay silent. Stay sharp. Stay unified.</blockquote>
      <a class="chat-cta" href="/chat">💬 Enter the Chat Room</a>
    </div>`;
}


/* ══════════════════════════════════════════════════════
   POST
   ══════════════════════════════════════════════════════ */
async function renderPost(slug) {
  APP.innerHTML = '<div class="empty"><div class="icon">👻</div><p>Loading…</p></div>';
  try {
    const [site, post, commentsData] = await Promise.all([
      state.site ? Promise.resolve(state.site) : fetchJSON('/api/blog/site'),
      fetchJSON(`/api/blog/posts/${encodeURIComponent(slug)}`),
      fetchJSON(`/api/blog/posts/${encodeURIComponent(slug)}/comments`),
    ]);
    document.title = `${post.title} · ${site.name}`;

    const tagsHtml = post.tags.map(t => `<span>${esc(t)}</span>`).join('');
    const cover = post.cover
      ? `<div class="post-cover" style="--cover:url(&quot;${esc(post.cover)}&quot;)">
           <img src="${esc(post.cover)}" alt=""></div>`
      : `<div class="post-cover no-img" style="--grad:${esc(post.cover_gradient || '#222')}">
           <span>👻</span></div>`;
    const related = (post.related || []).length ? `
      <div class="related">
        <h3>🔗 Related Posts</h3>
        <div class="related-list">
          ${post.related.map(r => `
            <div class="related-item">
              <a href="/post/${esc(r.slug)}">${esc(r.title)}</a>
              <div class="date">${esc(r.date_formatted)}</div>
            </div>`).join('')}
        </div>
      </div>` : '';

    APP.innerHTML = `
      <a class="back-link" href="/"><span class="arrow">←</span> Back to all posts</a>
      <article>
        <header class="post-header">
          <div class="post-tags-row">${tagsHtml}</div>
          <h1>${esc(post.title)}</h1>
          <div class="post-meta">
            <div class="author-block">
              <span class="avatar" style="background:${esc(post.author_color)}">${esc(post.author_initial)}</span>
              <div>
                <div class="author-name">${esc(post.author)}</div>
                <div class="author-rank">${esc(post.rank || '')} · ${esc(post.team || '')}</div>
              </div>
            </div>
            <div class="meta-right">
              <div>${esc(post.date_formatted)}</div>
              <div class="read">◷ ${post.reading_time} min · 👁 ${post.views}</div>
            </div>
          </div>
        </header>
        ${cover}
        <div class="prose">${post.content_html}</div>
      </article>
      ${related}
      <section class="comments">
        <h3>💬 Comments <span class="count" id="commentCount">${commentsData.count}</span></h3>
        <form class="comment-form" id="commentForm" data-slug="${esc(post.slug)}">
          <div class="row">
            <input id="cAuthor" placeholder="Your name" maxlength="24" required>
            <textarea id="cText" placeholder="Share your thoughts…" maxlength="1000" required></textarea>
          </div>
          <div style="margin-top:10px"><button type="submit">Post Comment</button></div>
        </form>
        <div class="comment-list" id="commentList">
          ${commentsData.comments.length
            ? commentsData.comments.map(c => `<div class="comment-thread">${renderCommentNode(c)}</div>`).join('')
            : '<div class="no-comments">No comments yet. Be the first.</div>'}
        </div>
      </section>`;

    wirePostEvents(post.slug);
    if (window.GhostBidi) window.GhostBidi.isolate(APP);
  } catch (e) {
    APP.innerHTML = `<div class="empty"><div class="icon">⚠</div><p>${esc(e.message)}</p></div>`;
  }
}

function renderCommentNode(c) {
  const replies = (c.replies || []).length
    ? `<div class="comment-replies">${c.replies.map(renderCommentNode).join('')}</div>`
    : '';
  return `
    <div class="comment${c.parent_id ? ' reply' : ''}" id="comment-${c.id}">
      <div class="comment-head">
        <span class="avatar" style="background:${esc(c.color)}">${esc(c.initial)}</span>
        <span class="comment-author">${esc(c.author)}</span>
        <span class="comment-time">${esc(c.time)}</span>
      </div>
      <div class="comment-text">${esc(c.text)}</div>
      <div class="comment-actions">
        <button type="button" data-reply-to="${c.id}" data-author="${esc(c.author)}">↩ Reply</button>
      </div>
      <div class="reply-slot" id="reply-slot-${c.id}"></div>
      ${replies}
    </div>`;
}

function wirePostEvents(slug) {
  const form = $('commentForm');
  if (form) {
    form.addEventListener('submit', async e => {
      e.preventDefault();
      await submitComment(slug, form, null);
    });
  }

  document.querySelectorAll('[data-reply-to]').forEach(btn => {
    btn.addEventListener('click', () => {
      const id = parseInt(btn.dataset.replyTo, 10);
      const author = btn.dataset.author;
      document.querySelectorAll('.reply-form').forEach(f => f.remove());
      const slot = $('reply-slot-' + id);
      if (!slot) return;
      const rf = document.createElement('form');
      rf.className = 'reply-form';
      rf.innerHTML = `
        <input placeholder="Your name" maxlength="24" required>
        <textarea placeholder="Reply to ${author}…" maxlength="1000" required></textarea>
        <div class="reply-form-actions">
          <button type="button" class="btn-cancel">Cancel</button>
          <button type="submit" class="btn-submit">Reply</button>
        </div>`;
      slot.appendChild(rf);
      rf.querySelector('input').focus();
      rf.querySelector('.btn-cancel').onclick = () => rf.remove();
      rf.addEventListener('submit', async ev => {
        ev.preventDefault();
        await submitComment(slug, rf, id);
      });
    });
  });
}

async function submitComment(slug, formEl, parentId) {
  const author = formEl.querySelector('input').value.trim();
  const text = formEl.querySelector('textarea').value.trim();
  if (!author || !text) return;
  const btn = formEl.querySelector('button[type="submit"]');
  const orig = btn.textContent;
  btn.disabled = true; btn.textContent = '…';
  try {
    const c = await fetchJSON(`/api/blog/posts/${encodeURIComponent(slug)}/comments`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ author, text, parent_id: parentId }),
    });
    appendComment(c, parentId);
    formEl.querySelector('textarea').value = '';
    if (parentId) formEl.remove();
  } catch (e) {
    alert('Comment failed: ' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = orig;
  }
}

function appendComment(c, parentId) {
  const list = $('commentList');
  if (!list) return;
  const empty = list.querySelector('.no-comments');
  if (empty) empty.remove();
  const html = renderCommentNode({ ...c, replies: [] });
  const div = document.createElement('div');
  div.innerHTML = html;
  const node = div.firstElementChild;

  if (parentId) {
    const parent = $('comment-' + parentId);
    if (parent) {
      const wrap = parent.parentElement;
      let replies = wrap.querySelector(':scope > .comment-replies');
      if (!replies) {
        replies = document.createElement('div');
        replies.className = 'comment-replies';
        wrap.appendChild(replies);
      }
      replies.appendChild(node);
    }
  } else {
    const wrap = document.createElement('div');
    wrap.className = 'comment-thread';
    wrap.appendChild(node);
    list.appendChild(wrap);
  }

  const cnt = $('commentCount');
  if (cnt) cnt.textContent = parseInt(cnt.textContent, 10) + 1;
  node.scrollIntoView({ behavior: 'smooth', block: 'center' });

  // rewire reply buttons inside newly added node
  node.querySelectorAll('[data-reply-to]').forEach(btn => {
    btn.addEventListener('click', () => {
      const id = parseInt(btn.dataset.replyTo, 10);
      const author = btn.dataset.author;
      document.querySelectorAll('.reply-form').forEach(f => f.remove());
      const slot = $('reply-slot-' + id);
      if (!slot) return;
      const rf = document.createElement('form');
      rf.className = 'reply-form';
      rf.innerHTML = `
        <input placeholder="Your name" maxlength="24" required>
        <textarea placeholder="Reply to ${author}…" maxlength="1000" required></textarea>
        <div class="reply-form-actions">
          <button type="button" class="btn-cancel">Cancel</button>
          <button type="submit" class="btn-submit">Reply</button>
        </div>`;
      slot.appendChild(rf);
      rf.querySelector('input').focus();
      rf.querySelector('.btn-cancel').onclick = () => rf.remove();
      rf.addEventListener('submit', async ev => {
        ev.preventDefault();
        const slug = $('commentForm').dataset.slug;
        await submitComment(slug, rf, id);
      });
    });
  });

  if (window.GhostBidi) window.GhostBidi.isolate(node);
}


/* ══════════════════════════════════════════════════════
   HELPERS
   ══════════════════════════════════════════════════════ */
async function fetchJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function renderNotFound() {
  APP.innerHTML = `<div class="empty"><div class="icon">👻</div><p>404 — Page not found.</p></div>`;
}


/* ══════════════════════════════════════════════════════
   BIDI ISOLATOR
   ══════════════════════════════════════════════════════ */
(function () {
  const LATIN_TEST = /[A-Za-z\u00C0-\u024F\u1E00-\u1EFF]/;
  const INNER = '0-9\\s.,!?;:\'"()\\[\\]{}\\-_@#$%^&*+=/\\\\|<>~`';
  const LTR_RUN = new RegExp(
    '[A-Za-z\\u00C0-\\u024F\\u1E00-\\u1EFF](?:[A-Za-z\\u00C0-\\u024F\\u1E00-\\u1EFF' + INNER +
    ']*[A-Za-z\\u00C0-\\u024F\\u1E00-\\u1EFF0-9])?', 'g');

  function isSkipped(el) {
    let cur = el;
    while (cur) {
      const tag = cur.tagName;
      if (['SCRIPT','STYLE','CODE','PRE','BDI','TEXTAREA','INPUT',
           'SELECT','OPTION','AUDIO','VIDEO'].includes(tag)) return true;
      cur = cur.parentElement;
    }
    return false;
  }

  function wrap(node) {
    const text = node.nodeValue;
    if (!text || !LATIN_TEST.test(text)) return;
    LTR_RUN.lastIndex = 0;
    const parts = [];
    let last = 0, m, changed = false;
    while ((m = LTR_RUN.exec(text)) !== null) {
      const raw = m[0];
      const trimmed = raw.replace(/\s+$/, '');
      const trailing = raw.slice(trimmed.length);
      if (!trimmed || !LATIN_TEST.test(trimmed)) continue;
      if (m.index > last) parts.push(document.createTextNode(text.slice(last, m.index)));
      const bdi = document.createElement('bdi');
      bdi.dir = 'ltr';
      bdi.className = 'bidi-ltr';
      bdi.textContent = trimmed;
      parts.push(bdi);
      if (trailing) parts.push(document.createTextNode(trailing));
      last = m.index + raw.length;
      changed = true;
    }
    if (!changed) return;
    if (last < text.length) parts.push(document.createTextNode(text.slice(last)));
    const frag = document.createDocumentFragment();
    parts.forEach(p => frag.appendChild(p));
    try { node.parentNode.replaceChild(frag, node); } catch (e) {}
  }

  window.GhostBidi = {
    isolate(root) {
      if (!root || !root.nodeType) return;
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
      const nodes = [];
      let n;
      while ((n = walker.nextNode())) {
        if (!isSkipped(n.parentElement)) nodes.push(n);
      }
      nodes.forEach(x => { try { wrap(x); } catch (e) {} });
    },
  };
})();