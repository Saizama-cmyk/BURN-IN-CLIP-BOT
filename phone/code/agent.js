/**
 * The coding agent: what it is told about the project, how it proposes changes, and a line diff
 * so every change can be looked at before it touches a file.
 *
 * Small phone models are unreliable at patch formats, so the agent always writes a changed file
 * out in full, inside a fenced block labelled with its path. The app turns each block into a
 * proposed change with a diff; nothing is written until you apply it.
 */
import { langOf } from "./store";

const ACTIVE_MAX = 12000;      // characters of the open file sent with a request
const OTHER_MAX = 2500;        // characters of each other small file
const OTHERS_TOTAL = 9000;     // characters of other files altogether
const DIFF_CELLS = 250000;     // beyond this many line pairs the diff is summarised, not computed

export const AGENT_RULES = "You are a coding agent working inside a small project on the person's phone. "
  + "You can read the files shown below. To create or change a file, write the COMPLETE new file in one block, "
  + "exactly like this:\n```file:path/to/name.ext\n...the whole file...\n```\n"
  + "To delete a file, write:\n```delete:path/to/name.ext\n```\n"
  + "Only touch files the request needs. Before the blocks, say in one or two sentences what you are changing and why. "
  + "Never leave placeholders like '...rest unchanged'. If you only need to explain, answer without any blocks.";

export const ASK_RULES = "You are a coding assistant looking at a project on the person's phone. "
  + "Explain clearly and concretely, with short code examples in fenced blocks when useful. "
  + "Do not rewrite whole files unless asked.";

/** The system prompt: the rules, the file list, the open file, and small neighbours for context. */
export function buildContext(rules, projectName, files, active, texts) {
  const list = files.filter(f => !f.dir).map(f => `- ${f.path}${f.path === active ? "  (open)" : ""}`).join("\n");
  const parts = [rules, `Project: ${projectName}\nFiles:\n${list || "(no files yet)"}`];
  if (active && texts[active] !== undefined) {
    const body = texts[active];
    parts.push(`The open file, ${active}${body.length > ACTIVE_MAX ? " (first part)" : ""}:\n`
      + "```" + langOf(active) + "\n" + body.slice(0, ACTIVE_MAX) + "\n```");
  }
  let budget = OTHERS_TOTAL;
  for (const [path, body] of Object.entries(texts)) {
    if (path === active || body.length > OTHER_MAX || body.length > budget) continue;
    budget -= body.length;
    parts.push(`${path}:\n` + "```" + langOf(path) + "\n" + body + "\n```");
  }
  return parts.join("\n\n");
}

/** Pull proposed changes out of a reply: [{path, content|null, deleted}] and the prose around them. */
export function parseEdits(text) {
  const edits = [];
  const src = String(text || "");
  const re = /```(file|delete):([^\n`]+)\n?([\s\S]*?)```/g;
  let m;
  while ((m = re.exec(src))) {
    const path = m[2].trim().replace(/^\/+/, "");
    if (!path) continue;
    const deleted = m[1] === "delete";
    const existing = edits.findIndex(e => e.path === path);
    const edit = { path, deleted, content: deleted ? null : m[3].replace(/\n$/, "") + "\n" };
    if (existing >= 0) edits[existing] = edit; else edits.push(edit);
  }
  const prose = src.replace(re, "").replace(/\n{3,}/g, "\n\n").trim();
  return { edits, prose };
}

/**
 * Line diff: [{t: " " | "+" | "-", s: line}]. A longest-common-subsequence table, which is
 * exact and plenty fast for files a person edits on a phone; very large files get a summary.
 */
export function lineDiff(before, after) {
  const a = String(before ?? "").replace(/\n$/, "").split("\n");
  const b = String(after ?? "").replace(/\n$/, "").split("\n");
  if (before == null || before === "") return b.map(s => ({ t: "+", s }));
  if (after == null) return a.map(s => ({ t: "-", s }));
  if (a.length * b.length > DIFF_CELLS) {
    return [{ t: "-", s: `(${a.length} lines)` }, { t: "+", s: `(${b.length} lines, too large to compare line by line)` }];
  }
  const n = a.length, m = b.length;
  const L = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    }
  }
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push({ t: " ", s: a[i] }); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) { out.push({ t: "-", s: a[i] }); i++; }
    else { out.push({ t: "+", s: b[j] }); j++; }
  }
  while (i < n) out.push({ t: "-", s: a[i++] });
  while (j < m) out.push({ t: "+", s: b[j++] });
  return out;
}

export const countDiff = (d) => ({ add: d.filter(x => x.t === "+").length, del: d.filter(x => x.t === "-").length });

/** Only the changed lines and a little context around them, for a compact view. */
export function hunks(d, context = 2) {
  const keep = new Set();
  d.forEach((x, i) => { if (x.t !== " ") for (let k = i - context; k <= i + context; k++) keep.add(k); });
  const out = [];
  let gap = false;
  d.forEach((x, i) => {
    if (keep.has(i)) { out.push(x); gap = false; }
    else if (!gap) { out.push({ t: "…", s: "" }); gap = true; }
  });
  return out;
}
