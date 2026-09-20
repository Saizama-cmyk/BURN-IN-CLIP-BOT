/**
 * BURN-IN Remote - the clip desk, on a phone.
 *
 * Talks to the BURN-IN app running on your PC over your own Wi-Fi: it asks for the session
 * token once (your profile password), keeps it in the iOS keychain, and sends it as a bearer
 * header on every call. Nothing is stored anywhere else and nothing leaves your network.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator, Image, Pressable, RefreshControl, ScrollView, StatusBar, StyleSheet,
  Text, TextInput, View,
} from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import * as SecureStore from "expo-secure-store";

/* ------------------------------------------------------------------ the look: sterling on black */
const C = {
  base: "#070708", plate: "#111114", plate2: "#17181C", plate3: "#24262C",
  line: "rgba(226,231,240,0.10)", line2: "rgba(226,231,240,0.22)",
  text: "#F1F3F6", muted: "#9EA3AD", faint: "#646973",
  ember: "#FF8A3D", pass: "#6FD39A", reject: "#E8605F", chrome: "#DDE1E7",
};
const HEAD = { fontWeight: "700", letterSpacing: 1.6, textTransform: "uppercase" };
const KEY_HOST = "burnin.host", KEY_TOKEN = "burnin.token";
const POLL_MS = 2500, TIMEOUT_MS = 6000, CLIP_LIMIT = 30, MON_LIMIT = 12;

/* ------------------------------------------------------------------ api */
async function call(host, path, { token, method = "GET", body } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`http://${host}${path}`, {
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
  return <View style={[s.plate, style]}>{children}</View>;
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
    fetch(`http://${host}/static/pets/manifest.json`)
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
          source={{ uri: `http://${host}/static/pets/${species}/${action}.png` }}
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
    const clean = (raw || "").trim().replace(/^https?:\/\//, "").replace(/\/+$/, "");
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
    const clean = host.trim().replace(/^https?:\/\//, "").replace(/\/+$/, "");
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
      setError("Cannot reach that address. Same Wi-Fi as the PC, and BURN-IN running with Phone remote on.");
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
          placeholder="192.168.0.72:8787"
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
  const timer = useRef(null);

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
    if (!host || !token) return undefined;
    poll();
    timer.current = setInterval(poll, POLL_MS);
    return () => clearInterval(timer.current);
  }, [host, token, poll]);

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
  if (!host || !token) {
    return (
      <SafeAreaProvider>
        <SafeAreaView style={s.screen} edges={["top", "bottom"]}>
          <StatusBar barStyle="light-content" />
          <SignIn onDone={(h, t) => { setHost(h); setToken(t); }} />
        </SafeAreaView>
      </SafeAreaProvider>
    );
  }

  return (
    <SafeAreaProvider>
      <SafeAreaView style={s.screen} edges={["top", "bottom"]}>
        <StatusBar barStyle="light-content" />
        <View style={s.header}>
          <Image source={require("./assets/icon.png")} style={s.headerMark} />
          <Text style={s.headerTitle}>Burn-in</Text>
          <View style={{ flex: 1 }} />
          <Pressable onPress={signOut} hitSlop={10}><Text style={s.label}>Sign out</Text></Pressable>
        </View>

        {offline && (
          <View style={s.offline}>
            <Text style={s.offlineText}>Cannot reach the PC — same Wi-Fi, BURN-IN running?</Text>
          </View>
        )}

        <ScrollView
          contentContainerStyle={{ padding: 14, paddingBottom: 26 }}
          refreshControl={<RefreshControl tintColor={C.muted} refreshing={refreshing}
            onRefresh={async () => { setRefreshing(true); await poll(); setRefreshing(false); }} />}
        >
          {tab === "deck" && <Deck state={state} onPause={pause} busy={busy} host={host} />}
          {tab === "clips" && <Clips host={host} token={token} onError={() => setOffline(true)} />}
          {tab === "log" && <Log host={host} token={token} state={state} onError={() => setOffline(true)} />}
        </ScrollView>

        <View style={s.tabs}>
          {[["deck", "Desk"], ["clips", "Clips"], ["log", "Log"]].map(([key, label]) => (
            <Pressable key={key} onPress={() => setTab(key)}
              style={[s.tab, tab === key && s.tabOn]} accessibilityRole="tab">
              <Text style={[s.tabText, tab === key && { color: C.text }]}>{label}</Text>
            </Pressable>
          ))}
        </View>
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
    borderWidth: 1, borderColor: C.line },
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
  btnPrimary: { backgroundColor: C.chrome, borderColor: "#FFFFFF" },
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
  accounts: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 8 },
  account: { minHeight: 40, paddingHorizontal: 14, justifyContent: "center", borderRadius: 8,
    backgroundColor: C.plate2, borderWidth: 1, borderColor: C.line },
  accountOn: { borderColor: C.chrome, backgroundColor: C.plate3 },
  accountText: { ...HEAD, color: C.muted, fontSize: 11 },
});
