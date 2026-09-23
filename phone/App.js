/**
 * BURN-IN Remote - the clip desk, on a phone.
 *
 * Talks to the BURN-IN app running on your PC over your own Wi-Fi: it asks for the session
 * token once (your profile password), keeps it in the iOS keychain, and sends it as a bearer
 * header on every call. Nothing is stored anywhere else and nothing leaves your network.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator, Image, KeyboardAvoidingView, Linking, Platform, Pressable,
  RefreshControl, ScrollView, StatusBar, StyleSheet, Switch, Text, TextInput, View,
} from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import * as SecureStore from "expo-secure-store";
import Constants from "expo-constants";
import { deviceProfile, download, freeBytes, isDownloaded, load, pickModel, reply }
  from "./localAi";

/* ------------------------------------------------------------------ the look: sterling on black */
const C = {
  base: "#070708", plate: "#111114", plate2: "#17181C", plate3: "#24262C",
  line: "rgba(226,231,240,0.10)", line2: "rgba(226,231,240,0.22)",
  text: "#F1F3F6", muted: "#9EA3AD", faint: "#646973",
  ember: "#FF8A3D", pass: "#6FD39A", reject: "#E8605F", chrome: "#DDE1E7",
};
const HEAD = { fontWeight: "700", letterSpacing: 1.6, textTransform: "uppercase" };
const KEY_HOST = "burnin.host", KEY_TOKEN = "burnin.token";
const POLL_MS = 2500, TIMEOUT_MS = 6000, CHAT_TIMEOUT_MS = 180000;
const CLIP_LIMIT = 30, MON_LIMIT = 15, DEFAULT_PORT = 8787;
const RELEASE_API = "https://api.github.com/repos/Saizama-cmyk/BURN-IN-CLIP-BOT/releases/tags/phone-latest";
const RELEASE_PAGE = "https://github.com/Saizama-cmyk/BURN-IN-CLIP-BOT/releases/tag/phone-latest";

/**
 * Turns whatever was typed into a base URL.
 *   192.168.0.72          -> http://192.168.0.72:8787      (home address: plain http is fine)
 *   msi.tailnet.ts.net    -> https://msi.tailnet.ts.net    (Tailscale serves it with a real cert)
 *   https://host:9000     -> kept as written
 */
function baseUrl(raw) {
  const text = String(raw || "").trim().replace(/\/+$/, "");
  if (!text) return "";
  const explicit = /^https?:\/\//i.test(text);
  const bare = text.replace(/^https?:\/\//i, "").replace(/\/.*$/, "");
  if (!bare) return "";
  if (explicit) return text.replace(/\/.*$/, "");
  if (/\.ts\.net$/i.test(bare)) return `https://${bare}`;      // Tailscale name: https, port 443
  return `http://${bare.includes(":") ? bare : `${bare}:${DEFAULT_PORT}`}`;
}

/* ------------------------------------------------------------------ api */
async function call(host, path, { token, method = "GET", body, timeout = TIMEOUT_MS } = {}) {
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

/* ------------------------------------------------------------------ small pieces */
function Lamp({ state }) {
  const color = state === "bad" ? C.reject : state === "on" ? C.ember : C.faint;
  return <View style={[s.lamp, { backgroundColor: color, shadowColor: color,
    shadowOpacity: state === "off" ? 0 : 0.9, shadowRadius: 8 }]} />;
}

function Plate({ children, style }) {
  return (
    <View style={[s.plate, style]}>
      <View style={s.plateEdge} />
      {children}
    </View>
  );
}

function Btn({ label, onPress, kind = "normal", busy, disabled }) {
  const primary = kind === "primary";
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || busy}
      style={({ pressed }) => [
        s.btn,
        primary && s.btnPrimary,
        (disabled || busy) && { opacity: 0.45 },
        pressed && { transform: [{ translateY: 1 }], opacity: 0.85 },
      ]}
      accessibilityRole="button"
      accessibilityLabel={label}
    >
      {busy ? <ActivityIndicator color={primary ? "#0B0B0D" : C.text} />
        : <Text style={[s.btnText, primary && { color: "#0B0B0D" }]}>{label}</Text>}
    </Pressable>
  );
}

function Stat({ value, label }) {
  return (
    <View style={{ flex: 1, minWidth: 0 }}>
      <Text style={s.statValue}>{value ?? "—"}</Text>
      <Text style={s.statLabel}>{label}</Text>
    </View>
  );
}

/* ------------------------------------------------------------------ the pet
   Same sprite strips the desktop pet uses: one wide PNG per action, served by the PC. React
   Native has no sprite cropping, so the strip sits inside a clipped box and slides left by one
   frame width at a time. */
const PET_FPS = 12, PET_BOX = 92, PET_IDLE = "idle", PET_CHEER = "cheer", PET_SLEEP = "sleep";

