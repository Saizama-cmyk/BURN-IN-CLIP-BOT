/**
 * Talking to the PC: turning what was typed into an address, one call, and the connection test.
 *
 * The phone signs in once with the profile password and keeps the session token in the
 * keychain. The PC treats it as a device sign-in: it lasts weeks and is not dropped by the
 * desktop's idle auto-lock, because this app has its own Face ID / fingerprint lock.
 */
export const DEFAULT_PORT = 8787;
export const KEY_HOST = "ashvane.host";
export const KEY_TOKEN = "ashvane.token";
export const TIMEOUT_MS = 6000;
const TEST_TIMEOUT_MS = 5000;

/**
 * Turns whatever was typed into a base URL.
 *   192.168.0.72          -> http://192.168.0.72:8787      (home address: plain http is fine)
 *   msi.tailnet.ts.net    -> https://msi.tailnet.ts.net    (Tailscale serves it with a real cert)
 *   https://host:9000     -> kept as written
 */
export function baseUrl(raw) {
  const text = String(raw || "").trim().replace(/\/+$/, "");
  if (!text) return "";
  const explicit = /^https?:\/\//i.test(text);
  const bare = text.replace(/^https?:\/\//i, "").replace(/\/.*$/, "");
  if (!bare) return "";
  if (explicit) return text.replace(/^(https?:\/\/[^/]+).*$/i, "$1");
  if (/\.ts\.net$/i.test(bare)) return `https://${bare}`;
  return `http://${bare.includes(":") ? bare : `${bare}:${DEFAULT_PORT}`}`;
}

export async function call(host, path, { token, method = "GET", body, timeout = TIMEOUT_MS } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    const res = await fetch(`${host}${path}`, {
      method,
      signal: ctrl.signal,
      headers: {
        "X-ClipBot": "1",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
    return { ok: res.ok, status: res.status, data };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Test the connection step by step and say, in plain words, what is wrong and what to do.
 * Returns { ok, steps: [{ label, ok, detail }], advice, profiles }.
 */
export async function testConnection(raw, token) {
  const host = baseUrl(raw);
  const steps = [];
  if (!host) {
    return { ok: false, steps, advice: "Type the address the PC shows under Settings → Dashboard → Test connection." };
  }
  const where = host.replace(/^https?:\/\//, "");
  const started = Date.now();
  let status;
  try {
    status = await call(host, "/api/auth/status", { timeout: TEST_TIMEOUT_MS });
  } catch (e) {
    steps.push({ label: `Reach ${where}`, ok: false, detail: e.name === "AbortError" ? "no answer in 5 seconds" : "no answer" });
    const tail = /\.ts\.net$/i.test(where) || /^100\./.test(where)
      ? "Open Tailscale on this phone and make sure it is connected (the same account as the PC)."
      : "Put this phone on the same Wi-Fi as the PC, or use the Tailscale name the PC shows.";
    return { ok: false, steps, advice: `The PC did not answer. Check Ashvane is open on the PC. ${tail}` };
  }
  const ms = Date.now() - started;
  if (status.status === 403) {
    steps.push({ label: `Reach ${where}`, ok: false, detail: "the PC refused this address" });
    return { ok: false, steps, advice: "On the PC, turn on Settings → Dashboard → Phone remote, press Save and let Ashvane restart." };
  }
  if (!status.ok) {
    steps.push({ label: `Reach ${where}`, ok: false, detail: `answered ${status.status}` });
    return { ok: false, steps, advice: "Something else answered at that address. Check you typed the one the PC shows." };
  }
  steps.push({ label: `Reach ${where}`, ok: true, detail: `answered in ${ms} ms` });
  const profiles = status.data.profiles || [];
  steps.push({ label: "Ashvane is running", ok: true, detail: `${profiles.length} profile${profiles.length === 1 ? "" : "s"}` });
  if (token) {
    try {
      const me = await call(host, "/api/state", { token, timeout: TEST_TIMEOUT_MS });
      steps.push({ label: "Signed in", ok: me.ok, detail: me.ok ? "your session is valid" : "the session ended" });
      if (!me.ok) return { ok: false, steps, profiles, advice: "Sign in again with your profile password." };
    } catch (e) {
      steps.push({ label: "Signed in", ok: false, detail: "no answer" });
      return { ok: false, steps, profiles, advice: "The PC stopped answering halfway. Try again." };
    }
  }
  return { ok: true, steps, profiles, advice: token ? "Everything works." : "The PC is reachable. Sign in with your profile password." };
}
