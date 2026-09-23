/**
 * The web preview's stand-in for fs.js: the same calls over browser storage, so the Code
 * workspace can be tried in a browser. Folders are implied by file paths.
 */
const KEY = "ashvane.code.files";

const load = () => {
  try { return JSON.parse(globalThis.localStorage?.getItem(KEY) || "{}"); } catch { return {}; }
};
const save = (all) => { try { globalThis.localStorage?.setItem(KEY, JSON.stringify(all)); } catch {} };
const DIR = "\u0000dir";

export async function ensureRoot() {}

export async function listDir(rel) {
  const all = load(), prefix = rel ? `${rel}/` : "";
  const names = new Set();
  for (const path of Object.keys(all)) {
    if (!path.startsWith(prefix)) continue;
    const head = path.slice(prefix.length).split("/")[0];
    if (head) names.add(head);
  }
  return [...names];
}

export async function isDir(rel) {
  const all = load();
  return all[rel] === DIR || Object.keys(all).some(p => p.startsWith(`${rel}/`));
}

export async function stat(rel) {
  const all = load();
  const dir = await isDir(rel);
  const exists = dir || rel in all;
  return { exists, dir, size: exists && !dir ? all[rel].length : 0, modified: Date.now() };
}

export async function read(rel) {
  const all = load();
  if (!(rel in all) || all[rel] === DIR) throw new Error(`no such file: ${rel}`);
  return all[rel];
}

export async function write(rel, text) {
  const all = load();
  all[rel] = text;
  save(all);
}

export async function mkdir(rel) {
  const all = load();
  if (!Object.keys(all).some(p => p.startsWith(`${rel}/`))) all[`${rel}/.keep`] = "";
  save(all);
}

export async function remove(rel) {
  const all = load();
  for (const p of Object.keys(all)) if (p === rel || p.startsWith(`${rel}/`)) delete all[p];
  save(all);
}

export async function move(from, to) {
  const all = load();
  for (const p of Object.keys(all)) {
    if (p === from || p.startsWith(`${from}/`)) { all[to + p.slice(from.length)] = all[p]; delete all[p]; }
  }
  save(all);
}