function Pet({ host, state }) {
  const [manifest, setManifest] = useState(null);
  const [frame, setFrame] = useState(0);
  const species = "blip";

  useEffect(() => {
    let alive = true;
    fetch(`${host}/static/pets/manifest.json`)
      .then(r => r.json())
      .then(m => { if (alive) setManifest(m[species] || null); })
      .catch(() => {});
    return () => { alive = false; };
  }, [host]);

  // what it is doing decides what it plays: cheering on a fresh clip, asleep when paused
  const action = state.manual_paused ? PET_SLEEP
    : (state.counts && state.counts.clips && state.status === "running") ? PET_CHEER : PET_IDLE;
  const count = manifest && manifest.actions ? (manifest.actions[action] || 1) : 1;

  useEffect(() => {
    const id = setInterval(() => setFrame(f => (f + 1) % Math.max(1, count)), 1000 / PET_FPS);
    return () => clearInterval(id);
  }, [count]);

  if (!manifest) return null;
  const scale = PET_BOX / manifest.frame;
  return (
    <View style={s.petBox} accessibilityLabel={`Blip is ${action}`}>
      <View style={{ width: PET_BOX, height: PET_BOX, overflow: "hidden" }}>
        <Image
          source={{ uri: `${host}/static/pets/${species}/${action}.png` }}
          style={{
            width: manifest.frame * count * scale,
            height: PET_BOX,
            transform: [{ translateX: -frame * manifest.frame * scale }],
          }}
          resizeMode="stretch"
        />
      </View>
    </View>
  );
}

function ago(iso) {
  const sec = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (sec < 60) return `${Math.round(sec)}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  return `${Math.round(sec / 3600)}h ago`;
}

/* ------------------------------------------------------------------ screens */
function SignIn({ onDone }) {
  const [host, setHost] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const [profiles, setProfiles] = useState([]);
  const [profileId, setProfileId] = useState("");

  useEffect(() => { SecureStore.getItemAsync(KEY_HOST).then(v => v && setHost(v)); }, []);

  // ask the PC which accounts exist, so you sign in as a specific one (names are unique there)
  const lookUp = useCallback(async (raw) => {
    const clean = baseUrl(raw);
    if (!clean.includes(".")) return;
    try {
      const r = await call(clean, "/api/auth/status");
      if (r.ok) {
        setProfiles(r.data.profiles || []);
        setProfileId(prev => prev || r.data.active || (r.data.profiles || [])[0]?.id || "");
        setError("");
      }
    } catch (e) { /* typed halfway, or the PC is asleep: the Connect button will say so */ }
  }, []);

  const go = async () => {
    const clean = baseUrl(host);
    if (!clean) return setError("Type the address shown in BURN-IN on your PC.");
    setBusy(true); setError("");
    try {
      const r = await call(clean, "/api/auth/login", {
        method: "POST", body: profileId ? { password, profile_id: profileId } : { password },
      });
      if (!r.ok || !r.data.token) {
        setError(r.data.error || (r.status === 401 ? "Wrong password." : `Sign-in failed (${r.status}).`));
      } else {
        await SecureStore.setItemAsync(KEY_HOST, clean);
        await SecureStore.setItemAsync(KEY_TOKEN, r.data.token);
        onDone(clean, r.data.token);
      }
    } catch (e) {
      setError(`No answer from ${clean.replace(/^https?:\/\//, "")}. Check: BURN-IN is running, `
        + "Settings - Dashboard - Phone remote is on, and this phone is on the same Wi-Fi "
        + "(or both are signed into Tailscale).");
    }
    setBusy(false);
  };

  return (
    <ScrollView contentContainerStyle={s.signWrap} keyboardShouldPersistTaps="handled">
      <Image source={require("./assets/icon.png")} style={s.signMark} />
      <Text style={s.signTitle}>Burn-in</Text>
      <Text style={s.signSub}>Your clips. Your PC. Your workspace.</Text>
      <Plate style={{ width: "100%", marginTop: 22 }}>
        <Text style={s.label}>PC address</Text>
        <TextInput
          value={host} onChangeText={setHost} onBlur={() => lookUp(host)}
          placeholder="192.168.0.72:8787 or name.ts.net"
          placeholderTextColor={C.faint} autoCapitalize="none" autoCorrect={false}
          keyboardType="numbers-and-punctuation" style={s.input}
        />
        {profiles.length > 0 && (
          <>
            <Text style={[s.label, { marginTop: 14 }]}>Account</Text>
            <View style={s.accounts}>
              {profiles.map(p => (
                <Pressable key={p.id} onPress={() => setProfileId(p.id)}
                  style={[s.account, profileId === p.id && s.accountOn]}>
                  <Text style={[s.accountText, profileId === p.id && { color: C.text }]}>
                    {p.name}
                  </Text>
                </Pressable>
              ))}
            </View>
          </>
        )}
        <Text style={[s.label, { marginTop: 14 }]}>Profile password</Text>
        <TextInput
          value={password} onChangeText={setPassword} secureTextEntry
          placeholderTextColor={C.faint} style={s.input} onSubmitEditing={go} returnKeyType="go"
        />
        {!!error && <Text style={s.error}>{error}</Text>}
        <View style={{ marginTop: 16 }}>
          <Btn label="Connect" kind="primary" onPress={go} busy={busy} />
        </View>
        <Text style={s.hint}>
          On the PC: Settings → Dashboard → Phone remote, then the address is on the Live desk.
        </Text>
      </Plate>
    </ScrollView>
  );
}

