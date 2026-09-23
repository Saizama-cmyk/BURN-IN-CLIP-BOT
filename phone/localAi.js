/**
 * The assistant that runs on the phone itself.
 *
 * No model is chosen by hand. The app reads what the device actually has - total memory, the
 * platform's per-app memory ceiling, and free storage - then takes the largest model from the
 * ladder below that fits with room to spare. A 12 GB iPhone lands on the 4B; a 4 GB Android
 * lands on the 1B; anything smaller is told honestly that it cannot host one, and the PC's
 * model stays one tap away.
 *
 * Weights are GGUF and run through llama.rn (Metal on iOS, CPU/OpenCL on Android). They are
 * downloaded once, resumable, and kept in the app's own documents folder.
 */
import { Platform } from "react-native";
import * as Device from "expo-device";
import * as FileSystem from "expo-file-system";
import { initLlama } from "llama.rn";

const GB = 1024 ** 3;

/**
 * The ladder, best first. `needsGb` is what the model wants *while running*: the weights plus
 * the KV cache plus room for the rest of the app, not just the download size.
 */
export const LADDER = [
  {
    id: "qwen3-4b",
    name: "Qwen3 4B Instruct",
    blurb: "The strongest model a phone can host comfortably.",
    url: "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
    bytes: 2497281120,
    needsGb: 4.2,
    ctx: 4096,
  },
  {
    id: "llama32-3b",
    name: "Llama 3.2 3B Instruct",
    blurb: "The most-tested 3B: steady, good at following instructions.",
    url: "https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
    bytes: 2019377696,
    needsGb: 3.4,
    ctx: 4096,
  },
  {
    id: "qwen3-1.7b",
    name: "Qwen3 1.7B",
    blurb: "Quick, and the best of the small ones at other languages.",
    url: "https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf",
    bytes: 1107409472,
    needsGb: 2.0,
    ctx: 4096,
  },
  {
    id: "gemma3-1b",
    name: "Gemma 3 1B",
    blurb: "The lightweight pick for older phones.",
    url: "https://huggingface.co/unsloth/gemma-3-1b-it-GGUF/resolve/main/gemma-3-1b-it-Q4_K_M.gguf",
    bytes: 806058272,
    needsGb: 1.4,
    ctx: 2048,
  },
];

/**
 * How much memory this app may actually use.
 *
 * iOS kills an app that goes much past roughly half the device's RAM, and Android's per-app
 * heap is stricter still, so the raw total would be a lie. These fractions are deliberately
 * conservative: being handed a model that gets the app killed mid-sentence is worse than being
 * handed a smaller one that works.
 */
const SHARE = Platform.OS === "ios" ? 0.45 : 0.35;

export function deviceProfile() {
  const totalGb = (Device.totalMemory || 0) / GB;
  return {
    name: Device.modelName || Device.deviceName || "this phone",
    brand: Device.brand || Platform.OS,
    totalGb,
    usableGb: totalGb * SHARE,
    os: `${Device.osName || Platform.OS} ${Device.osVersion || ""}`.trim(),
  };
}

/**
 * The best model this device can host, or null when it cannot host one.
 *
 * A model that is already on the phone is never passed over for lack of disk space - it is
 * already using that space. (Checking free space against a model that is already downloaded is
 * exactly what used to make the app ask for a download it had just finished.)
 */
export function pickModel(profile, freeBytes = Infinity, downloaded = new Set()) {
  return LADDER.find(m => m.needsGb <= profile.usableGb
    && (downloaded.has(m.id) || m.bytes * 1.05 < freeBytes)) || null;
}

export function modelPath(model) {
  return `${FileSystem.documentDirectory}models/${model.id}.gguf`;
}
const resumePath = (model) => `${FileSystem.documentDirectory}models/${model.id}.resume.json`;
const DIR = () => `${FileSystem.documentDirectory}models`;
const SAVE_EVERY_MS = 5000;    // how often a download's resume point is written down

export async function isDownloaded(model) {
  const info = await FileSystem.getInfoAsync(modelPath(model));
  // a half-finished download is worse than none: it loads as garbage, so treat it as missing
  return info.exists && info.size >= model.bytes * 0.99 && !(await FileSystem.getInfoAsync(resumePath(model))).exists;
}

