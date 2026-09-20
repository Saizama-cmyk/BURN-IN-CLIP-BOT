"""One-time source migration for the BURN-IN editing desk."""
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
p = root / 'clipbot/dashboard/static/index.html'
s = p.read_text(encoding='utf-8')
backup = root / 'build/revamp-before'
backup.mkdir(parents=True, exist_ok=True)
for name in ('index.html', 'pet.html'):
    dest = backup / name
    if not dest.exists():
        dest.write_bytes((p.parent / name).read_bytes())
s = re.sub(r'<link[^>]+https://fonts\.[^>]+>\n?', '', s)
s = s.replace('</style>', '</style>\n<link rel="stylesheet" href="/static/workspace.css">', 1)
s = s.replace('<body>', '<body>\n<a class="skip-link" href="#workspace">Skip to workspace</a>', 1)
s = s.replace('<h1>Clipping <b>Companion</b></h1><p>by BURN-IN</p>', '<h1>BURN-IN</h1><p>Find the moment. Make the cut.</p>')
s = s.replace('<b>Clipping<i> </i>Companion</b><span>by BURN-IN</span>', '<b>BURN-IN</b><span>Your local clip studio</span>')
s = s.replace('Sign in · by BURN-IN', 'Your clips. Your PC. Your workspace.')
s = s.replace('SPIKE OS', 'BURN-IN')
s = s.replace('<main>', '<main id="workspace" tabindex="-1">', 1)
s = s.replace('<form id="lockForm" autocomplete="off">', '<form id="lockForm">')
s = s.replace('id="lockName" placeholder="Profile name"', 'id="lockName" name="username" autocomplete="username" aria-label="Profile name" placeholder="Profile name"')
s = s.replace('id="lockPass" placeholder="Password"', 'id="lockPass" name="password" autocomplete="current-password" aria-label="Password" placeholder="Password"')
s = s.replace('id="lockPass2" placeholder="Repeat password"', 'id="lockPass2" name="confirm-password" autocomplete="new-password" aria-label="Repeat password" placeholder="Repeat password"')
old = '<div class="topbar"><div class="grow"><h1 id="pageTitle">Live</h1><p id="pageSub">What BURN-IN is watching right now, and every moment it caught.</p></div></div>'
new = '<div class="topbar"><div class="grow"><h1 id="pageTitle">Live desk</h1><p id="pageSub">Watch the streams. Catch the moments worth keeping.</p></div><div class="top-tools"><span class="local-label">LOCAL WORKSPACE</span><button class="btn ghost" type="button" id="petSettings">Desktop pet</button><button class="btn" type="button" onclick="show(\'clips\')">Open clips</button></div></div>'
assert old in s
s = s.replace(old, new)
s = s.replace('<h2>Monitors</h2>', '<h2>On the radar</h2>')
s = s.replace('<div id="monitors" class="monitors"></div>', '<div class="monitor-tools"><input id="monitorSearch" type="search" placeholder="Find a stream…" aria-label="Find a stream"><button class="btn ghost" id="monitorMore" type="button" hidden>Show all streams</button></div><div id="monitors" class="monitors" aria-live="off"></div>')
s = s.replace('<h2>Pipeline</h2>', '<h2>From stream to clip</h2>')
s = s.replace('<div class="side">\n        <div class="chip rej">', '<details class="diagnostics"><summary>Pipeline details &amp; diagnostics</summary><div class="side">\n        <div class="chip rej">', 1)
s = s.replace('<div class="sysline" id="sysline"></div>', '<div class="sysline" id="sysline"></div></details>', 1)
s = s.replace('<h2>Samples</h2><span class="sub">Click any row to watch it</span>', '<h2>Recent moments</h2><span class="sub">Select a moment to review the cut</span>')
s = s.replace('<div class="panel">\n        <div class="phead"><h2>Posted</h2>', '<details class="panel history-panel"><summary>Posting activity</summary>\n        <div class="phead"><h2>Posted</h2>', 1)
s = s.replace('<tbody id="posts"></tbody></table></div>\n      </div>', '<tbody id="posts"></tbody></table></div>\n      </details>', 1)
# Navigation order matches the editing workflow, then management.
nav_start = s.index('  <nav role="tablist"')
nav_end = s.index('  </nav>', nav_start)
nav = s[nav_start:nav_end]
buttons = {m.group(1):m.group(0) for m in re.finditer(r'    <button[^\n]+data-view="([^"]+)"[^\n]+</button>', nav)}
assert len(buttons) == 7
s = s[:nav_start] + nav.splitlines()[0] + '\n' + '\n'.join(buttons[k] for k in ('live','clips','studio','growth','system','settings','setup')) + '\n' + s[nav_end:]
s = s.replace('live: ["Live", "What BURN-IN is watching right now, and every moment it caught."]', 'live: ["Live desk", "Watch the streams. Catch the moments worth keeping."]')
s = s.replace('settings: ["Settings", "Every knob in one place. Changes apply live unless marked restart."]', 'settings: ["Settings", "Start with a section or search for what you need."]')
s = s.replace('  const pg = PAGES[view] || PAGES.live;', '  view = PAGES[view] ? view : "live";\n  const pg = PAGES[view];')
s = s.replace('function openPreview(id) {\n  const c = (state.samples || [])', 'function openPreview(id) {\n  const c = (state?.samples || [])')
s = s.replace('if (!(state.samples || []).some', 'if (!state) state = {samples: []};\n  if (!(state.samples || []).some')
s = s.replace('  if (matchMedia("(prefers-reduced-motion: reduce)").matches) { v.removeAttribute("autoplay"); v.pause(); }', '  if (matchMedia("(prefers-reduced-motion: reduce)").matches) { v.removeAttribute("autoplay"); v.pause(); hide(); return; }')
s = s.replace('setTimeout(hide, 5600)', 'setTimeout(hide, 1800)')
s = s.replace('  const setup = authInfo.setup;\n', '  const setup = authInfo.setup;\n  $("#lockPass").autocomplete = setup ? "new-password" : "current-password";\n', 1)
s = s.replace('  try { await unlock(); } finally', '  try { await unlock(); } catch (err) { $("#lockErr").textContent = "The engine is not responding. Try again in a moment."; } finally')
# Monitor rendering is limited to the visible board; retain filter/view across refreshes.
s = s.replace('function renderMonitors(s) {', 'let allMonitors = false;\nfunction visibleMonitors(s) {\n  const q = $("#monitorSearch").value.trim().toLowerCase();\n  const list = s.monitors.filter(m => `${m.display_name} ${m.category || ""}`.toLowerCase().includes(q));\n  const more = $("#monitorMore"); more.hidden = list.length <= 6;\n  more.textContent = allMonitors ? "Show fewer" : `Show all ${list.length} streams`;\n  return allMonitors ? list : list.slice(0, 6);\n}\nfunction renderMonitors(s) {')
s = s.replace('  el.innerHTML = s.monitors.map(m => {', '  const visible = visibleMonitors(s);\n  if (!visible.length) { el.innerHTML = `<div class="empty"><strong>No matching streams</strong>Try a different name or category.</div>`; return; }\n  el.innerHTML = visible.map(m => {')
s = s.replace('    const m = s.monitors[i]; if (!m', '    const m = s.monitors.find(m => m.key === el.dataset.watch); if (!m')
s = s.replace('  if (!s.monitors.length) {\n    el.style', '  if (!s.monitors.length) {\n    $("#monitorMore").hidden = true;\n    el.style')
s = s.replace('<strong>No streams on the board yet</strong>', '<img src="/static/art/mark.png" alt=""><strong>Your next great clip starts here</strong>')
s = s.replace('  if ($("#lock").classList.contains("show")) { pollTimer', '  if (document.hidden || $("#lock").classList.contains("show")) { pollTimer', 1)
s = s.replace('renderHeader(state); renderMonitors(state); renderFlow(state); renderSamples(state); renderPosts(state);', 'renderHeader(state);\n    if ($("#view-live").classList.contains("active")) { renderMonitors(state); renderFlow(state); renderSamples(state); renderPosts(state); }')
s = s.replace('  if (view === "settings") ensureSettings();', '  if (view === "live" && state) { renderMonitors(state); renderFlow(state); renderSamples(state); renderPosts(state); }\n  if (view === "settings") ensureSettings();', 1)
s = s.replace('/* ---------------- boot ---------------- */', '''$("#petSettings").addEventListener("click", () => focusField("pets.enabled"));
$("#monitorSearch").addEventListener("input", () => { if (state) renderMonitors(state); });
$("#monitorMore").addEventListener("click", () => { allMonitors = !allMonitors; if (state) renderMonitors(state); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
$$("nav button").forEach((b, i, tabs) => {
  b.id = "tab-" + b.dataset.view; b.setAttribute("aria-controls", "view-" + b.dataset.view);
  $("#view-" + b.dataset.view).setAttribute("aria-labelledby", b.id);
  b.addEventListener("keydown", e => {
    const delta = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0;
    if (delta) { e.preventDefault(); const next = tabs[(i + delta + tabs.length) % tabs.length]; next.focus(); next.click(); }
  });
});
/* ---------------- boot ---------------- */''')
p.write_text(s, encoding='utf-8')
print('Dashboard migration complete.')