function Deck({ state, onPause, busy, host }) {
  const counts = state.counts || {};
  const backlog = state.backlog || {};
  const paused = !!state.manual_paused;
  const pressure = Math.round((backlog.pressure || 0) * 100);
  const lamp = backlog.failsafe ? "bad" : (state.status === "running" && !paused ? "on" : "off");
  const streams = (state.streams || []).slice(0, MON_LIMIT);

  return (
    <>
      <Plate>
        <View style={s.row}>
          <Lamp state={lamp} />
          <Text style={s.state}>
            {paused ? "paused" : backlog.failsafe ? "failsafe — catching up" : (state.status || "—")}
          </Text>
          <View style={{ flex: 1 }} />
          <Text style={s.label}>{state.gpu ? `GPU ${Math.round(state.gpu.util)}%` : "GPU —"}</Text>
        </View>
        <View style={s.track}>
          <View style={[s.trackFill, { width: `${Math.min(100, pressure)}%` },
            pressure > 70 && { backgroundColor: C.ember }]} />
        </View>
        <View style={[s.row, { marginTop: 14, alignItems: "flex-end" }]}>
          <View style={{ flex: 1, flexDirection: "row" }}>
            <Stat value={counts.clips} label="clips" />
            <Stat value={counts.posted} label="posted" />
            <Stat value={counts.watching} label="watching" />
          </View>
          {!!host && <Pet host={host} state={state} />}
        </View>
        <View style={{ marginTop: 16 }}>
          <Btn label={paused ? "Resume" : "Pause"} kind="primary" onPress={onPause} busy={busy} />
        </View>
      </Plate>

      <Plate>
        <Text style={[s.label, { marginBottom: 10 }]}>On the radar</Text>
        {streams.length === 0 && <Text style={s.empty}>Nothing live yet.</Text>}
        {streams.map((m, i) => (
          <View key={m.key || i} style={[s.mon, i > 0 && s.divider]}>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={s.monName} numberOfLines={1}>{m.name}</Text>
              <Text style={s.monSub} numberOfLines={1}>
                {m.category || "—"} · {Number(m.viewers || 0).toLocaleString()}
              </Text>
              <Text style={s.monSub}>chat {m.rate}/s · z {m.warm ? m.z : "warming"}</Text>
            </View>
          </View>
        ))}
      </Plate>
    </>
  );
}

function Clips({ host, token, onError }) {
  const [clips, setClips] = useState([]);
  const [busyId, setBusyId] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await call(host, "/api/clips", { token });
      if (r.ok) setClips(((r.data.clips || r.data) || []).slice(0, CLIP_LIMIT));
    } catch (e) { onError(); }
  }, [host, token, onError]);

  useEffect(() => { load(); }, [load]);

  const publish = async (id) => {
    setBusyId(id);
    try { await call(host, `/api/clips/${id}/publish`, { token, method: "POST" }); await load(); }
    catch (e) { onError(); }
    setBusyId("");
  };

  return (
    <Plate>
      <Text style={[s.label, { marginBottom: 10 }]}>Latest clips</Text>
      {clips.length === 0 && <Text style={s.empty}>No clips yet.</Text>}
      {clips.map((c, i) => {
        const v = c.verdict || {};
        const target = (c.event && c.event.target) || {};
        const ready = c.stage === "scheduled";
        return (
          <View key={c.id} style={[s.clip, i > 0 && s.divider]}>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={s.clipTitle} numberOfLines={2}>{v.title || target.display_name || c.id}</Text>
              <Text style={s.monSub}>{target.display_name || ""} · {ago(c.created_at)}</Text>
              <Text style={[s.tag, c.stage === "posted" && { color: C.pass },
                c.stage === "rejected" && { color: C.reject }]}>{c.stage}</Text>
            </View>
            {ready && (
              <Pressable onPress={() => publish(c.id)} disabled={busyId === c.id}
                style={({ pressed }) => [s.go, pressed && { opacity: 0.8 }]}>
                {busyId === c.id ? <ActivityIndicator color={C.text} size="small" />
                  : <Text style={s.goText}>Post now</Text>}
              </Pressable>
            )}
          </View>
        );
      })}
    </Plate>
  );
}

function Log({ host, token, state, onError }) {
  const [lines, setLines] = useState([]);
  useEffect(() => {
    let alive = true;
    call(host, "/api/system/panel", { token })
      .then(r => { if (alive && r.ok) setLines((r.data.events || []).slice(-60)); })
      .catch(() => onError());
    return () => { alive = false; };
  }, [host, token, onError]);
  const trouble = state.trouble || [];
  return (
    <>
      <Plate>
        <Text style={[s.label, { marginBottom: 10 }]}>Trouble</Text>
        {trouble.length === 0 ? <Text style={s.empty}>Nothing wrong.</Text>
          : trouble.map((t, i) => <Text key={i} style={s.troubleLine}>{t}</Text>)}
      </Plate>
      <Plate>
        <Text style={[s.label, { marginBottom: 10 }]}>What it has been doing</Text>
        {lines.length === 0 ? <Text style={s.empty}>Nothing logged yet.</Text>
          : lines.map((e, i) => (
            <Text key={i} style={s.logLine}>{`${e.time || ""} ${e.text || e.msg || ""}`.trim()}</Text>
          ))}
      </Plate>
    </>
  );
}