/** Ids of every model already fully on the phone. */
export async function downloadedIds() {
  const out = new Set();
  for (const m of LADDER) if (await isDownloaded(m)) out.add(m.id);
  return out;
}

/** How far an interrupted download got (0..1), or 0 if none is waiting. */
export async function pendingProgress(model) {
  try {
    const saved = JSON.parse(await FileSystem.readAsStringAsync(resumePath(model)));
    return Math.min(1, (saved.written || 0) / model.bytes);
  } catch { return 0; }
}

/**
 * Download the weights, reporting 0..1 progress. `source` is {url, headers} - the PC's copy over
 * home Wi-Fi when it has one, otherwise the internet. The transfer runs as a background session,
 * so it keeps going while the screen is locked, and its resume point is saved every few seconds
 * so a closed app carries on from where it stopped instead of starting again.
 */
export async function download(model, onProgress, source = null) {
  await FileSystem.makeDirectoryAsync(DIR(), { intermediates: true }).catch(() => {});
  let saved = null;
  try { saved = JSON.parse(await FileSystem.readAsStringAsync(resumePath(model))); } catch { /* fresh */ }
  const url = source?.url || model.url;
  const options = { headers: source?.headers || {}, sessionType: FileSystem.FileSystemSessionType.BACKGROUND };
  let written = saved?.written || 0, lastSave = 0;
  const task = FileSystem.createDownloadResumable(
    url, modelPath(model), options,
    ({ totalBytesWritten, totalBytesExpectedToWrite }) => {
      written = totalBytesWritten;
      const total = totalBytesExpectedToWrite > 0 ? totalBytesExpectedToWrite : model.bytes;
      onProgress(Math.min(1, totalBytesWritten / total));
      const now = Date.now();
      if (now - lastSave > SAVE_EVERY_MS) {
        lastSave = now;
        const state = task.savable();
        FileSystem.writeAsStringAsync(resumePath(model),
          JSON.stringify({ ...state, written, source: url })).catch(() => {});
      }
    },
    saved && saved.source === url ? saved.resumeData : undefined,
  );
  const res = saved && saved.source === url && saved.resumeData
    ? await task.resumeAsync() : await task.downloadAsync();
  if (!res?.uri) throw new Error("the download did not finish");
  const info = await FileSystem.getInfoAsync(modelPath(model));
  if (!info.exists || info.size < model.bytes * 0.99) {
    throw new Error(`only ${Math.round((info.size || 0) / 1e6)} MB of ${Math.round(model.bytes / 1e6)} MB arrived`);
  }
  await FileSystem.deleteAsync(resumePath(model), { idempotent: true });
  return res.uri;
}

export async function removeModel(model) {
  await FileSystem.deleteAsync(modelPath(model), { idempotent: true });
  await FileSystem.deleteAsync(resumePath(model), { idempotent: true });
}

export async function freeBytes() {
  try {
    return await FileSystem.getFreeDiskStorageAsync();
  } catch (e) {
    return Infinity;
  }
}

/** Load the weights into memory. Keep the returned context and reuse it for every turn. */
export async function load(model) {
  return initLlama({
    model: modelPath(model).replace("file://", ""),
    n_ctx: model.ctx,
    n_gpu_layers: Platform.OS === "ios" ? 99 : 0,   // Metal on iOS; Android stays on the CPU
    use_mlock: false,
  });
}

export const SYSTEM = "You are a straight-talking assistant running entirely on this phone. Be "
  + "concise and concrete. Say when you do not know something.";
const ANSWER_TOKENS = 768;       // a normal answer
const CODE_TOKENS = 1536;        // code needs room

/**
 * One turn. `history` is [{role, content}, ...]; `system` carries the person's instructions,
 * rules and any skill in use; `onToken` gets the answer as it is written. Returns the whole reply.
 */
export async function reply(context, history, onToken, system = SYSTEM, long = false) {
  const out = await context.completion({
    messages: [{ role: "system", content: system }, ...history],
    n_predict: long ? CODE_TOKENS : ANSWER_TOKENS,
    temperature: 0.7,
    stop: ["<|im_end|>", "<|eot_id|>", "<end_of_turn>"],
  }, (data) => {
    if (onToken && data?.token) onToken(data.token);
  });
  return (out?.text || "").trim();
}
