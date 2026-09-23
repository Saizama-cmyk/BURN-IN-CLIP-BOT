/** The web preview has no keychain; browser storage stands in for it there only. */
export const get = async (key) => { try { return globalThis.localStorage?.getItem(key) ?? null; } catch { return null; } };
export const set = async (key, value) => { try { globalThis.localStorage?.setItem(key, value); } catch {} };
export const del = async (key) => { try { globalThis.localStorage?.removeItem(key); } catch {} };