/* ------------------------------------------------------------------ studio */
function Studio({ host, token, onError, toast }) {
  const [info, setInfo] = useState(null);
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await call(host, "/api/studio", { token });
      if (r.ok) setInfo(r.data);
    } catch (e) { onError(); }
  }, [host, token, onError]);

  useEffect(() => { load(); }, [load]);

  const useTheme = async (name, style) => {
    setBusy(name);
    try {
      const r = await call(host, "/api/studio/default", { token, method: "POST", body: { style } });
      toast(r.ok ? `${name} is the look for new clips` : "Could not apply that look");
      await load();
    } catch (e) { onError(); }
    setBusy("");
  };

  const themes = Object.entries(info?.themes || {});
  return (
    <>
      <Plate>
        <Text style={[s.label, { marginBottom: 10 }]}>Look for new clips</Text>
        {themes.length === 0 && <Text style={s.empty}>Loading looks…</Text>}
        {themes.map(([name, style], i) => (
          <View key={name} style={[s.mon, i > 0 && s.divider]}>
            <View style={[s.swatch, { backgroundColor: style.highlight_color || C.chrome }]} />
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={s.monName}>{name}</Text>
              <Text style={s.monSub} numberOfLines={1}>
                {style.font || "—"} · {style.caption_uppercase ? "CAPS" : "normal"} captions
              </Text>
            </View>
            <Btn label="Use" busy={busy === name} onPress={() => useTheme(name, style)} />
          </View>
        ))}
      </Plate>
      {!!info?.current && (
        <Plate>
          <Text style={[s.label, { marginBottom: 8 }]}>Current</Text>
          <Text style={s.monSub}>{info.current.font} · {info.current.layout} layout
            {info.current.captions ? " · captions on" : " · captions off"}</Text>
          <Text style={[s.monSub, { marginTop: 4 }]}>Watermark {info.watermark || "none"}</Text>
        </Plate>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ settings */
function SettingsTab({ host, token, onError, toast }) {
  const [schema, setSchema] = useState(null);
  const [values, setValues] = useState(null);
  const [section, setSection] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [sc, va] = await Promise.all([
        call(host, "/api/settings/schema", { token }),
        call(host, "/api/settings", { token }),
      ]);
      if (sc.ok) setSchema(sc.data);
      if (va.ok) setValues(va.data);
    } catch (e) { onError(); }
  }, [host, token, onError]);

  useEffect(() => { load(); }, [load]);

  const save = async (key, field, value) => {
    setBusy(true);
    try {
      const r = await call(host, "/api/settings", {
        token, method: "POST", body: { [key]: { [field]: value } },
      });
      toast(r.ok ? "Saved" : (r.data.errors?.[0]?.msg || "Could not save that"));
      if (r.ok) setValues(v => ({ ...v, [key]: { ...(v?.[key] || {}), [field]: value } }));
    } catch (e) { onError(); }
    setBusy(false);
  };

  const props = schema?.properties || {};
  const deref = (raw) => (raw?.$ref ? schema.$defs?.[raw.$ref.split("/").pop()] : raw) || {};

  if (!section) {
    return (
      <Plate>
        <Text style={[s.label, { marginBottom: 10 }]}>Settings</Text>
        {Object.entries(props).map(([key, raw], i) => (
          <Pressable key={key} onPress={() => setSection(key)} style={[s.mon, i > 0 && s.divider]}>
            <Text style={[s.monName, { flex: 1 }]}>{raw.title || key}</Text>
            <Text style={s.chev}>›</Text>
          </Pressable>
        ))}
      </Plate>
    );
  }

  const node = deref(props[section]);
  const fields = Object.entries(node.properties || {});
  const current = values?.[section] || {};
  return (
    <>
      <Pressable onPress={() => setSection("")} style={{ paddingVertical: 10 }}>
        <Text style={s.label}>‹ All settings</Text>
      </Pressable>
      <Plate>
        <Text style={[s.label, { marginBottom: 10 }]}>{props[section]?.title || section}</Text>
        {fields.length === 0 && <Text style={s.empty}>Edit this one on the PC.</Text>}
        {fields.map(([key, raw], i) => {
          const f = deref(raw);
          const value = current[key];
          const kind = raw.type || f.type || (raw.anyOf || [])[0]?.type;
          if (value !== null && typeof value === "object") return null;    // nested: PC only
          return (
            <View key={key} style={[{ paddingVertical: 12 }, i > 0 && s.divider]}>
              <View style={s.row}>
                <Text style={[s.monName, { flex: 1 }]}>{raw.title || f.title || key}</Text>
                {kind === "boolean" && (
                  <Switch value={!!value} disabled={busy}
                    trackColor={{ true: C.chrome, false: C.plate3 }} thumbColor="#fff"
                    onValueChange={v => save(section, key, v)} />
                )}
              </View>
              {!!(raw.description || f.description) && (
                <Text style={s.help}>{raw.description || f.description}</Text>
              )}
              {kind !== "boolean" && (
                <TextInput
                  defaultValue={value === null || value === undefined ? "" : String(value)}
                  style={[s.input, { marginTop: 8 }]} placeholderTextColor={C.faint}
                  autoCapitalize="none" returnKeyType="done"
                  keyboardType={kind === "number" || kind === "integer"
                    ? "numbers-and-punctuation" : "default"}
                  onSubmitEditing={e => {
                    const raw2 = e.nativeEvent.text;
                    save(section, key, (kind === "number" || kind === "integer")
                      ? Number(raw2) : raw2);
                  }} />
              )}
            </View>
          );
        })}
      </Plate>
    </>
  );
}

