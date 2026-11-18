/* Hardlinkarr UI - dependency-free single page app */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const bytes = (n) => { if (!n) return "0 B"; const u = ["B", "KB", "MB", "GB", "TB"]; const i = Math.min(4, Math.floor(Math.log(n) / Math.log(1024))); return (n / 1024 ** i).toFixed(i > 2 ? 2 : 0) + " " + u[i]; };
const ago = (ts) => { if (!ts) return "never"; const s = Date.now() / 1000 - ts; if (s < 60) return "just now"; if (s < 3600) return Math.floor(s / 60) + "m ago"; if (s < 86400) return Math.floor(s / 3600) + "h ago"; return Math.floor(s / 86400) + "d ago"; };
const splitPath = (p) => { const i = (p || "").lastIndexOf("/"); return i < 0 ? ["", p || ""] : [p.slice(0, i + 1), p.slice(i + 1)]; };
const pathHtml = (p) => { const [d, f] = splitPath(p); return `<span class="path">${esc(d)}<b>${esc(f)}</b></span>`; };
const badge = (s) => `<span class="badge b-${esc(s)}">${esc(s)}</span>`;

async function api(method, url, body) {
  const r = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  let data = null;
  try { data = await r.json(); } catch { /* empty */ }
  if (!r.ok) throw new Error((data && data.detail) || `HTTP ${r.status}`);
  return data;
}
const GET = (u) => api("GET", u), POST = (u, b) => api("POST", u, b || {});

function toast(msg, err = false) {
  const t = document.createElement("div");
  t.className = "toast" + (err ? " err" : "");
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), err ? 7000 : 3500);
}

function modal(html, { wide = false } = {}) {
  const root = $("#modal-root");
  root.innerHTML = `<div class="modal-bg"><div class="modal" style="${wide ? "max-width:980px" : ""}">${html}</div></div>`;
  const bg = $(".modal-bg", root);
  bg.addEventListener("mousedown", (e) => { if (e.target === bg) closeModal(); });
  $$("[data-close]", root).forEach((b) => b.onclick = closeModal);
  return $(".modal", root);
}
const closeModal = () => { $("#modal-root").innerHTML = ""; };

function confirmBox(title, text, okLabel = "Confirm", danger = true) {
  return new Promise((resolve) => {
    const m = modal(`<div class="modal-head"><h2>${esc(title)}</h2></div><div>${text}</div>
      <div class="row" style="justify-content:flex-end;margin-top:18px"><button data-close>Cancel</button>
      <button class="${danger ? "danger" : "primary"}" id="ok">${esc(okLabel)}</button></div>`);
    $$("[data-close]", m).forEach((b) => b.onclick = () => { closeModal(); resolve(false); });
    $("#ok", m).onclick = () => { closeModal(); resolve(true); };
  });
}

/* ------------------------------------------------------------------ router */
const routes = {};
let current = null, pollTimer = null, settingsCache = null;

