/**
 * Project files on the phone: plain files under the app's documents folder, one folder per
 * project. (fs.web.js is the same API over browser storage, for the web preview.)
 */
import * as FileSystem from "expo-file-system";

const ROOT = `${FileSystem.documentDirectory}code/`;

const abs = (rel) => ROOT + rel.split("/").map(encodeURIComponent).join("/");

export async function ensureRoot() {
  await FileSystem.makeDirectoryAsync(ROOT, { intermediates: true }).catch(() => {});
}

export async function listDir(rel) {
  try { return (await FileSystem.readDirectoryAsync(abs(rel))).map(decodeURIComponent); }
  catch { return []; }
}

export async function isDir(rel) {
  const info = await FileSystem.getInfoAsync(abs(rel));
  return info.exists && info.isDirectory;
}

export async function stat(rel) {
  const info = await FileSystem.getInfoAsync(abs(rel));
  return { exists: info.exists, dir: !!info.isDirectory, size: info.size || 0,
           modified: (info.modificationTime || 0) * 1000 };
}

export async function read(rel) {
  return FileSystem.readAsStringAsync(abs(rel));
}

export async function write(rel, text) {
  const parent = rel.split("/").slice(0, -1).join("/");
  if (parent) await FileSystem.makeDirectoryAsync(abs(parent), { intermediates: true }).catch(() => {});
  await FileSystem.writeAsStringAsync(abs(rel), text);
}

export async function mkdir(rel) {
  await FileSystem.makeDirectoryAsync(abs(rel), { intermediates: true }).catch(() => {});
}

export async function remove(rel) {
  await FileSystem.deleteAsync(abs(rel), { idempotent: true });
}

export async function move(from, to) {
  const parent = to.split("/").slice(0, -1).join("/");
  if (parent) await mkdir(parent);
  await FileSystem.moveAsync({ from: abs(from), to: abs(to) });
}