/* ------------------------------------------------------------------ assistant
   Runs on the phone. The device is measured once and the best model it can host is chosen for
   it - nothing to pick. The PC's much larger model stays available as a fallback for phones
   that cannot host one, or when you want the better answer. */
function Assistant({ host, token, onError }) {
  const linked = !!(host && token);           // no PC: the phone's own model is the only option
  const [profile] = useState(() => deviceProfile());
  const [model, setModel] = useState(null);
  const [ready, setReady] = useState(false);
  const [progress, setProgress] = useState(-1);
  const [onPhone, setOnPhone] = useState(true);
  const [turns, setTurns] = useState([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const context = useRef(null);
  const scroller = useRef(null);

  // measure the device, choose for it, and load the weights if they are already here
  useEffect(() => {
    let alive = true;
    (async () => {
      const free = await freeBytes();
      const best = pickModel(profile, free);
      if (!alive) return;
      setModel(best);
      if (!best) { setOnPhone(false); return; }
      if (await isDownloaded(best)) {
        try {
          context.current = await load(best);
          if (alive) setReady(true);
        } catch (e) { if (alive) setProblem(`Could not load ${best.name}: ${e.message}`); }
      }
    })();
    return () => { alive = false; };
  }, [profile]);

  const fetchWeights = async () => {
    setProgress(0); setProblem("");
    try {
      await download(model, setProgress);
      context.current = await load(model);
      setReady(true);
    } catch (e) {
      setProblem(`Download failed: ${e.message}`);
    }
    setProgress(-1);
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || busy) return;
    const next = [...turns, { role: "user", content: text }];
    setTurns(next); setDraft(""); setBusy(true);
    try {
      if (onPhone && ready && context.current) {
        const answer = await reply(context.current, next);
        setTurns([...next, { role: "assistant", content: answer || "(no answer)" }]);
      } else if (!linked) {
        setTurns([...next, { role: "assistant", content: model
          ? "Download the model above first - it runs right here, no PC needed."
          : "This phone cannot run a model of its own. Connect to your PC under Remote "
            + "to use the one there." }]);
      } else {
        const r = await call(host, "/api/chat", {
          token, method: "POST", body: { messages: next }, timeout: CHAT_TIMEOUT_MS,
        });
        setTurns([...next, { role: "assistant", content: r.ok
          ? (r.data.reply || "(the model said nothing)")
          : (r.data.error || `The PC answered ${r.status}.`) }]);
      }
    } catch (e) {
      setTurns([...next, { role: "assistant", content: `That did not work: ${e.message}` }]);
      if (!onPhone) onError();
    }
    setBusy(false);
  };

  const gb = (n) => `${n.toFixed(1)} GB`;
  return (
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
      <ScrollView ref={scroller} contentContainerStyle={{ padding: 14, paddingBottom: 8 }}
        onContentSizeChange={() => scroller.current?.scrollToEnd({ animated: true })}>

        {turns.length === 0 && (
          <Plate>
            <Text style={[s.label, { marginBottom: 8 }]}>On this phone</Text>
            <Text style={s.help}>
              {profile.name} · {gb(profile.totalGb)} memory · {profile.os}
            </Text>
            {model ? (
              <>
                <Text style={[s.monName, { marginTop: 10 }]}>{model.name}</Text>
                <Text style={s.help}>
                  {model.blurb} Chosen because this phone can give an app about {gb(profile.usableGb)},
                  and this one needs {gb(model.needsGb)}.
                </Text>
                {!ready && progress < 0 && (
                  <View style={{ marginTop: 12 }}>
                    <Btn label={`Download ${(model.bytes / 1e9).toFixed(1)} GB`} kind="primary"
                      onPress={fetchWeights} />
                    <Text style={s.help}>Once. After that it works with no internet at all.</Text>
                  </View>
                )}
                {progress >= 0 && (
                  <View style={{ marginTop: 12 }}>
                    <View style={s.track}>
                      <View style={[s.trackFill, { width: `${Math.round(progress * 100)}%` }]} />
                    </View>
                    <Text style={s.help}>Downloading… {Math.round(progress * 100)}%</Text>
                  </View>
                )}
                {ready && <Text style={[s.help, { color: C.pass, marginTop: 10 }]}>
                  Loaded and running on this phone.</Text>}
              </>
            ) : (
              <Text style={[s.help, { marginTop: 10 }]}>
                {linked
                  ? "This phone is too small to host a model of its own, so the assistant uses the one already loaded on your PC instead."
                  : "This phone is too small to host a model of its own. Connect to your PC under Remote to use the one there."}
              </Text>
            )}
            {!!problem && <Text style={s.error}>{problem}</Text>}

            {linked && (ready || !model) && (
              <View style={[s.row, { marginTop: 14 }]}>
                <Text style={[s.monName, { flex: 1 }]}>Answer on this phone</Text>
                <Switch value={onPhone && !!model} disabled={!model}
                  trackColor={{ true: C.chrome, false: C.plate3 }} thumbColor="#fff"
                  onValueChange={setOnPhone} />
              </View>
            )}
            {linked && (ready || !model) && (
              <Text style={s.help}>
                {onPhone && model
                  ? "Private and offline, but a phone-sized model."
                  : "Uses the PC's much larger model. Needs BURN-IN running."}
              </Text>
            )}
          </Plate>
        )}

        {turns.map((t, i) => (
          <View key={i} style={[s.bubble, t.role === "user" ? s.bubbleMine : s.bubbleTheirs]}>
            <Text style={t.role === "user" ? s.bubbleMineText : s.bubbleText}>{t.content}</Text>
          </View>
        ))}
        {busy && <View style={[s.bubble, s.bubbleTheirs]}><ActivityIndicator color={C.muted} /></View>}
      </ScrollView>

      <View style={s.composer}>
        <TextInput value={draft} onChangeText={setDraft}
          placeholder={(onPhone || !linked) && ready ? "Ask this phone…"
            : linked ? "Ask the PC's model…" : "Load a model to start…"}
          placeholderTextColor={C.faint} style={[s.input, { flex: 1, marginTop: 0 }]}
          multiline onSubmitEditing={send} returnKeyType="send" blurOnSubmit />
        <Btn label="Send" kind="primary" onPress={send}
          busy={busy} disabled={onPhone && !!model && !ready} />
      </View>
    </KeyboardAvoidingView>
  );
}

/* ------------------------------------------------------------------ self-update
   The same releases the sideloaders read. AltStore installs updates itself once its source is
   added; this banner is for everyone else (and for Android, where you tap and install). */
function useUpdate(current) {
  const [latest, setLatest] = useState("");
  useEffect(() => {
    let alive = true;
    fetch(RELEASE_API)
      .then(r => r.json())
      .then(d => {
        const tag = String(d.tag_name || d.name || "");
        const found = (tag.match(/\d+\.\d+\.\d+/) || [])[0] || "";
        if (alive && found && found !== current) setLatest(found);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [current]);
  return latest;
}

/* ------------------------------------------------------------------ shell */
export default function App() {
  const [host, setHost] = useState(null);
  const [token, setToken] = useState(null);
  const [ready, setReady] = useState(false);
  const [state, setState] = useState({});
  const [tab, setTab] = useState("deck");
  const [offline, setOffline] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [assistant, setAssistant] = useState(false);
  const [note, setNote] = useState("");
  const timer = useRef(null);
  const toast = (msg) => { setNote(msg); setTimeout(() => setNote(""), 2600); };
  const update = useUpdate(Constants.expoConfig?.version || "");

  useEffect(() => {
    (async () => {
      const [h, t] = await Promise.all([
        SecureStore.getItemAsync(KEY_HOST), SecureStore.getItemAsync(KEY_TOKEN),
      ]);
      if (h && t) { setHost(h); setToken(t); }
      setReady(true);
    })();
  }, []);

  const poll = useCallback(async () => {
    if (!host || !token) return;
    try {
      const r = await call(host, "/api/state", { token });
      if (r.status === 401) { await signOut(); return; }
      if (r.ok) { setState(r.data); setOffline(false); }
    } catch (e) { setOffline(true); }
  }, [host, token]);

  useEffect(() => {
    if (!host || !token || assistant) return undefined;
    poll();
    timer.current = setInterval(poll, POLL_MS);
    return () => clearInterval(timer.current);
  }, [host, token, poll, assistant]);

  const signOut = async () => {
    await SecureStore.deleteItemAsync(KEY_TOKEN);
    setToken(null); setState({});
  };

  const pause = async () => {
    setBusy(true);
    try {
      await call(host, state.manual_paused ? "/api/control/resume" : "/api/control/pause",
        { token, method: "POST" });
      await poll();
    } catch (e) { setOffline(true); }
    setBusy(false);
  };

  if (!ready) {
    return <View style={[s.screen, { justifyContent: "center" }]}><ActivityIndicator color={C.chrome} /></View>;
  }
  // Not connected is not a dead end: the assistant runs on the phone by itself, so the app
  // opens either way. Remote simply shows the sign-in until there is a PC to talk to.
  const linked = !!(host && token);

  return (
    <SafeAreaProvider>
      <SafeAreaView style={s.screen} edges={["top", "bottom"]}>
        <StatusBar barStyle="light-content" />
        <View style={s.header}>
          <Image source={require("./assets/icon.png")} style={s.headerMark} />
          <Text style={s.headerTitle}>Burn-in</Text>
          <View style={{ flex: 1 }} />
          {linked && <Pressable onPress={signOut} hitSlop={10}><Text style={s.label}>Sign out</Text></Pressable>}
        </View>

        <View style={s.master}>
          {[["Remote", false], ["Assistant", true]].map(([label, on]) => (
            <Pressable key={label} onPress={() => setAssistant(on)}
              style={[s.masterHalf, assistant === on && s.masterOn]} accessibilityRole="tab">
              <Text style={[s.masterText, assistant === on && { color: C.text }]}>{label}</Text>
            </Pressable>
          ))}
        </View>

        {!!note && <View style={s.note}><Text style={s.noteText}>{note}</Text></View>}
        {!!update && (
          <Pressable onPress={() => Linking.openURL(RELEASE_PAGE)} style={s.update}>
            <Text style={s.updateText}>Version {update} is out - tap to get it</Text>
          </Pressable>
        )}
        {linked && offline && (
          <View style={s.offline}>
            <Text style={s.offlineText}>Cannot reach the PC — same Wi-Fi, BURN-IN running?</Text>
          </View>
        )}

        {assistant ? <Assistant host={host} token={token} onError={() => setOffline(true)} />
          : !linked ? <SignIn onDone={(h, t) => { setHost(h); setToken(t); }} /> : (
        <ScrollView
          contentContainerStyle={{ padding: 14, paddingBottom: 26 }}
          refreshControl={<RefreshControl tintColor={C.muted} refreshing={refreshing}
            onRefresh={async () => { setRefreshing(true); await poll(); setRefreshing(false); }} />}
        >
          {tab === "deck" && <Deck state={state} onPause={pause} busy={busy} host={host} />}
          {tab === "clips" && <Clips host={host} token={token} onError={() => setOffline(true)} />}
          {tab === "studio" && <Studio host={host} token={token} toast={toast}
            onError={() => setOffline(true)} />}
          {tab === "settings" && <SettingsTab host={host} token={token} toast={toast}
            onError={() => setOffline(true)} />}
          {tab === "log" && <Log host={host} token={token} state={state} onError={() => setOffline(true)} />}
        </ScrollView>
        )}
        {!assistant && linked && (
        <View style={s.tabs}>
          {[["deck", "Desk"], ["clips", "Clips"], ["studio", "Studio"], ["settings", "Set"],
            ["log", "Log"]].map(([key, label]) => (
            <Pressable key={key} onPress={() => setTab(key)}
              style={[s.tab, tab === key && s.tabOn]} accessibilityRole="tab">
              <Text style={[s.tabText, tab === key && { color: C.text }]}>{label}</Text>
            </Pressable>
          ))}
        </View>
        )}
      </SafeAreaView>
    </SafeAreaProvider>
  );
}

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: C.base },
  header: { flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 14, paddingVertical: 10 },
  headerMark: { width: 30, height: 30, resizeMode: "contain" },
  headerTitle: { ...HEAD, color: C.chrome, fontSize: 19 },
  plate: { backgroundColor: C.plate, borderRadius: 12, padding: 14, marginBottom: 12,
    borderWidth: 1, borderColor: C.line,
    shadowColor: "#000", shadowOpacity: 0.55, shadowRadius: 14, shadowOffset: { width: 0, height: 8 },
    elevation: 4 },
  plateEdge: { position: "absolute", left: 0, right: 0, top: 0, height: 1,
    backgroundColor: "rgba(255,255,255,0.07)" },
  row: { flexDirection: "row", alignItems: "center", gap: 10 },
  divider: { borderTopWidth: 1, borderTopColor: C.line },
  lamp: { width: 10, height: 10, borderRadius: 5 },
  state: { ...HEAD, color: C.text, fontSize: 12 },
  label: { ...HEAD, color: C.faint, fontSize: 10.5 },
  track: { height: 7, borderRadius: 2, backgroundColor: "#050506", marginTop: 12, overflow: "hidden" },
  trackFill: { height: "100%", backgroundColor: C.chrome },
  statValue: { color: C.text, fontSize: 21, fontWeight: "700", fontVariant: ["tabular-nums"] },
  statLabel: { ...HEAD, color: C.faint, fontSize: 9.5, marginTop: 2 },
  btn: { minHeight: 48, borderRadius: 10, backgroundColor: C.plate3, alignItems: "center",
    justifyContent: "center", borderWidth: 1, borderColor: C.line2 },
  btnPrimary: { backgroundColor: C.chrome, borderColor: "#FFFFFF",
    shadowColor: "#FFFFFF", shadowOpacity: 0.25, shadowRadius: 10, shadowOffset: { width: 0, height: 0 } },
  btnText: { ...HEAD, color: C.text, fontSize: 13 },
  mon: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 10 },
  monName: { color: C.text, fontWeight: "600", fontSize: 15 },
  monSub: { color: C.faint, fontSize: 12, marginTop: 2 },
  clip: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 10 },
  clipTitle: { color: C.text, fontSize: 15, fontWeight: "600" },
  tag: { ...HEAD, color: C.muted, fontSize: 9.5, marginTop: 6 },
  go: { minHeight: 40, paddingHorizontal: 12, borderRadius: 8, backgroundColor: C.plate3,
    alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: C.line2 },
  goText: { ...HEAD, color: C.text, fontSize: 11 },
  empty: { color: C.faint, textAlign: "center", paddingVertical: 16, fontSize: 13.5 },
  logLine: { color: C.muted, fontSize: 12, lineHeight: 19, fontVariant: ["tabular-nums"] },
  troubleLine: { color: "#FFC9C8", fontSize: 13, paddingVertical: 3 },
  tabs: { flexDirection: "row", gap: 4, paddingHorizontal: 10, paddingTop: 6,
    borderTopWidth: 1, borderTopColor: C.line, backgroundColor: "rgba(9,9,11,0.96)" },
  tab: { flex: 1, minHeight: 50, alignItems: "center", justifyContent: "center", borderRadius: 10 },
  tabOn: { backgroundColor: "rgba(226,231,240,0.09)" },
  tabText: { ...HEAD, color: C.faint, fontSize: 11 },
  update: { marginHorizontal: 14, marginBottom: 6, padding: 11, borderRadius: 8,
    backgroundColor: "rgba(226,231,240,0.10)", borderWidth: 1, borderColor: C.line2 },
  updateText: { ...HEAD, color: C.text, fontSize: 11 },
  rule: { height: 1, marginVertical: 14, backgroundColor: C.line },
  offline: { marginHorizontal: 14, marginBottom: 6, padding: 10, borderRadius: 8,
    backgroundColor: "rgba(232,96,95,0.14)", borderWidth: 1, borderColor: "rgba(232,96,95,0.4)" },
  offlineText: { color: "#FFC9C8", fontSize: 12.5 },
  signWrap: { flexGrow: 1, alignItems: "center", justifyContent: "center", padding: 22 },
  signMark: { width: 74, height: 74, resizeMode: "contain" },
  signTitle: { ...HEAD, color: C.chrome, fontSize: 26, marginTop: 12 },
  signSub: { color: C.faint, fontSize: 13, marginTop: 6 },
  input: { marginTop: 6, minHeight: 46, borderRadius: 8, paddingHorizontal: 12,
    backgroundColor: C.plate2, color: C.text, fontSize: 16, borderWidth: 1, borderColor: C.line },
  error: { color: "#FFC9C8", fontSize: 13, marginTop: 12 },
  hint: { color: C.faint, fontSize: 12, marginTop: 14, lineHeight: 18 },
  petBox: { width: 92, height: 92, justifyContent: "flex-end" },
  master: { flexDirection: "row", marginHorizontal: 14, marginBottom: 10, borderRadius: 10,
    backgroundColor: C.plate, borderWidth: 1, borderColor: C.line, overflow: "hidden" },
  masterHalf: { flex: 1, minHeight: 42, alignItems: "center", justifyContent: "center" },
  masterOn: { backgroundColor: C.plate3 },
  masterText: { ...HEAD, color: C.faint, fontSize: 11 },
  swatch: { width: 26, height: 26, borderRadius: 4, borderWidth: 1, borderColor: C.line2 },
  chev: { color: C.faint, fontSize: 20 },
  help: { color: C.faint, fontSize: 12, lineHeight: 17, marginTop: 4 },
  note: { marginHorizontal: 14, marginBottom: 8, padding: 10, borderRadius: 8,
    backgroundColor: C.plate2, borderWidth: 1, borderColor: C.line },
  noteText: { color: C.text, fontSize: 12.5 },
  bubble: { maxWidth: "88%", padding: 12, borderRadius: 12, marginBottom: 10 },
  bubbleTheirs: { alignSelf: "flex-start", backgroundColor: C.plate, borderWidth: 1,
    borderColor: C.line },
  bubbleMine: { alignSelf: "flex-end", backgroundColor: C.chrome },
  bubbleText: { color: C.text, fontSize: 15, lineHeight: 21 },
  bubbleMineText: { color: "#0B0B0D", fontSize: 15, lineHeight: 21 },
  composer: { flexDirection: "row", gap: 8, alignItems: "flex-end", padding: 12,
    borderTopWidth: 1, borderTopColor: C.line, backgroundColor: "rgba(9,9,11,0.96)" },
  accounts: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 8 },
  account: { minHeight: 40, paddingHorizontal: 14, justifyContent: "center", borderRadius: 8,
    backgroundColor: C.plate2, borderWidth: 1, borderColor: C.line },
  accountOn: { borderColor: C.chrome, backgroundColor: C.plate3 },
  accountText: { ...HEAD, color: C.muted, fontSize: 11 },
});