function navigate() {
  const [name, ...rest] = (location.hash.replace(/^#\/?/, "") || "dashboard").split("/");
  current = name;
  $$("nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === name));
  clearInterval(pollTimer);
  const fn = routes[name] || routes.dashboard;
  fn(rest).catch((e) => { $("#main").innerHTML = `<div class="notice err">${esc(e.message)}</div>`; });
}
window.addEventListener("hashchange", navigate);

async function refreshShell() {
  try {
    const s = await GET("/api/status");
    $("#version").textContent = "v" + s.version;
    const rv = (s.counts.review || 0) + (s.counts.duplicate || 0) + (s.counts.error || 0);
    const pill = $("#nav-review");
    pill.hidden = !rv; pill.textContent = rv;
    const j = s.job;
    $("#job-mini").innerHTML = j.running
      ? `<div class="row small"><span class="spinner"></span>${esc(j.phase)}</div>${j.total ? `<div class="progress" style="margin-top:6px"><div style="width:${(j.done / j.total) * 100}%"></div></div>` : ""}`
      : `<div class="small muted">Last scan ${ago(j.last_run)}</div>`;
    return s;
  } catch (e) { return null; }
}

/* ------------------------------------------------------------------ dashboard */
routes.dashboard = async () => {
  const s = await refreshShell();
  if (s && !s.setup_complete) { location.hash = "#/setup"; return; }
  let msCheck = null;
  GET("/api/mediaserver/check").then((c) => { msCheck = c; if (current === "dashboard") render(lastS); }).catch(() => {});
  let lastS = s;
  const render = (s) => {
    lastS = s;
    const c = s.counts, j = s.job;
    const stat = (n, l, st, color) => `<div class="stat" onclick="location.hash='#/library/${st}'"><div class="n" style="color:${color || "inherit"}">${n || 0}</div><div class="l">${l}</div></div>`;
    $("#main").innerHTML = `
      <div class="page-head"><div><h1>Dashboard</h1><div class="muted">Hardlinks keep your torrents seeding while your media server sees clean names.</div></div>
        <div class="row"><button id="full">Full rescan</button><button class="primary" id="scan" ${j.running ? "disabled" : ""}>${j.running ? '<span class="spinner"></span> Scanning' : "Scan now"}</button></div></div>
      ${s.dry_run ? `<div class="notice">Dry-run mode is on: nothing is linked, matches are shown as <b>planned</b>. Turn it off in <a href="#/settings">Settings</a> when you're happy.</div>` : ""}
      ${j.last_error ? `<div class="notice err">${esc(j.last_error)}</div>` : ""}
      ${mediaServerNotice(msCheck)}
      ${j.running ? `<div class="card"><div class="row" style="margin-bottom:8px"><span class="spinner"></span><b>${esc(j.phase)}</b><span class="spacer"></span><span class="muted">${j.done}/${j.total || "?"}</span></div><div class="progress"><div style="width:${j.total ? (j.done / j.total) * 100 : 3}%"></div></div></div>` : ""}
      <div class="grid stats">
        ${stat(c.linked, "Linked", "linked", "var(--accent)")}
        ${stat(c.exists, "Already in library", "exists", "var(--info)")}
        ${stat((c.review || 0) + (c.planned || 0), "Needs review", "review,planned", "var(--warn)")}
        ${stat(c.duplicate, "Conflicts", "duplicate", "var(--err)")}
        ${stat(c.error, "Errors", "error", "var(--err)")}
        <div class="stat"><div class="n">${bytes(s.space_saved)}</div><div class="l">Space saved by hardlinks</div></div>
      </div>
      <div class="grid two">
        <div class="card"><h2>Recent activity</h2>${s.recent.length ? `<table>${s.recent.map((i) => `<tr><td>${badge(i.status)}</td><td><div>${esc(i.title || splitPath(i.source_path)[1])}${i.season != null && i.media_type === "episode" ? ` <span class="muted">S${String(i.season).padStart(2, "0")}E${(i.episodes || []).map((e) => String(e).padStart(2, "0")).join("-E")}</span>` : ""}</div><div class="small muted">${esc(i.message || "")}</div></td><td class="small muted">${ago(i.updated)}</td></tr>`).join("")}</table>` : `<div class="empty"><div class="big">⇄</div>Nothing processed yet. Hit <b>Scan now</b>.</div>`}</div>
        <div class="card"><h2>Storage</h2>${s.disks.map((d) => `<div style="margin-bottom:14px"><div class="row small"><code>${esc(d.path)}</code><span class="spacer"></span><span class="muted">${bytes(d.free)} free of ${bytes(d.total)}</span></div><div class="progress" style="margin-top:6px"><div style="width:${(1 - d.free / d.total) * 100}%;background:${d.free / d.total < .1 ? "var(--err)" : "var(--accent)"}"></div></div></div>`).join("") || '<div class="muted">No folders configured.</div>'}
          <h3>Last scan</h3><div class="muted">${j.last_result ? Object.entries(j.last_result).map(([k, v]) => `${v} ${k}`).join(" · ") : "No scan yet"} — ${ago(j.last_run)}</div>
          <h3>Housekeeping</h3><div class="row"><a class="btn" href="#/organize">Rename existing library</a><a class="btn" href="#/cleanup/duplicates">Find duplicates</a><a class="btn" href="#/cleanup/empty">Empty folders</a><a class="btn" href="#/cleanup/orphans">Orphaned torrents</a></div>
        </div>
      </div>`;
    if ($("#howswitch")) $("#howswitch").onclick = (e) => { e.preventDefault(); switchGuide(msCheck); };
    $("#scan").onclick = async () => { await POST("/api/scan"); toast("Scan started"); tick(); };
    $("#full").onclick = async () => {
      if (!await confirmBox("Full rescan", "Re-identify every file, including ones already processed. Existing links are kept; new names only apply to files that aren't linked yet.", "Rescan", false)) return;
      await POST("/api/scan?full=true"); toast("Full rescan started"); tick();
    };
  };
  render(s);
  const tick = async () => { const s2 = await refreshShell(); if (s2 && current === "dashboard") render(s2); };
  pollTimer = setInterval(tick, 3000);
};

/* ------------------------------------------------------------------ library */
const STATUSES = [["", "All"], ["linked", "Linked"], ["review,planned", "Needs review"], ["duplicate", "Conflicts"], ["error", "Errors"], ["exists", "Already in library"], ["ignored", "Ignored"]];
routes.library = async ([status = ""]) => {
  let q = "", offset = 0;
  const sel = new Set();
  $("#main").innerHTML = `
    <div class="page-head"><div><h1>Library</h1><div class="muted">Every media file found in your downloads and where it was linked.</div></div></div>
    <div class="card"><div class="row" style="margin-bottom:12px"><div class="chips" id="chips"></div><span class="spacer"></span><input id="q" placeholder="Search…" style="max-width:260px"></div>
      <div class="row" id="bulk" style="margin-bottom:10px" hidden><b id="selcount"></b>
        <button class="sm primary" data-a="approve">Approve & link</button><button class="sm" data-a="retry">Re-identify</button>
        <button class="sm" data-a="ignore">Ignore</button><button class="sm danger" data-a="unlink">Remove link</button><button class="sm ghost" data-a="forget">Forget</button></div>
      <div class="table-wrap" id="tbl"></div><div class="row" id="pager" style="margin-top:12px"></div></div>`;
  const chips = () => { $("#chips").innerHTML = STATUSES.map(([v, l]) => `<button class="chip ${v === status ? "on" : ""}" data-s="${v}">${l}</button>`).join(""); $$("#chips .chip").forEach((c) => c.onclick = () => { status = c.dataset.s; offset = 0; sel.clear(); history.replaceState(null, "", "#/library/" + status); chips(); load(); }); };
  const load = async () => {
    const d = await GET(`/api/items?status=${encodeURIComponent(status)}&q=${encodeURIComponent(q)}&limit=100&offset=${offset}`);
    $("#tbl").innerHTML = d.items.length ? `<table><thead><tr><th style="width:28px"><input type="checkbox" id="all"></th><th>Status</th><th>Source → Library</th><th>Match</th><th></th></tr></thead><tbody>${d.items.map((i) => `
      <tr><td><input type="checkbox" data-id="${i.id}" ${sel.has(i.id) ? "checked" : ""}></td>
      <td>${badge(i.status)}${i.confidence != null && i.status !== "exists" ? `<div class="small muted" style="margin-top:4px">${Math.round(i.confidence * 100)}%</div>` : ""}</td>
      <td>${pathHtml(i.source_path)}${i.dest_path ? `<div class="arrow small">↳ ${pathHtml(i.dest_path)}</div>` : ""}${i.message ? `<div class="small muted" style="margin-top:3px">${esc(i.message)}</div>` : ""}</td>
      <td style="min-width:160px">${i.tmdb_id ? `<a href="https://www.themoviedb.org/${i.media_type === "episode" ? "tv" : "movie"}/${i.tmdb_id}" target="_blank">${esc(i.title)}</a> <span class="muted">${i.year || ""}</span>${i.media_type === "episode" && i.episodes ? `<div class="small muted">S${String(i.season ?? 0).padStart(2, "0")}E${i.episodes.map((e) => String(e).padStart(2, "0")).join("-E")} ${esc(i.episode_title || "")}</div>` : ""}<div class="small muted">${esc(i.match_source || "")}</div>` : '<span class="muted">—</span>'}</td>
      <td><div class="row" style="flex-wrap:nowrap">${["review", "planned"].includes(i.status) && i.dest_path ? `<button class="sm primary" data-approve="${i.id}">Approve</button>` : ""}${i.status === "duplicate" ? `<button class="sm" data-replace="${i.id}" title="Replace the existing library file with this one">Replace</button>` : ""}<button class="sm" data-fix="${i.id}">Fix match</button></div></td></tr>`).join("")}</tbody></table>`
      : `<div class="empty"><div class="big">∅</div>Nothing here.</div>`;
    $("#pager").innerHTML = d.total > 100 ? `<span class="muted">${offset + 1}–${Math.min(offset + 100, d.total)} of ${d.total}</span><span class="spacer"></span><button class="sm" id="prev" ${offset ? "" : "disabled"}>Prev</button><button class="sm" id="next" ${offset + 100 < d.total ? "" : "disabled"}>Next</button>` : `<span class="muted">${d.total} item(s)</span>`;
    if ($("#prev")) { $("#prev").onclick = () => { offset -= 100; load(); }; $("#next").onclick = () => { offset += 100; load(); }; }
    $$("[data-id]").forEach((c) => c.onchange = () => { c.checked ? sel.add(+c.dataset.id) : sel.delete(+c.dataset.id); upd(); });
    if ($("#all")) $("#all").onchange = (e) => { $$("[data-id]").forEach((c) => { c.checked = e.target.checked; c.checked ? sel.add(+c.dataset.id) : sel.delete(+c.dataset.id); }); upd(); };
    $$("[data-fix]").forEach((b) => b.onclick = () => fixMatch(+b.dataset.fix, load));
    $$("[data-approve]").forEach((b) => b.onclick = async () => { const r = await POST("/api/items/bulk", { action: "approve", ids: [+b.dataset.approve] }); toast("Result: " + Object.keys(r).join(", ")); load(); refreshShell(); });
    $$("[data-replace]").forEach((b) => b.onclick = async () => {
      if (!await confirmBox("Replace library file?", "The file currently at the destination will be replaced by a hardlink to this download. If the old file is not hardlinked elsewhere it will be deleted.", "Replace")) return;
      await POST("/api/items/bulk", { action: "replace", ids: [+b.dataset.replace] }); load(); refreshShell();
    });
  };
  const upd = () => { $("#bulk").hidden = !sel.size; $("#selcount").textContent = `${sel.size} selected`; };
  $$("#bulk [data-a]").forEach((b) => b.onclick = async () => {
    const a = b.dataset.a;
    if (a === "unlink" && !await confirmBox("Remove library links?", `Removes the hardlinked copy from your library for ${sel.size} item(s). The torrent data is untouched.`, "Remove links")) return;
    const r = await POST("/api/items/bulk", { action: a, ids: [...sel] });
    toast(Object.entries(r).map(([k, v]) => `${v} ${k}`).join(", ") || "Done");
    sel.clear(); upd(); load(); refreshShell();
  });
  let deb; $("#q").oninput = (e) => { clearTimeout(deb); deb = setTimeout(() => { q = e.target.value; offset = 0; load(); }, 250); };
  chips(); await load(); refreshShell();
};

async function fixMatch(id, done) {
  const m = modal(`<div class="modal-head"><h2>Fix match</h2><button class="ghost" data-close>✕</button></div><div id="fm">Loading…</div>`, { wide: true });
  let d;
  try { d = await GET(`/api/items/${id}/candidates`); } catch (e) { $("#fm", m).innerHTML = `<div class="notice err">${esc(e.message)}</div>`; return; }
  const p = d.parsed;
  let kind = d.kind, chosen = null;
  $("#fm", m).innerHTML = `
    <dl class="kv" style="margin-bottom:14px"><dt>Parsed title</dt><dd>${esc(p.title)} ${p.year ? `(${p.year})` : ""}</dd>
      ${p.season != null || p.episodes.length ? `<dt>Episode</dt><dd>S${p.season ?? "?"} E${p.episodes.join(",") || "?"}</dd>` : ""}
      ${p.resolution ? `<dt>Quality</dt><dd>${esc(p.resolution)} ${esc(p.source || "")}</dd>` : ""}</dl>
    <div class="row" style="margin-bottom:10px;flex-wrap:nowrap">
      <select id="kind" style="width:120px"><option value="movie">Movie</option><option value="tv">TV show</option></select>
      <input id="sq" value="${esc(p.title)}" placeholder="Search TMDB"><input id="sy" value="${p.year || ""}" placeholder="Year" style="width:90px">
      <button id="sgo">Search</button></div>
    <div class="results" id="res"></div>
    <div id="tvf" class="row" style="margin-top:12px" hidden><div class="field" style="width:120px;margin:0"><label>Season</label><input id="se" value="${p.season ?? 1}"></div>
      <div class="field" style="width:180px;margin:0"><label>Episode(s)</label><input id="ep" value="${p.episodes.join(",")}" placeholder="e.g. 3 or 3,4"></div></div>
    <div class="row" style="justify-content:flex-end;margin-top:16px"><button data-close>Cancel</button><button class="primary" id="apply" disabled>Match & link</button></div>`;
  $$("[data-close]", m).forEach((b) => b.onclick = closeModal);
  $("#kind", m).value = kind;
  const showRes = (rs) => {
    $("#res", m).innerHTML = rs.length ? rs.map((r, n) => `<div class="result" data-n="${n}">${r.poster ? `<img src="${esc(r.poster)}" loading="lazy">` : '<div class="noimg"></div>'}<div><div class="t">${esc(r.title)} <span class="muted">${r.year || ""}</span></div>${r.original_title && r.original_title !== r.title ? `<div class="small muted">${esc(r.original_title)}</div>` : ""}<div class="o">${esc(r.overview)}</div><div class="small muted">TMDB ${r.tmdb_id}</div></div></div>`).join("") : '<div class="muted">No results.</div>';
    $$(".result", m).forEach((el) => el.onclick = () => { $$(".result", m).forEach((x) => x.classList.remove("sel")); el.classList.add("sel"); chosen = rs[+el.dataset.n]; $("#apply", m).disabled = false; });
  };
  const doSearch = async () => {
    kind = $("#kind", m).value; $("#tvf", m).hidden = kind !== "tv";
    $("#res", m).innerHTML = '<span class="spinner"></span>'; chosen = null; $("#apply", m).disabled = true;
    try { showRes(await GET(`/api/search?kind=${kind}&q=${encodeURIComponent($("#sq", m).value)}${$("#sy", m).value ? "&year=" + $("#sy", m).value : ""}`)); } catch (e) { $("#res", m).innerHTML = `<div class="notice err">${esc(e.message)}</div>`; }
  };
  $("#tvf", m).hidden = kind !== "tv";
  showRes(d.results);
  $("#sgo", m).onclick = doSearch; $("#kind", m).onchange = doSearch;
  $("#sq", m).onkeydown = (e) => { if (e.key === "Enter") doSearch(); };
  $("#apply", m).onclick = async () => {
    $("#apply", m).disabled = true;
    try {
      const r = await POST(`/api/items/${id}/match`, { kind, tmdb_id: chosen.tmdb_id, season: kind === "tv" ? $("#se", m).value : null, episodes: kind === "tv" ? $("#ep", m).value : null });
      toast(r.result === "linked" ? "Linked → " + splitPath(r.dest_path)[1] : `Matched (${r.result}): ${r.message || ""}`, r.result === "error");
      closeModal(); done && done(); refreshShell();
    } catch (e) { toast(e.message, true); $("#apply", m).disabled = false; }
  };
}

/* ------------------------------------------------------------------ media server switch-over */
function mediaServerNotice(c) {
  if (!c || !c.configured) return "";
  if (c.error) return `<div class="notice err">Couldn't read your media server libraries: ${esc(c.error)}</div>`;
  const bad = c.points_at_downloads || [];
  if (bad.length) return `<div class="notice err"><b>Your ${esc(c.server)} libraries still scan the torrent folder.</b> They'll show raw torrent names until you switch them to the Hardlinkarr library:
    <ul style="margin:6px 0">${bad.map((l) => `<li>${esc(l.library)}: <code>${esc(l.path)}</code></li>`).join("")}</ul>
    <button class="sm" id="howswitch">How to switch without losing watch history</button></div>`;
  if (!c.uses_library && c.locations.length) return `<div class="notice">None of your ${esc(c.server)} libraries seem to point at the Hardlinkarr library folders (<code>${(c.library_roots || []).map(esc).join("</code>, <code>")}</code>). If ${esc(c.server)} sees them under a different path, that's fine. Otherwise <a href="#" id="howswitch">add them</a>.</div>`;
  return "";
}

function switchGuide(c) {
  const server = (c && c.server) || "plex";
  // prefer the clean folders as the media server itself sees them; skip ones nested in another
  const seen = ((c && c.locations) || []).filter((l) => l.verdict === "library").map((l) => l.path);
  const top = seen.filter((p) => !seen.some((o) => o !== p && p.startsWith(o.replace(/\/$/, "") + "/")));
  const roots = [...new Set(top.length ? top : ((c && c.library_roots) || []))];
  const bad = ((c && c.locations) || []).filter((l) => l.verdict === "downloads");
  const nested = bad.filter((b) => top.some((t) => b.path.startsWith(t.replace(/\/$/, "") + "/")));
  const here = roots.length ? ` (here: <code>${roots.map(esc).join("</code>, <code>")}</code>)` : " (your Movies and TV library folders)";
  const steps = server === "plex" ? [
    "Plex → Settings → Library: temporarily <b>turn off</b> “Empty trash automatically after every scan”.",
    `Edit each library (… → Edit → Add folders) and <b>add</b> the Hardlinkarr folder as seen by Plex${here}. Keep the old folder for now.`,
    "Scan the library and wait until it finishes. Plex matches the new files to the same movies/shows, so watch history and posters carry over.",
    "Edit the library again and <b>remove</b> the torrent/download folder. Scan again.",
    "… → Manage Library → <b>Empty Trash</b>, then turn the automatic trash setting back on.",
  ] : [
    `Dashboard → Libraries → open each library and <b>add</b> the Hardlinkarr folder as seen by ${esc(server)}${here}${roots.length && top.length ? " – already done ✓" : ""}.`,
    "Scan the library and wait until it finishes.",
    "<b>Remove</b> the torrent/download folder from the library and scan again. Watched status follows the TMDB/IMDb IDs, so it normally carries over.",
  ];
  if (nested.length) steps.push(`<code>${nested.map((b) => esc(b.path)).join("</code>, <code>")}</code> ${nested.length === 1 ? "sits" : "sit"} <b>inside</b> a library folder, so removing ${nested.length === 1 ? "it" : "them"} from the library isn't enough: ${esc(server)} still finds ${nested.length === 1 ? "it" : "them"} through the parent folder. To hide ${nested.length === 1 ? "it" : "them"}, put an empty file named <code>.ignore</code> inside${server === "plex" ? " (Plex: a <code>.plexignore</code> file in the parent with the folder name)" : ""}, or better, change the torrent client to save there no more.`);
  modal(`<div class="modal-head"><h2>Switch ${esc(server)} to the clean library</h2><button class="ghost" data-close>✕</button></div>
    <div class="notice info">The torrent folder stays where it is and keeps seeding. Your media server should only ever scan the library folders, which hold the renamed hardlinks.</div>
    <ol>${steps.map((s) => `<li style="margin-bottom:8px">${s}</li>`).join("")}</ol>
    <p class="muted small">If ${esc(server)} runs in Docker, it needs a volume that includes the library folder (e.g. the same <code>/data</code> mount).</p>
    <div class="row" style="justify-content:flex-end"><button class="primary" data-close>Got it</button></div>`);
}

/* ------------------------------------------------------------------ organize */
function organizeProgress(r) {
  const p = r.progress || {}, done = p.done || 0, total = p.total || 0;
  const pct = total ? Math.floor((done / total) * 100) : 0;
  const secs = r.started ? Date.now() / 1000 - r.started : 0;
  const eta = total && done > 20 ? Math.round(((total - done) * secs) / done / 60) : null;
  return `<div class="card"><div class="row" style="margin-bottom:8px"><span class="spinner"></span>
    <b>${total ? `Identifying ${done.toLocaleString()} of ${total.toLocaleString()} files` : "Listing library files…"}</b><span class="spacer"></span>
    <span class="muted">${total ? `${pct}%` : ""}${eta !== null ? ` · about ${eta < 1 ? "1 min" : eta + " min"} left` : ""}</span></div>
    <div class="bar"><div style="width:${pct}%"></div></div>
    <div class="small muted" style="margin-top:6px">${p.current ? esc(p.current) : ""}</div>
    <div class="small muted" style="margin-top:6px">You can leave this page; it keeps running in the background. Lookups are cached, so later scans are much faster.</div></div>`;
}

routes.organize = async () => {
  $("#main").innerHTML = `<div class="page-head"><div><h1>Organize library</h1><div class="muted">Rename files already in your library (including existing hardlinks) to your naming format. Renaming a hardlink never touches the seeding copy.</div></div></div>
    <div id="msn"></div><div id="oc"></div>`;
  GET("/api/mediaserver/check?fresh=true").then((c) => { $("#msn").innerHTML = mediaServerNotice(c); const b = $("#howswitch"); if (b) b.onclick = (e) => { e.preventDefault(); switchGuide(c); }; }).catch(() => {});
  const box = $("#oc");
  const poll = async (auto) => {
    const r = await GET("/api/cleanup/organize");
    if (r.running) {
      box.innerHTML = organizeProgress(r);
      if (auto) { clearInterval(pollTimer); pollTimer = setInterval(() => poll(false), 2000); }  // already running: keep watching
      return;
    }
    clearInterval(pollTimer);
    if (r.error) { box.innerHTML = `<div class="notice err">${esc(r.error)}</div><button id="rescan2">Try again</button>`; $("#rescan2").onclick = () => rescan(); return; }
    if (!r.result) {
      if (auto) { await POST("/api/cleanup/organize/scan"); pollTimer = setInterval(() => poll(false), 2000); return poll(false); }
      return;
    }
    renderOrganize(box, r.result, rescan);
  };
  const rescan = async () => { await POST("/api/cleanup/organize/scan"); clearInterval(pollTimer); pollTimer = setInterval(() => poll(false), 2000); poll(false); };
  await poll(true);
};

function renderOrganize(box, r, rescan) {
  if (r.error) { box.innerHTML = `<div class="notice err">${esc(r.error)}</div><button id="rescan">Retry</button>`; $("#rescan").onclick = rescan; return; }
  const moves = r.moves || [];
  box.innerHTML = `<div class="grid stats">
      <div class="stat"><div class="n" style="color:var(--accent)">${r.tidy}</div><div class="l">Already named correctly</div></div>
      <div class="stat"><div class="n" style="color:var(--warn)">${moves.length}</div><div class="l">To rename</div></div>
      <div class="stat"><div class="n">${(r.unknown || []).length}</div><div class="l">Not identified</div></div>
      <div class="stat"><div class="n" style="color:var(--info)">${r.arr_managed}</div><div class="l">Managed by Radarr/Sonarr (skipped)</div></div>
      ${r.seeding ? `<div class="stat"><div class="n" style="color:var(--info)">${r.seeding}</div><div class="l">Seeding from the library (skipped)</div></div>` : ""}</div>
    ${r.seeding ? `<div class="notice info">${r.seeding} file${r.seeding === 1 ? " is" : "s are"} the actual torrent data, because the torrent client saves straight into your library. Renaming ${r.seeding === 1 ? "it" : "them"} would stop seeding, so ${r.seeding === 1 ? "it's" : "they're"} left alone. To fix this for future downloads, point the client's save paths at your torrents folder; Hardlinkarr then links a clean copy into the library.</div>` : ""}
    ${r.arr_managed ?`<div class="notice info">Files in folders Radarr/Sonarr manage are left alone, because renaming them here would make the arr report them missing. Use the arr's own <i>Library → Rename files</i> for those, or turn off “Protect Radarr/Sonarr folders” in Settings → Automation.</div>` : ""}
    <div class="card"><div class="row" style="margin-bottom:12px"><div><h2 style="margin:0">Proposed renames</h2><div class="small muted">Scanned ${ago(r.generated)}. Confident matches are pre-selected; check the rest before applying.</div></div><span class="spacer"></span>
      <button id="rescan">Rescan</button>${moves.length ? `<button class="primary" id="apply">Rename selected</button>` : ""}</div>
      ${moves.length ? `<div class="table-wrap"><table><thead><tr><th style="width:28px"><input type="checkbox" id="all"></th><th>Current → New</th><th>Match</th></tr></thead><tbody>${moves.map((m, n) => `
        <tr><td><input type="checkbox" data-n="${n}" ${m.confident && !m.conflict ? "checked" : ""} ${m.conflict ? "disabled" : ""}></td>
        <td>${pathHtml(m.src)}<div class="arrow small">↳ ${pathHtml(m.dest)}</div>${m.message ? `<div class="small" style="color:var(--err)">${esc(m.message)}</div>` : m.conflict ? '<div class="small" style="color:var(--err)">a different file already has that name</div>' : ""}</td>
        <td style="min-width:170px"><a href="https://www.themoviedb.org/${m.kind === "tv" ? "tv" : "movie"}/${m.tmdb_id}" target="_blank">${esc(m.title)}</a> <span class="muted">${m.year || ""}</span>${m.episode ? `<div class="small muted">${m.episode}</div>` : ""}
          <div>${m.confident ? `<span class="badge b-ok">${Math.round(m.confidence * 100)}%</span>` : `<span class="badge b-warn">${Math.round(m.confidence * 100)}% – check</span>`}</div></td></tr>`).join("")}</tbody></table></div>`
        : `<div class="empty"><div class="big">✓</div>Everything identified is already named correctly.</div>`}</div>
    ${(r.unknown || []).length ? `<div class="card"><h2>Not identified</h2><div class="small muted" style="margin-bottom:8px">Rename the folder to “Title (Year)”, or add a <code>{tmdb-ID}</code> tag to it, then rescan.</div>${r.unknown.map((u) => `<div style="margin-bottom:6px">${pathHtml(u.src)} <span class="small muted">— ${esc(u.message)}</span></div>`).join("")}</div>` : ""}`;
  $("#rescan").onclick = rescan;
  if (!moves.length) return;
  $("#all").onchange = (e) => $$("[data-n]:not(:disabled)").forEach((c) => c.checked = e.target.checked);
  $("#apply").onclick = async () => {
    const sel = $$("[data-n]:checked").map((c) => moves[+c.dataset.n]);
    if (!sel.length) return toast("Nothing selected", true);
    if (!await confirmBox("Rename library files?", `<p>Rename ${sel.length} file(s), plus their subtitles. Folders left empty are removed.</p><p class="muted small">Only the library names change. Torrent copies keep seeding. Your media server re-matches the new paths on its next scan.</p>`, "Rename", false)) return;
    $("#apply").disabled = true;
    const res = await POST("/api/organize/apply", { moves: sel.map((m) => ({ src: m.src, dest: m.dest })) });
    toast(`Renamed ${res.done.length}${res.failed.length ? `, ${res.failed.length} failed: ${res.failed[0].error}` : ""}`, !!res.failed.length);
    clearInterval(pollTimer); pollTimer = setInterval(async () => { const x = await GET("/api/cleanup/organize"); if (!x.running) { clearInterval(pollTimer); renderOrganize(box, x.result, rescan); } }, 1500);
  };
}

/* ------------------------------------------------------------------ torrents */
routes.torrents = async () => {
  $("#main").innerHTML = `<div class="page-head"><div><h1>Torrents</h1><div class="muted">What your torrent client is seeding and how each one maps into the library.</div></div><button id="rl">Refresh</button></div><div class="card" id="tc"><span class="spinner"></span></div>`;
  const load = async () => {
    const d = await GET("/api/torrents");
    if (d.error) { $("#tc").innerHTML = `<div class="notice err">${esc(d.error)}</div><a class="btn" href="#/settings">Configure torrent client</a>`; return; }
    $("#tc").innerHTML = d.torrents.length ? `<div class="table-wrap"><table><thead><tr><th>Name</th><th>Category</th><th>State</th><th>Ratio</th><th>Library</th></tr></thead><tbody>${d.torrents.map((t) => {
      const i = t.items || {};
      const lib = !t.completed ? '<span class="muted">downloading</span>' : i.linked || i.existing ? `<span class="badge b-linked">${(i.linked || 0) + (i.existing || 0)} linked</span>` : i.review ? `<span class="badge b-review">${i.review} to review</span>` : i.errors ? `<span class="badge b-error">error</span>` : '<span class="muted">—</span>';
      return `<tr><td>${esc(t.name)}<div class="small muted mono">${esc(t.save_path)}</div></td><td>${esc(t.category || "—")}</td><td>${t.completed ? esc(t.state) : `${Math.round(t.progress * 100)}%`}${t.error ? ' <span class="badge b-error">error</span>' : ""}</td><td>${t.ratio}</td><td>${lib}${i.review ? ` <a href="#/library/review">review</a>` : ""}</td></tr>`;
    }).join("")}</tbody></table></div>` : '<div class="empty">No torrents.</div>';
  };
  $("#rl").onclick = load;
  await load();
};

/* ------------------------------------------------------------------ cleanup */
routes.cleanup = async ([tab = "duplicates"]) => {
  $("#main").innerHTML = `<div class="page-head"><div><h1>Cleanup</h1><div class="muted">Nothing is deleted without your confirmation.</div></div></div>
    <div class="tabs">${[["duplicates", "Duplicates"], ["empty", "Empty folders"], ["orphans", "Orphaned torrents & files"]].map(([k, l]) => `<button class="tab ${k === tab ? "on" : ""}" data-t="${k}">${l}</button>`).join("")}</div><div id="cc"></div>`;
  $$(".tab").forEach((t) => t.onclick = () => { location.hash = "#/cleanup/" + t.dataset.t; });
  const box = $("#cc");
  const poll = async (auto) => {
    const r = await GET("/api/cleanup/" + tab);
    if (r.running) { box.innerHTML = `<div class="card"><span class="spinner"></span> Scanning… this can take a while on big libraries.</div>`; return; }
    clearInterval(pollTimer);
    if (r.error) { box.innerHTML = `<div class="notice err">${esc(r.error)}</div>`; }
    if (!r.result) {
      if (auto) { await POST(`/api/cleanup/${tab}/scan`); pollTimer = setInterval(() => poll(false), 1500); return poll(false); }
      return;
    }
    ({ duplicates: renderDupes, empty: renderEmpty, orphans: renderOrphans })[tab](box, r.result, rescan);
  };
  const rescan = async () => { await POST(`/api/cleanup/${tab}/scan`); clearInterval(pollTimer); pollTimer = setInterval(() => poll(false), 1500); poll(false); };
  await poll(true);
};

const head = (title, sub, extra = "") => `<div class="row" style="margin-bottom:14px"><div><h2 style="margin:0">${title}</h2><div class="muted small">${sub}</div></div><span class="spacer"></span>${extra}<button id="rescan">Rescan</button></div>`;

function renderEmpty(box, list, rescan) {
  box.innerHTML = `<div class="card">${head("Empty folders", "Folders with no media left (only junk like .nfo/.txt/images, as set in Settings).", list.length ? `<button class="danger" id="del">Delete selected</button>` : "")}
    ${list.length ? `<table><thead><tr><th style="width:28px"><input type="checkbox" id="all" checked></th><th>Folder</th><th>Junk files</th></tr></thead><tbody>${list.map((e, n) => `<tr><td><input type="checkbox" data-n="${n}" checked></td><td>${pathHtml(e.path)}</td><td class="muted">${e.junk_files}</td></tr>`).join("")}</tbody></table>` : `<div class="empty"><div class="big">✓</div>No empty folders.</div>`}</div>`;
  $("#rescan").onclick = rescan;
  if (!list.length) return;
  $("#all").onchange = (e) => $$("[data-n]").forEach((c) => c.checked = e.target.checked);
  $("#del").onclick = async () => {
    const paths = $$("[data-n]:checked").map((c) => list[+c.dataset.n].path);
    if (!paths.length || !await confirmBox("Delete empty folders?", `Delete ${paths.length} folder(s) and the junk files inside them?`, "Delete")) return;
    const r = await POST("/api/cleanup/empty/remove", { paths });
    toast(`Removed ${r.removed.length}${r.failed.length ? `, ${r.failed.length} failed` : ""}`, !!r.failed.length);
    rescan();
  };
}

function fileRow(f, name, checked) {
  return `<div class="f"><input type="checkbox" name="${name}" value="${esc(f.path)}" ${checked ? "checked" : ""}>
    <div style="flex:1;min-width:0">${pathHtml(f.path)}</div>
    ${f.keep ? '<span class="badge b-ok">keep</span>' : ""}${f.resolution ? `<span class="badge b-ignored">${esc(f.resolution)}</span>` : ""}
    <span class="small muted" style="width:80px;text-align:right">${bytes(f.size)}</span>
    <span class="small muted" title="number of hardlinks to this data" style="width:70px">${f.nlink} link${f.nlink > 1 ? "s" : ""}</span></div>`;
}

function renderDupes(box, r, rescan) {
  const sec = (title, sub, groups, name, extraBtn) => `<div class="card"><div class="row" style="margin-bottom:10px"><div><h2 style="margin:0">${title} <span class="muted">(${groups.length})</span></h2><div class="small muted">${sub}</div></div><span class="spacer"></span>${groups.length ? (extraBtn || "") + `<button class="danger sm" data-del="${name}">Delete selected</button>` : ""}</div>
    ${groups.length ? groups.map((g) => `<div class="dup-group"><div class="gh">${esc(g.label)}</div>${g.files.map((f) => fileRow(f, name, !f.keep)).join("")}</div>`).join("") : '<div class="muted">None found.</div>'}</div>`;
  box.innerHTML = `${head("Duplicates", `Scanned ${ago(r.generated)}.`)}
    ${sec("Same movie / episode more than once", "Different files for the same title in your library. The highest quality is marked keep; others are pre-selected.", r.media, "media")}
    ${sec("Identical copies (not hardlinked)", `Same content stored twice, wasting <b>${bytes(r.wasted_bytes)}</b>. Converting turns the extra copies into hardlinks – nothing visible changes, the space comes back.`, r.copies, "copies", `<button class="primary sm" id="relink">Convert selected to hardlinks</button>`)}
    ${sec("Duplicate links", "The same data appears under several library paths, so your media server shows it twice.", r.redundant_links, "redundant")}`;
  $("#rescan").onclick = rescan;
  $$("[data-del]").forEach((b) => b.onclick = async () => {
    const paths = $$(`input[name=${b.dataset.del}]:checked`).map((c) => c.value);
    if (!paths.length) return toast("Nothing selected", true);
    if (!await confirmBox("Delete files?", `<p>Permanently delete ${paths.length} file(s)?</p><p class="muted small">Files with more than one link keep their data in the other location (e.g. still seeding).</p><div class="path">${paths.map(esc).join("<br>")}</div>`, "Delete")) return;
    const res = await POST("/api/cleanup/files/delete", { paths, report: "duplicates" });
    toast(`Deleted ${res.removed.length}${res.failed.length ? `, ${res.failed.length} failed: ${res.failed[0].error}` : ""}`, !!res.failed.length);
    rescan();
  });
  if ($("#relink")) $("#relink").onclick = async () => {
    let n = 0, failed = 0;
    for (const g of r.copies) {
      const keep = g.files.find((f) => f.keep).path;
      const targets = g.files.filter((f) => !f.keep).map((f) => f.path).filter((p) => $$("input[name=copies]:checked").some((c) => c.value === p));
      if (!targets.length) continue;
      const res = await POST("/api/cleanup/duplicates/relink", { keep, targets });
      n += res.relinked.length; failed += res.failed.length;
    }
    toast(`Converted ${n} file(s)${failed ? `, ${failed} failed` : ""}`, !!failed); rescan();
  };
}

function renderOrphans(box, r, rescan) {
  box.innerHTML = `${head("Orphans", `Scanned ${ago(r.generated)} across ${r.torrent_count ?? 0} torrents.`)}
    ${(r.warnings || []).map((w) => `<div class="notice">${esc(w)}</div>`).join("")}
    ${r.error ? `<div class="notice err">${esc(r.error)}</div>` : ""}
    <div class="card"><div class="row" style="margin-bottom:10px"><div><h2 style="margin:0">Orphaned torrents <span class="muted">(${r.torrents.length})</span></h2><div class="small muted">Torrents whose data is missing, or whose media is no longer in your library (e.g. you deleted it in Plex/Jellyfin).</div></div><span class="spacer"></span>
      ${r.torrents.length ? `<button class="sm" id="rt">Remove torrent only</button><button class="sm danger" id="rtd">Remove torrent + data</button>` : ""}</div>
      ${r.torrents.length ? `<table><thead><tr><th style="width:28px"><input type="checkbox" id="allt"></th><th>Torrent</th><th>Reason</th><th>Ratio</th><th>Size</th></tr></thead><tbody>${r.torrents.map((t) => `<tr><td><input type="checkbox" name="ot" value="${esc(t.hash)}"></td><td>${esc(t.name)}<div class="small muted">${esc(t.category || "")} · added ${ago(t.added_on)}</div></td><td class="small">${esc(t.reason)}</td><td>${t.ratio}</td><td class="muted">${t.size ? bytes(t.size) : "—"}</td></tr>`).join("")}</tbody></table>` : '<div class="muted">None found.</div>'}</div>
    <div class="card"><div class="row" style="margin-bottom:10px"><div><h2 style="margin:0">Orphaned files <span class="muted">(${r.files.length})</span></h2><div class="small muted">Files in your download folders that don't belong to any torrent in the client.</div></div><span class="spacer"></span>${r.files.length ? `<button class="sm danger" id="df">Delete selected</button>` : ""}</div>
      ${r.files.length ? `<table><thead><tr><th style="width:28px"><input type="checkbox" id="allf"></th><th>Path</th><th>Files</th><th>Size</th><th>In library?</th></tr></thead><tbody>${r.files.map((f) => `<tr><td><input type="checkbox" name="of" value="${esc(f.path)}"></td><td>${pathHtml(f.path)}</td><td>${f.files}</td><td>${bytes(f.size)}</td><td>${f.linked ? `<span class="badge b-exists">${f.linked} linked</span><div class="small muted">library copy survives</div>` : '<span class="muted">no</span>'}</td></tr>`).join("")}</tbody></table>` : '<div class="muted">None found.</div>'}</div>`;
  $("#rescan").onclick = rescan;
  const all = (id, name) => { if ($(id)) $(id).onchange = (e) => $$(`input[name=${name}]`).forEach((c) => c.checked = e.target.checked); };
  all("#allt", "ot"); all("#allf", "of");
  const rm = async (withData) => {
    const hashes = $$("input[name=ot]:checked").map((c) => c.value);
    if (!hashes.length) return toast("Nothing selected", true);
    if (!await confirmBox("Remove torrents?", `Remove ${hashes.length} torrent(s) from the client${withData ? " <b>and delete their downloaded data</b>. Library hardlinks (if any) are not affected" : ". Data stays on disk"}.`, "Remove")) return;
    try { await POST("/api/cleanup/torrents/remove", { hashes, delete_files: withData }); toast("Removed"); rescan(); } catch (e) { toast(e.message, true); }
  };
  if ($("#rt")) { $("#rt").onclick = () => rm(false); $("#rtd").onclick = () => rm(true); }
  if ($("#df")) $("#df").onclick = async () => {
    const paths = $$("input[name=of]:checked").map((c) => c.value);
    if (!paths.length) return toast("Nothing selected", true);
    if (!await confirmBox("Delete orphaned files?", `Permanently delete ${paths.length} item(s) from your download folders?`, "Delete")) return;
    const res = await POST("/api/cleanup/files/delete", { paths, report: "orphans" });
    toast(`Deleted ${res.removed.length}${res.failed.length ? `, ${res.failed.length} failed` : ""}`, !!res.failed.length); rescan();
  };
}

/* ------------------------------------------------------------------ logs */
routes.logs = async () => {
  $("#main").innerHTML = `<div class="page-head"><div><h1>Logs</h1></div><div class="row"><select id="lv" style="width:140px"><option value="">All levels</option><option>info</option><option>warning</option><option>error</option></select><button id="rl">Refresh</button></div></div><div class="card" id="lc"></div>`;
  const load = async () => {
    const d = await GET("/api/logs?level=" + $("#lv").value);
    $("#lc").innerHTML = d.length ? d.map((l) => `<div class="log-line"><span class="muted" style="width:150px;flex:none">${new Date(l.ts * 1000).toLocaleString()}</span><span class="lv lv-${l.level}">${l.level}</span><span>${esc(l.msg)}</span></div>`).join("") : '<div class="muted">No log entries.</div>';
  };
  $("#rl").onclick = load; $("#lv").onchange = load;
  await load();
  pollTimer = setInterval(load, 10000);
};

/* ------------------------------------------------------------------ settings form helpers */
const F = {
  text: (k, label, hint = "", ph = "") => `<div class="field"><label for="f-${k}">${label}</label><input id="f-${k}" data-k="${k}" placeholder="${esc(ph)}">${hint ? `<div class="hint">${hint}</div>` : ""}</div>`,
  secret: (k, label, hint = "") => `<div class="field"><label for="f-${k}">${label}</label><input id="f-${k}" data-k="${k}" type="password" autocomplete="off">${hint ? `<div class="hint">${hint}</div>` : ""}</div>`,
  num: (k, label, hint = "", step = "1") => `<div class="field"><label for="f-${k}">${label}</label><input id="f-${k}" data-k="${k}" type="number" step="${step}">${hint ? `<div class="hint">${hint}</div>` : ""}</div>`,
  bool: (k, label, hint = "") => `<div class="field inline"><input id="f-${k}" data-k="${k}" type="checkbox"><div><label for="f-${k}">${label}</label>${hint ? `<div class="hint" style="margin:0">${hint}</div>` : ""}</div></div>`,
  select: (k, label, opts, hint = "") => `<div class="field"><label for="f-${k}">${label}</label><select id="f-${k}" data-k="${k}">${opts.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>${hint ? `<div class="hint">${hint}</div>` : ""}</div>`,
  list: (k, label, hint = "", ph = "") => `<div class="field"><label for="f-${k}">${label}</label><input id="f-${k}" data-k="${k}" data-list="1" placeholder="${esc(ph)}">${hint ? `<div class="hint">${hint}</div>` : ""}</div>`,
  folder: (k, label, hint = "") => `<div class="field"><label for="f-${k}">${label}</label><div class="input-btn"><input id="f-${k}" data-k="${k}" ${k === "download_dirs" ? 'data-list="1"' : ""}><button type="button" data-browse="${k}">Browse</button></div>${hint ? `<div class="hint">${hint}</div>` : ""}</div>`,
};

function fillForm(root, s) {
  $$("[data-k]", root).forEach((el) => {
    const v = s[el.dataset.k];
    if (el.type === "checkbox") el.checked = !!v;
    else if (el.dataset.list) el.value = (v || []).join(", ");
    else if (el.dataset.json) el.value = JSON.stringify(v || {});
    else el.value = v ?? "";
  });
  renderMappings(root, s.path_mappings || []);
  renderCatTypes(root, s.category_types || {});
}

function readForm(root) {
  const out = {};
  $$("[data-k]", root).forEach((el) => {
    const k = el.dataset.k;
    if (el.type === "checkbox") out[k] = el.checked;
    else if (el.type === "number") out[k] = el.value === "" ? null : Number(el.value);
    else if (el.dataset.list) out[k] = el.value.split(",").map((x) => x.trim()).filter(Boolean);
    else out[k] = el.value.trim();
  });
  const mp = $("#mappings", root);
  if (mp) out.path_mappings = $$(".mapping", mp).map((r) => ({ remote: $(".r", r).value.trim(), local: $(".l", r).value.trim() })).filter((m) => m.remote && m.local);
  const ct = $("#cattypes", root);
  if (ct) { out.category_types = {}; $$(".mapping", ct).forEach((r) => { const c = $(".r", r).value.trim(); if (c) out.category_types[c] = $(".l", r).value; }); }
  return out;
}

function renderMappings(root, maps) {
  const el = $("#mappings", root); if (!el) return;
  const row = (m = {}) => `<div class="mapping"><input class="r" placeholder="Path in torrent client e.g. /downloads" value="${esc(m.remote || "")}"><span class="arrow">→</span><input class="l" placeholder="Path here e.g. /data/torrents" value="${esc(m.local || "")}"><button type="button" class="ghost" data-rm>✕</button></div>`;
  el.innerHTML = maps.map(row).join("") + `<button type="button" class="sm" id="addmap">+ Add mapping</button>`;
  $("#addmap", el).onclick = () => { $("#addmap", el).insertAdjacentHTML("beforebegin", row()); bindRm(el); };
  bindRm(el);
}
function renderCatTypes(root, map) {
  const el = $("#cattypes", root); if (!el) return;
  const row = (c = "", t = "movie") => `<div class="mapping"><input class="r" placeholder="category" value="${esc(c)}"><span class="arrow">→</span><select class="l"><option value="movie" ${t === "movie" ? "selected" : ""}>Movies</option><option value="tv" ${t === "tv" ? "selected" : ""}>TV / Anime</option></select><button type="button" class="ghost" data-rm>✕</button></div>`;
  el.innerHTML = Object.entries(map).map(([c, t]) => row(c, t)).join("") + `<button type="button" class="sm" id="addcat">+ Add category</button>`;
  $("#addcat", el).onclick = () => { $("#addcat", el).insertAdjacentHTML("beforebegin", row()); bindRm(el); };
  bindRm(el);
}
const bindRm = (el) => $$("[data-rm]", el).forEach((b) => b.onclick = () => b.parentElement.remove());

function bindBrowse(root) {
  $$("[data-browse]", root).forEach((b) => b.onclick = async () => {
    const input = $(`[data-k="${b.dataset.browse}"]`, root);
    const multi = !!input.dataset.list;
    let path = (multi ? input.value.split(",").pop().trim() : input.value) || "/";
    const m = modal(`<div class="modal-head"><h2>Choose folder</h2><button class="ghost" data-close>✕</button></div><div class="row"><input id="bp"><button id="bup">Up</button></div><div class="browser" id="bl"></div><div class="row" style="justify-content:flex-end;margin-top:14px"><button data-close>Cancel</button><button class="primary" id="bok">Select this folder</button></div>`);
    const go = async (p) => {
      try { const d = await GET("/api/browse?path=" + encodeURIComponent(p)); path = d.path; $("#bp", m).value = d.path; $("#bl", m).innerHTML = d.dirs.map((x) => `<div data-d="${esc(x)}">📁 ${esc(x)}</div>`).join("") || '<div class="muted">No sub-folders</div>'; $$("[data-d]", m).forEach((x) => x.onclick = () => go((path === "/" ? "" : path) + "/" + x.dataset.d)); }
      catch (e) { if (p !== "/") go("/"); else toast(e.message, true); }
    };
    $("#bup", m).onclick = () => go(path.split("/").slice(0, -1).join("/") || "/");
    $("#bp", m).onkeydown = (e) => { if (e.key === "Enter") go(e.target.value); };
    $("#bok", m).onclick = () => {
      if (multi) { const parts = input.value.split(",").map((x) => x.trim()).filter(Boolean); if (!parts.includes(path)) parts.push(path); input.value = parts.join(", "); } else input.value = path;
      input.dispatchEvent(new Event("change")); closeModal();
    };
    go(path);
  });
}

function bindTests(root) {
  $$("[data-test]", root).forEach((b) => b.onclick = async () => {
    const out = $(`#t-${b.dataset.test}`, root);
    out.innerHTML = '<span class="spinner"></span>';
    try {
      const r = await POST("/api/test/" + b.dataset.test, readForm(root));
      let html = `<div class="notice ${r.ok ? "ok" : "err"}">${esc(r.message)}</div>`;
      if (r.checks) html += r.checks.map((c) => `<div class="check">${c.ok ? "✅" : "❌"}<div><code>${esc(c.from)}</code> → <code>${esc(c.to)}</code><div class="small muted">${esc(c.message)}</div></div></div>`).join("");
      if (r.libraries) html += r.libraries.length ? `<table class="small" style="margin-top:8px">${r.libraries.map((l) => `<tr><td>${esc(l.library)}</td><td><code>${esc(l.path)}</code></td><td>${l.verdict === "downloads" ? '<span class="badge b-error">torrent folder – switch it</span>' : l.verdict === "library" ? '<span class="badge b-ok">clean library ✓</span>' : '<span class="badge b-ignored">other</span>'}</td></tr>`).join("")}</table>${r.libraries.some((l) => l.verdict === "downloads") ? '<button type="button" class="sm" id="hs2" style="margin-top:8px">How to switch these libraries</button>' : ""}` : '<div class="small muted">No movie/TV libraries found.</div>';
      if (r.categories && r.categories.length) html += `<div class="small muted">Categories found: ${r.categories.map((c) => `<code>${esc(c)}</code>`).join(" ")}</div>`;
      let addedMaps = [];
      if (r.suggested_mappings && r.suggested_mappings.length) {
        // fill in the mappings the server worked out, keeping any the user already entered
        const cur = readForm(root).path_mappings || [];
        addedMaps = r.suggested_mappings.filter((m) => !cur.some((c) => c.remote.replace(/\/$/, "") === m.remote));
        if (addedMaps.length) renderMappings(root, [...cur, ...addedMaps]);
        html += `<div class="notice ok" style="margin-top:8px">${addedMaps.length ? "Path mappings detected and filled in below:" : "Path mappings detected:"} ${r.suggested_mappings.map((m) => `<div><code>${esc(m.remote)}</code> → <code>${esc(m.local)}</code> <span class="muted small">(${m.count} torrent${m.count === 1 ? "" : "s"})</span></div>`).join("")}</div>`;
      }
      const unresolved = (r.missing || 0) - (r.suggested_mappings || []).reduce((a, m) => a + m.count, 0);
      if (unresolved > 0) html += `<div class="notice" style="margin-top:8px">${unresolved} torrent${unresolved === 1 ? "'s" : "s'"} files couldn't be found from this container (e.g. <code>${(r.missing_paths || []).slice(0, 3).map(esc).join("</code>, <code>")}</code>). Those folders are probably not mounted into Hardlinkarr. They'll be skipped; this is fine if they aren't media.</div>`;
      out.innerHTML = html;
      if ($("#hs2", out)) $("#hs2", out).onclick = () => switchGuide({ server: readForm(root).mediaserver_type, locations: r.libraries,
        library_roots: [settingsCache?.movies_dir, settingsCache?.tv_dir].filter(Boolean) });
    } catch (e) { out.innerHTML = `<div class="notice err">${esc(e.message)}</div>`; }
  });
}

const NAMING_TEMPLATE_KEYS = ["movie_folder", "movie_file", "tv_folder", "season_folder", "episode_file", "include_quality"];
const NAMING_PRESETS = {
  standard: { label: "Standard (recommended)", desc: "Plex/Jellyfin/Emby friendly, with the resolution in file names.",
    movie_folder: "{title} ({year})", movie_file: "{title} ({year}){edition_tag}{quality_tag}", tv_folder: "{show} ({year})",
    season_folder: "Season {season:02d}", episode_file: "{show} - S{season:02d}{episode_tag} - {episode_title}{quality_tag}", include_quality: true },
  clean: { label: "Clean – no quality", desc: "Same layout, but without the resolution.",
    movie_folder: "{title} ({year})", movie_file: "{title} ({year}){edition_tag}", tv_folder: "{show} ({year})",
    season_folder: "Season {season:02d}", episode_file: "{show} - S{season:02d}{episode_tag} - {episode_title}", include_quality: false },
  short: { label: "Short episodes", desc: "Episode files without episode titles, e.g. “Show - S01E01.mkv”.",
    movie_folder: "{title} ({year})", movie_file: "{title} ({year})", tv_folder: "{show} ({year})",
    season_folder: "Season {season:02d}", episode_file: "{show} - S{season:02d}{episode_tag}", include_quality: false },
  kodi: { label: "Kodi style", desc: "Show folder without the year and “Season 1” folders.",
    movie_folder: "{title} ({year})", movie_file: "{title} ({year})", tv_folder: "{show}",
    season_folder: "Season {season}", episode_file: "{show} S{season:02d}{episode_tag}", include_quality: false },
  ids: { label: "With TMDB IDs in folders", desc: "Adds {tmdb-123} to folder names, the most reliable matching for Plex/Jellyfin.",
    movie_folder: "{title} ({year}) {{tmdb-{tmdb_id}}}", movie_file: "{title} ({year}){edition_tag}{quality_tag}", tv_folder: "{show} ({year}) {{tmdb-{tmdb_id}}}",
    season_folder: "Season {season:02d}", episode_file: "{show} - S{season:02d}{episode_tag} - {episode_title}{quality_tag}", include_quality: true },
};
const NAMING_TOKENS = [
  ["{title}", "Movie title"], ["{show}", "Show title"], ["{year}", "Release / first-air year"],
  ["{season}", "Season number (1)"], ["{season:02d}", "Season number padded (01)"], ["{episode_tag}", "E01, or E01-E02 for multi-episode files"],
  ["{episode_title}", "Episode title"], ["{resolution}", "e.g. 1080p"], ["{quality_tag}", "“ - 1080p” (when enabled)"],
  ["{edition}", "e.g. Director's Cut"], ["{edition_tag}", "Plex edition tag {edition-Director's Cut}"],
  ["{imdb_id}", "IMDb id (tt…)"], ["{tmdb_id}", "TMDB number"],
];

function bindNamingPreview(root) {
  const upd = async () => {
    const body = readForm(root);
    try { const r = await POST("/api/naming/preview", body); $("#preview", root).innerHTML = r.examples.map((e) => `<div class="path">${esc(e)}</div>`).join(""); } catch { /* ignore */ }
  };
  const presetSel = $("[data-k=naming_preset]", root);
  const desc = () => {
    const p = NAMING_PRESETS[presetSel.value];
    $("#preset-desc", root).textContent = p ? p.desc : "Your own templates. Edit them below.";
    if (presetSel.value === "custom") $("#naming-custom", root).open = true;
  };
  presetSel.addEventListener("change", () => {
    const p = NAMING_PRESETS[presetSel.value];
    if (p) NAMING_TEMPLATE_KEYS.forEach((k) => { const el = $(`[data-k=${k}]`, root); if (el.type === "checkbox") el.checked = p[k]; else el.value = p[k]; });
    desc(); upd();
  });
  // editing a template by hand switches the style to Custom
  NAMING_TEMPLATE_KEYS.forEach((k) => $(`[data-k=${k}]`, root).addEventListener("input", () => { presetSel.value = "custom"; desc(); }));
  let lastField = $("[data-k=movie_file]", root);
  $$("#naming-custom input:not([type=checkbox])", root).forEach((el) => el.addEventListener("focus", () => { lastField = el; }));
  $$("[data-token]", root).forEach((b) => b.onclick = () => {
    const el = lastField, t = b.dataset.token, pos = el.selectionStart ?? el.value.length;
    el.value = el.value.slice(0, pos) + t + el.value.slice(el.selectionEnd ?? pos);
    el.focus(); el.setSelectionRange(pos + t.length, pos + t.length);
    el.dispatchEvent(new Event("input"));
  });
  $$("[data-k^=movie_],[data-k^=tv_],[data-k=season_folder],[data-k=episode_file],[data-k$=_dir]", root).forEach((el) => el.addEventListener("input", upd));
  $("[data-k=include_quality]", root).addEventListener("change", upd);
  desc(); upd();
}

const SECTIONS = {
  folders: () => `<h2>Folders</h2><div class="notice info">Paths are as seen <b>inside this container</b>. For hardlinks to work, your downloads and media must live on the same disk and be mounted through <b>one</b> shared volume (e.g. host <code>/mnt/user/data</code> → <code>/data</code>).</div>
    ${F.folder("download_dirs", "Download folder(s)", "Where completed torrents are saved. Comma-separate several.")}
    <div class="form-grid">${F.folder("movies_dir", "Movies library")}${F.folder("tv_dir", "TV library")}</div>
    ${F.folder("anime_dir", "Anime library (optional)", "Leave empty to put anime with TV.")}
    <div class="row"><button type="button" data-test="paths">Test hardlinks</button></div><div id="t-paths" style="margin-top:10px"></div>`,
  metadata: () => `<h2>Metadata</h2>
    ${F.secret("tmdb_api_key", "TMDB API key", 'Free at <a href="https://www.themoviedb.org/settings/api" target="_blank">themoviedb.org/settings/api</a>. Either the v3 "API Key" or the v4 "Read Access Token" works.')}
    <div class="row"><button type="button" data-test="tmdb">Test key</button></div><div id="t-tmdb" style="margin:10px 0"></div>
    <div class="form-grid">${F.text("tmdb_language", "Language", "Titles are fetched in this language, e.g. en-US, de-DE.")}
    ${F.num("match_threshold", "Auto-link confidence", "0–1. Matches below this wait for your review. 0.8 is a good balance.", "0.05")}</div>`,
  client: () => `<h2>Torrent client</h2>
    <div class="form-grid">${F.select("client_type", "Client", [["none", "None (scan folders only)"], ["qbittorrent", "qBittorrent"], ["transmission", "Transmission"], ["deluge", "Deluge"]])}
    ${F.text("client_url", "URL", "e.g. http://qbittorrent:8080 (container name works when on the same Docker network)")}
    ${F.text("client_username", "Username", "Deluge doesn't use one.")}${F.secret("client_password", "Password")}</div>
    <div class="row"><button type="button" data-test="client">Test connection</button></div><div id="t-client" style="margin:10px 0"></div>
    <h3>Path mappings</h3><div class="hint muted small" style="margin-bottom:8px">Filled in automatically when you test the connection (and checked again on every scan). Only edit these if detection gets something wrong.</div><div id="mappings"></div>
    <h3>Categories</h3><div class="form-grid">${F.list("include_categories", "Only process these categories", "Empty = all torrents.", "movies, tv")}
    ${F.list("exclude_categories", "Never process these categories", "e.g. categories Radarr/Sonarr already import, or non-media.", "radarr, tv-sonarr, games")}</div>
    <label style="font-weight:550">Category → type hints</label><div class="hint muted small" style="margin-bottom:8px">Tell Hardlinkarr what's in each category for more accurate matching. Names containing "movie"/"tv"/"anime" are detected automatically.</div><div id="cattypes"></div>`,
  integrations: () => `<h2>Integrations <span class="muted small">(optional)</span></h2>
    <p class="muted small">Radarr/Sonarr are used as an extra identification source (their parser understands scene naming and your existing library). Hardlinkarr also notices files they already imported and won't duplicate them.</p>
    <div class="form-grid">${F.text("radarr_url", "Radarr URL", "", "http://radarr:7878")}${F.secret("radarr_api_key", "Radarr API key")}</div>
    <div class="row"><button type="button" data-test="radarr">Test Radarr</button></div><div id="t-radarr" style="margin:10px 0"></div>
    <div class="form-grid">${F.text("sonarr_url", "Sonarr URL", "", "http://sonarr:8989")}${F.secret("sonarr_api_key", "Sonarr API key")}</div>
    <div class="row"><button type="button" data-test="sonarr">Test Sonarr</button></div><div id="t-sonarr" style="margin:10px 0"></div>
    <h3>Media server</h3><div class="form-grid">${F.select("mediaserver_type", "Server", [["none", "None"], ["plex", "Plex"], ["jellyfin", "Jellyfin"], ["emby", "Emby"]], "Refreshes your libraries after new links are made.")}
    ${F.text("mediaserver_url", "URL", "", "http://plex:32400")}${F.secret("mediaserver_token", "Token / API key", "Plex: X-Plex-Token. Jellyfin/Emby: Dashboard → API Keys.")}</div>
    <div class="row"><button type="button" data-test="mediaserver">Test media server</button></div><div id="t-mediaserver" style="margin:10px 0"></div>`,
  naming: () => `<h2>Naming</h2>
    <p class="muted small">Pick a style, or choose <b>Custom</b> and build your own. The preview updates as you type.</p>
    ${F.select("naming_preset", "Naming style", Object.entries(NAMING_PRESETS).map(([k, p]) => [k, p.label]).concat([["custom", "Custom"]]))}
    <div id="preset-desc" class="hint muted small" style="margin:-8px 0 14px"></div>
    <h3>Preview</h3><div id="preview" class="card" style="background:var(--bg)"></div>
    <details id="naming-custom"><summary style="cursor:pointer;font-weight:550;margin-bottom:12px">Edit templates</summary>
      <div class="small muted" style="margin-bottom:8px">Click a token to insert it into the field you last clicked:</div>
      <div class="chips" id="tokens" style="margin-bottom:14px">${NAMING_TOKENS.map(([t, d]) => `<button type="button" class="chip" data-token="${esc(t)}" title="${esc(d)}">${esc(t)}</button>`).join("")}</div>
      <div class="form-grid">${F.text("movie_folder", "Movie folder")}${F.text("movie_file", "Movie file")}${F.text("tv_folder", "Show folder")}${F.text("season_folder", "Season folder")}</div>
      ${F.text("episode_file", "Episode file")}
      <div class="hint muted small" style="margin:-6px 0 12px">Empty tokens are dropped automatically, along with stray separators like “ - ” and “()”. To write a literal brace, use <code>{{</code> or <code>}}</code>.</div>
      ${F.bool("include_quality", "Fill in {quality_tag}", "Adds “ - 1080p” where the template has {quality_tag}. Plex/Jellyfin use it to group multiple versions of a movie.")}
    </details>
    ${F.bool("link_subtitles", "Link subtitles too", ".srt/.ass etc. next to the video or in a Subs folder")}
    <div class="notice info">Changing the style only affects new links. To rename files already in your library to the new style, open <a href="#/organize">Organize</a>.</div>`,
  automation: () => `<h2>Automation</h2>
    ${F.bool("auto_link", "Link automatically", "Confident matches are linked without asking.")}
    ${F.bool("dry_run", "Dry-run mode", "Identify and plan everything but don't create links. Great for a first look.")}
    ${F.bool("rename_existing_links", "Rename existing library links", "When a torrent is already hardlinked into the library under a different name (e.g. by another tool), rename that link to your naming format. To do your whole library at once, use the Organize page.")}
    ${F.bool("skip_arr_managed", "Protect Radarr/Sonarr folders", "Never rename files in folders Radarr/Sonarr manage (they would report them missing). Requires Radarr/Sonarr to be connected under Integrations.")}
    ${F.bool("upgrade_existing", "Upgrade to better releases", "If a higher-resolution copy of something already in the library arrives, swap the library link to it.")}
    <div class="form-grid">${F.num("scan_interval_minutes", "Scan every (minutes)")}${F.num("min_video_mb", "Ignore videos smaller than (MB)", "Skips samples and extras.")}</div>`,
  cleanup: () => `<h2>Cleanup</h2>${F.list("junk_extensions", "Junk file types", "A folder containing only these counts as empty.")}
    ${F.num("orphan_min_age_days", "Orphan grace period (days)", "Torrents younger than this are never reported as “not in library”.")}`,
  security: () => `<h2>Security</h2><p class="muted small">Optional login for the web UI (HTTP basic auth). Leave blank to disable – fine on a private LAN.</p>
    <div class="form-grid">${F.text("ui_username", "Username")}${F.secret("ui_password", "Password")}</div>`,
};

function bindSection(root) { bindBrowse(root); bindTests(root); if ($("#preview", root)) bindNamingPreview(root); }

/* ------------------------------------------------------------------ settings page */
routes.settings = async ([sec = "folders"]) => {
  const d = await GET("/api/settings");
  settingsCache = d.settings;
  const names = { folders: "Folders", metadata: "Metadata", client: "Torrent client", integrations: "Integrations", naming: "Naming", automation: "Automation", cleanup: "Cleanup", security: "Security" };
  $("#main").innerHTML = `<div class="page-head"><div><h1>Settings</h1></div><div class="row"><button id="wiz">Run setup wizard</button><button class="primary" id="save">Save</button></div></div>
    <div class="tabs" style="overflow-x:auto">${Object.entries(names).map(([k, l]) => `<button class="tab ${k === sec ? "on" : ""}" data-t="${k}">${l}</button>`).join("")}</div>
    <form class="card" id="sf" onsubmit="return false">${SECTIONS[sec]()}</form>`;
  const root = $("#sf");
  fillForm(root, d.settings); bindSection(root);
  $$(".tab").forEach((t) => t.onclick = () => { location.hash = "#/settings/" + t.dataset.t; });
  $("#wiz").onclick = () => { location.hash = "#/setup"; };
  $("#save").onclick = async () => { try { const r = await POST("/api/settings", readForm(root)); settingsCache = r.settings; toast("Settings saved"); } catch (e) { toast(e.message, true); } };
};

/* ------------------------------------------------------------------ setup wizard */
routes.setup = async () => {
  const d = await GET("/api/settings");
  let s = d.settings, step = 0, detected = {};
  settingsCache = s;
  const steps = [
    { title: "Welcome", body: () => `<h2>Welcome to Hardlinkarr 👋</h2>
      <p>Hardlinkarr watches your torrents, identifies each movie and episode with TMDB, and <b>hardlinks</b> it into your library with a clean, media-server-friendly name.</p>
      <ul class="muted"><li>Your torrents keep seeding the original files, untouched.</li><li>A hardlink uses <b>no extra disk space</b> – it's the same data under a second name.</li><li>Plex / Jellyfin / Emby only ever see tidy folders like <code>Movies/Dune (2021)/Dune (2021).mkv</code>.</li></ul>
      <p>This wizard takes about 2 minutes. You can change everything later in Settings.</p><div id="detect"></div>`,
      init: async (root) => { try { const h = await GET("/api/detect"); if (h.downloads || h.movies || h.tv) { $("#detect", root).innerHTML = `<div class="notice ok">Detected folders: ${[["downloads", "downloads"], ["movies", "movies"], ["tv", "TV"], ["anime", "anime"]].filter(([k]) => h[k]).map(([k, l]) => `${l} <code>${esc(h[k])}</code>`).join(", ")}. They'll be pre-filled on the next step.</div>`; if (!s.setup_complete) { if (h.downloads) detected.download_dirs = [h.downloads]; if (h.movies) detected.movies_dir = h.movies; if (h.tv) detected.tv_dir = h.tv; if (h.anime) detected.anime_dir = h.anime; Object.assign(s, detected); } } else if (!(h.mounts || []).length) { $("#detect", root).innerHTML = `<div class="notice">No media volume seems to be mounted. Check the <code>volumes:</code> section of your docker-compose file (e.g. <code>/mnt/user/data:/data</code>).</div>`; } } catch { /* ignore */ } } },
    { title: "Folders", body: SECTIONS.folders, tip: "Run the hardlink test before continuing – it catches the most common setup mistake." },
    { title: "TMDB", body: SECTIONS.metadata },
    { title: "Torrent client", body: SECTIONS.client },
    { title: "Integrations", body: SECTIONS.integrations },
    { title: "Naming", body: SECTIONS.naming },
    { title: "Finish", body: () => `<h2>Ready to go</h2>
      <p>How should the first scan of your existing downloads behave?</p>
      <div class="field inline"><input type="radio" name="mode" id="m-dry" value="dry" checked style="width:16px"><div><label for="m-dry"><b>Preview first (recommended)</b></label><div class="hint" style="margin:0">Dry-run: identify everything and show what would be linked. Turn dry-run off in Settings → Automation when happy.</div></div></div>
      <div class="field inline"><input type="radio" name="mode" id="m-live" value="live" style="width:16px"><div><label for="m-live"><b>Link right away</b></label><div class="hint" style="margin:0">Confident matches are linked immediately; uncertain ones wait for you in Library → Needs review.</div></div></div>
      <div class="field inline"><input type="radio" name="mode" id="m-manual" value="manual" style="width:16px"><div><label for="m-manual"><b>Ask me for everything</b></label><div class="hint" style="margin:0">Nothing is linked until you approve it.</div></div></div>
      ${F.num("scan_interval_minutes", "Check for new torrents every (minutes)")}
      <div class="notice info">Afterwards: point Plex/Jellyfin/Emby at your <b>library</b> folders (not the torrent folder). The dashboard warns you if a library still scans the torrent folder and shows how to switch. To rename what's already in your library, use <b>Organize</b>.</div>` },
  ];
  const render = () => {
    const st = steps[step];
    $("#main").innerHTML = `<div class="wizard"><h1>Setup</h1><div class="muted">Step ${step + 1} of ${steps.length} · ${st.title}</div>
      <div class="steps">${steps.map((_, n) => `<div class="${n <= step ? "done" : ""}"></div>`).join("")}</div>
      <form class="card" id="wf" onsubmit="return false">${st.body()}${st.tip ? `<div class="notice info" style="margin-top:14px">${st.tip}</div>` : ""}</form>
      <div class="wizard-nav"><button id="back" ${step ? "" : "style=visibility:hidden"}>← Back</button>
      <div class="row">${s.setup_complete ? '<button class="ghost" id="exit">Exit wizard</button>' : ""}<button class="primary" id="next">${step === steps.length - 1 ? "Finish & start scanning" : "Next →"}</button></div></div></div>`;
    const root = $("#wf");
    fillForm(root, s); bindSection(root);
    if (st.init) st.init(root);
    $("#back").onclick = async () => { await keep(root); step--; render(); };
    if ($("#exit")) $("#exit").onclick = () => { location.hash = "#/dashboard"; };
    $("#next").onclick = async () => {
      const vals = readForm(root);
      if (st.title === "TMDB" && !vals.tmdb_api_key) { toast("A TMDB API key is required for identification", true); return; }
      if (step < steps.length - 1) { await keep(root); step++; render(); return; }
      const mode = ($("input[name=mode]:checked", root) || {}).value;
      Object.assign(vals, { setup_complete: true, dry_run: mode === "dry", auto_link: mode !== "manual" });
      await POST("/api/settings", vals);
      await POST("/api/scan");
      toast("Setup complete – first scan started");
      location.hash = "#/dashboard";
    };
  };
  // detected folders are saved with the next step change so they survive the settings reload
  const keep = async (root) => { const vals = { ...detected, ...readForm(root) }; detected = {}; const r = await POST("/api/settings", vals); s = settingsCache = r.settings; };
  render();
};

/* ------------------------------------------------------------------ boot */
(async () => {
  $$("nav a").forEach((a) => { const i = a.querySelector("i"); const rest = [...a.childNodes].filter((n) => n !== i && n.nodeType === 3); rest.forEach((n) => { const sp = document.createElement("span"); sp.textContent = n.textContent; a.replaceChild(sp, n); }); });
  const s = await refreshShell();
  if (s && !s.setup_complete && !location.hash.startsWith("#/setup")) location.hash = "#/setup";
  navigate();
  setInterval(() => { if (current !== "dashboard") refreshShell(); }, 10000);
})();
