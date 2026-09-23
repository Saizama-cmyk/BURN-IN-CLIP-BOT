/**
 * Desk: the PC's clip desk on the phone - sign-in, live status, clips, studio looks, settings
 * and the log - plus the connection test.
 */
import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Image, Pressable, ScrollView, Switch, Text, TextInput, View } from "react-native";
import { Button, C, Card, Empty, Field, Icon, ListRow, MONO, Pill, Section, t } from "./theme";
import { baseUrl, call, testConnection } from "./api";
import * as secure from "./secure";
import { KEY_HOST, KEY_TOKEN } from "./api";

const CLIP_LIMIT = 30, MON_LIMIT = 15;

function ago(iso) {
  const sec = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (sec < 60) return `${Math.round(sec)}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
}

/* ------------------------------------------------------------------ connection test */
export function ConnectionTest({ host, token, autoRun }) {
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = useCallback(async () => {
    setBusy(true);
    try { setResult(await testConnection(host, token)); }
    catch (e) { setResult({ ok: false, steps: [], advice: e.message }); }
    setBusy(false);
  }, [host, token]);
  useEffect(() => { if (autoRun) run(); }, [autoRun, run]);
  return (
    <View>
      <Button label="Test connection" icon="pulse-outline" onPress={run} busy={busy} />
      {!!result && (
        <View style={{ marginTop: 12 }}>
          {result.steps.map((s, i) => (
            <View key={i} style={{ flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 6 }}>
              <Icon name={s.ok ? "checkmark-circle" : "close-circle"} size={18} color={s.ok ? C.good : C.bad} />
              <Text style={[t.body, { flex: 1 }]}>{s.label}</Text>
              <Text style={t.faint}>{s.detail}</Text>
            </View>))}
          <View style={{ marginTop: 8, padding: 12, borderRadius: 12, backgroundColor: result.ok ? C.goodSoft : C.badSoft }}>
            <Text style={[t.body, { color: result.ok ? C.good : C.ink }]}>{result.advice}</Text>
          </View>
        </View>)}
    </View>);
}

/* ------------------------------------------------------------------ sign in */
export function SignIn({ onDone }) {
  const [host, setHost] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [profiles, setProfiles] = useState([]);
  const [profileId, setProfileId] = useState("");
  const [help, setHelp] = useState(false);

  useEffect(() => { secure.get(KEY_HOST).then(v => v && setHost(v)); }, []);

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
    } catch (e) { /* typed halfway, or the PC is asleep: Connect or Test will say so */ }
  }, []);

  const go = async () => {
    const clean = baseUrl(host);
    if (!clean) return setError("Type the address your PC shows.");
    setBusy(true); setError("");
    try {
      const r = await call(clean, "/api/auth/login", {
        method: "POST", body: { password, device: "phone", ...(profileId ? { profile_id: profileId } : {}) },
      });
      if (!r.ok || !r.data.token) {
        setError(r.data.error || (r.status === 401 ? "Wrong password." : `Sign-in failed (${r.status}).`));
      } else {
        await secure.set(KEY_HOST, clean);
        await secure.set(KEY_TOKEN, r.data.token);
        onDone(clean, r.data.token);
      }
    } catch (e) {
      setError("The PC did not answer. Press Test connection below to see why.");
    }
    setBusy(false);
  };

  return (
    <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 40 }} keyboardShouldPersistTaps="handled">
      <View style={{ alignItems: "center", marginTop: 18, marginBottom: 22 }}>
        <Image source={require("./assets/icon.png")} resizeMode="contain" style={{ width: 64, height: 64 }} />
        <Text style={[t.title, { marginTop: 12 }]}>Connect to your PC</Text>
        <Text style={[t.muted, { marginTop: 4, textAlign: "center" }]}>Run the clip desk from here. The Assistant and Code work without it.</Text>
      </View>
      <Card>
        <Field label="PC address" value={host} onChangeText={setHost} onBlur={() => lookUp(host)}
          placeholder="192.168.0.72 or name.ts.net" autoCapitalize="none" autoCorrect={false}
          keyboardType="url" mono />
        {profiles.length > 0 && (
          <>
            <Text style={[t.label, { marginTop: 16, marginBottom: 8 }]}>Profile</Text>
            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
              {profiles.map(p => {
                const on = profileId === p.id;
                return (
                  <Pressable key={p.id} onPress={() => setProfileId(p.id)}
                    style={{ height: 38, paddingHorizontal: 14, borderRadius: 19, justifyContent: "center", borderWidth: 1,
                      borderColor: on ? C.emberLine : C.line, backgroundColor: on ? C.emberSoft : C.s2 }}>
                    <Text style={[t.body, { fontWeight: "600", color: on ? C.ember : C.ink2 }]}>{p.name}</Text>
                  </Pressable>);
              })}
            </View>
          </>)}
        <Field label="Password" style={{ marginTop: 16 }} value={password} onChangeText={setPassword}
          secureTextEntry onSubmitEditing={go} returnKeyType="go" />
        {!!error && <Text style={[t.error, { marginTop: 12 }]}>{error}</Text>}
        <Button label="Connect" kind="primary" onPress={go} busy={busy} style={{ marginTop: 16 }} />
      </Card>

      <Section title="Not connecting?" style={{ marginTop: 22 }}>
        <Card>
          <ConnectionTest host={host} />
          <Pressable onPress={() => setHelp(!help)} style={{ flexDirection: "row", alignItems: "center", marginTop: 14 }}>
            <Text style={[t.body, { flex: 1, fontWeight: "600" }]}>How to connect, step by step</Text>
            <Icon name={help ? "chevron-up" : "chevron-down"} size={18} color={C.ink3} />
          </Pressable>
          {help && (
            <View style={{ marginTop: 10, gap: 10 }}>
              {[
                "On the PC, open Ashvane → Settings → Dashboard and turn on Phone remote. Press Save and let it restart.",
                "Still on the PC, press Test connection. It shows the address to use.",
                "Type that address above, pick your profile, enter your password and tap Connect.",
                "Away from home? Install Tailscale on the PC and this phone, sign both into the same account, and use the name ending in .ts.net.",
              ].map((line, i) => (
                <View key={i} style={{ flexDirection: "row", gap: 10 }}>
                  <View style={{ width: 22, height: 22, borderRadius: 11, backgroundColor: C.s3, alignItems: "center", justifyContent: "center" }}>
                    <Text style={[t.faint, { color: C.ink, fontWeight: "700" }]}>{i + 1}</Text>
                  </View>
                  <Text style={[t.muted, { flex: 1 }]}>{line}</Text>
                </View>))}
            </View>)}
        </Card>
      </Section>
    </ScrollView>);
}

/* ------------------------------------------------------------------ the pet */
const PET_FPS = 12, PET_BOX = 84, PET_IDLE = "idle", PET_CHEER = "cheer", PET_SLEEP = "sleep";

function Pet({ host, state }) {
  const [manifest, setManifest] = useState(null);
  const [frame, setFrame] = useState(0);
  const species = "blip";
  useEffect(() => {
    let alive = true;
    fetch(`${host}/static/pets/manifest.json`).then(r => r.json())
      .then(m => { if (alive) setManifest(m[species] || null); }).catch(() => {});
    return () => { alive = false; };
  }, [host]);
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
    <View style={{ width: PET_BOX, height: PET_BOX, overflow: "hidden" }} accessibilityLabel={`Blip is ${action}`}>
      <Image source={{ uri: `${host}/static/pets/${species}/${action}.png` }}
        style={{ width: manifest.frame * count * scale, height: PET_BOX, transform: [{ translateX: -frame * manifest.frame * scale }] }}
        resizeMode="stretch" />
    </View>);
}

/* ------------------------------------------------------------------ live */
export function Live({ state, onPause, busy, host, token }) {
  const counts = state.counts || {};
  const backlog = state.backlog || {};
  const paused = !!state.manual_paused;
  const pressure = Math.min(100, Math.round((backlog.pressure || 0) * 100));
  const streams = (state.streams || []).slice(0, MON_LIMIT);
  const [testing, setTesting] = useState(false);
  const status = paused ? ["Paused", "plain"] : backlog.failsafe ? ["Catching up", "bad"]
    : state.status === "running" ? ["Live", "live"] : [state.status || "Starting", "plain"];

  return (
    <>
      <Card style={{ marginBottom: 20 }}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
          <Pill label={status[0]} tone={status[1]} />
          <View style={{ flex: 1 }} />
          <Text style={[t.mono, { color: C.ink2 }]}>{state.gpu ? `GPU ${Math.round(state.gpu.util)}%` : ""}</Text>
        </View>
        <View style={{ flexDirection: "row", alignItems: "flex-end", marginTop: 16 }}>
          <View style={{ flex: 1, flexDirection: "row" }}>
            {[[counts.clips, "clips"], [counts.posted, "posted"], [counts.watching, "watching"]].map(([v, l]) => (
              <View key={l} style={{ flex: 1 }}>
                <Text style={t.num}>{v ?? "—"}</Text>
                <Text style={t.faint}>{l}</Text>
              </View>))}
          </View>
          {!!host && <Pet host={host} state={state} />}
        </View>
        <View style={{ marginTop: 14 }}>
          <View style={{ flexDirection: "row", marginBottom: 6 }}>
            <Text style={[t.faint, { flex: 1 }]}>AI queue</Text>
            <Text style={[t.faint, { fontFamily: MONO }]}>{pressure}%</Text>
          </View>
          <View style={{ height: 6, borderRadius: 3, backgroundColor: C.s3, overflow: "hidden" }}>
            <View style={{ height: 6, width: `${pressure}%`, backgroundColor: pressure > 70 ? C.ember : C.ink2 }} />
          </View>
        </View>
        <View style={{ flexDirection: "row", gap: 8, marginTop: 16 }}>
          <Button label={paused ? "Resume" : "Pause"} icon={paused ? "play" : "pause"} kind="primary" onPress={onPause} busy={busy} grow />
          <Button label="Test" icon="pulse-outline" onPress={() => setTesting(!testing)} />
        </View>
        {testing && <View style={{ marginTop: 14 }}><ConnectionTest host={host} token={token} autoRun /></View>}
      </Card>

      <Section title={`On the radar${streams.length ? ` · ${streams.length}` : ""}`}>
        <Card pad={false}>
          {streams.length === 0 ? <Empty icon="radio-outline" title="Nothing live yet" body="Streams show up here as soon as they are picked." />
            : streams.map((m, i) => (
              <ListRow key={m.key || i} first={i === 0} icon={m.platform === "kick" ? "flash-outline" : "videocam-outline"}
                title={m.name} sub={`${m.category || "—"} · ${Number(m.viewers || 0).toLocaleString()} watching`}
                right={<View style={{ alignItems: "flex-end" }}>
                  <Text style={[t.mono, { fontSize: 12.5 }]}>{m.rate}/s</Text>
                  <Text style={[t.faint, { fontFamily: MONO }]}>{m.warm ? `z ${m.z}` : "warming"}</Text>
                </View>} />))}
        </Card>
      </Section>
    </>);
}

/* ------------------------------------------------------------------ clips */
export function Clips({ host, token, onError }) {
  const [clips, setClips] = useState(null);
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
  if (clips === null) return <ActivityIndicator color={C.ink2} style={{ marginTop: 30 }} />;
  const tone = { posted: "good", rejected: "bad", scheduled: "live" };
  return (
    <Section title="Latest clips">
      <Card pad={false}>
        {clips.length === 0 ? <Empty icon="film-outline" title="No clips yet" body="They appear here as soon as one is cut." />
          : clips.map((c, i) => {
            const v = c.verdict || {};
            const target = (c.event && c.event.target) || {};
            return (
              <View key={c.id} style={[{ padding: 16, gap: 8 }, i > 0 && { borderTopWidth: 1, borderTopColor: C.line }]}>
                <Text style={[t.body, { fontWeight: "600" }]} numberOfLines={2}>{v.title || target.display_name || c.id}</Text>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                  <Pill label={c.stage} tone={tone[c.stage] || "plain"} />
                  <Text style={[t.faint, { flex: 1 }]} numberOfLines={1}>{target.display_name || ""} · {ago(c.created_at)}</Text>
                  {c.stage === "scheduled" && (
                    <Button label="Post now" icon="send" small kind="primary" busy={busyId === c.id} onPress={() => publish(c.id)} />)}
                </View>
              </View>);
          })}
      </Card>
    </Section>);
}

/* ------------------------------------------------------------------ studio */
export function Studio({ host, token, onError, toast }) {
  const [info, setInfo] = useState(null);
  const [busy, setBusy] = useState("");
  const load = useCallback(async () => {
    try { const r = await call(host, "/api/studio", { token }); if (r.ok) setInfo(r.data); }
    catch (e) { onError(); }
  }, [host, token, onError]);
  useEffect(() => { load(); }, [load]);
  const use = async (name, style) => {
    setBusy(name);
    try {
      const r = await call(host, "/api/studio/default", { token, method: "POST", body: { style } });
      toast(r.ok ? `${name} is now the look for new clips` : "Could not apply that look");
      await load();
    } catch (e) { onError(); }
    setBusy("");
  };
  const themes = Object.entries(info?.themes || {});
  return (
    <>
      {!!info?.current && (
        <Section title="Current look">
          <Card>
            <Text style={[t.body, { fontWeight: "600" }]}>{info.current.font} · {info.current.layout} layout</Text>
            <Text style={[t.faint, { marginTop: 4 }]}>Captions {info.current.captions ? "on" : "off"} · watermark {info.watermark || "none"}</Text>
          </Card>
        </Section>)}
      <Section title="Looks for new clips">
        <Card pad={false}>
          {themes.length === 0 ? <ActivityIndicator color={C.ink2} style={{ margin: 24 }} />
            : themes.map(([name, style], i) => (
              <View key={name} style={[{ flexDirection: "row", alignItems: "center", gap: 12, padding: 14 }, i > 0 && { borderTopWidth: 1, borderTopColor: C.line }]}>
                <View style={{ width: 30, height: 30, borderRadius: 8, backgroundColor: style.highlight_color || C.ink, borderWidth: 1, borderColor: C.line2 }} />
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text style={[t.body, { fontWeight: "600" }]}>{name}</Text>
                  <Text style={t.faint} numberOfLines={1}>{style.font || "—"} · {style.caption_uppercase ? "CAPS" : "normal"} captions</Text>
                </View>
                <Button label="Use" small busy={busy === name} onPress={() => use(name, style)} />
              </View>))}
        </Card>
      </Section>
    </>);
}

/* ------------------------------------------------------------------ settings */
export function SettingsTab({ host, token, onError, toast }) {
  const [schema, setSchema] = useState(null);
  const [values, setValues] = useState(null);
  const [section, setSection] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      const [sc, va] = await Promise.all([call(host, "/api/settings/schema", { token }), call(host, "/api/settings", { token })]);
      if (sc.ok) setSchema(sc.data);
      if (va.ok) setValues(va.data);
    } catch (e) { onError(); }
  }, [host, token, onError]);
  useEffect(() => { load(); }, [load]);
  const save = async (key, field, value) => {
    setBusy(true);
    try {
      const r = await call(host, "/api/settings", { token, method: "POST", body: { [key]: { [field]: value } } });
      toast(r.ok ? "Saved" : (r.data.errors?.[0]?.msg || "Could not save that"));
      if (r.ok) setValues(v => ({ ...v, [key]: { ...(v?.[key] || {}), [field]: value } }));
    } catch (e) { onError(); }
    setBusy(false);
  };
  const props = schema?.properties || {};
  const deref = (raw) => (raw?.$ref ? schema.$defs?.[raw.$ref.split("/").pop()] : raw) || {};
  if (!schema) return <ActivityIndicator color={C.ink2} style={{ marginTop: 30 }} />;
  if (!section) {
    return (
      <Section title="Settings on the PC">
        <Card pad={false}>
          {Object.entries(props).map(([key, raw], i) => (
            <ListRow key={key} first={i === 0} title={raw.title || key} onPress={() => setSection(key)}
              right={<Icon name="chevron-forward" size={18} color={C.ink3} />} />))}
        </Card>
      </Section>);
  }
  const node = deref(props[section]);
  const current = values?.[section] || {};
  return (
    <>
      <Pressable onPress={() => setSection("")} style={{ flexDirection: "row", alignItems: "center", gap: 4, paddingVertical: 8, marginBottom: 6 }}>
        <Icon name="chevron-back" size={18} color={C.ink2} /><Text style={t.muted}>All settings</Text>
      </Pressable>
      <Section title={props[section]?.title || section}>
        <Card pad={false}>
          {Object.entries(node.properties || {}).map(([key, raw], i) => {
            const f = deref(raw);
            const value = current[key];
            const kind = raw.type || f.type || (raw.anyOf || [])[0]?.type;
            if (value !== null && typeof value === "object") return null;
            return (
              <View key={key} style={[{ padding: 16 }, i > 0 && { borderTopWidth: 1, borderTopColor: C.line }]}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
                  <Text style={[t.body, { flex: 1, fontWeight: "600" }]}>{raw.title || f.title || key}</Text>
                  {kind === "boolean" && (
                    <Switch value={!!value} disabled={busy} trackColor={{ true: C.ember, false: C.s4 }} thumbColor="#fff"
                      onValueChange={v => save(section, key, v)} />)}
                </View>
                {!!(raw.description || f.description) && <Text style={[t.faint, { marginTop: 4 }]}>{raw.description || f.description}</Text>}
                {kind !== "boolean" && (
                  <TextInput defaultValue={value === null || value === undefined ? "" : String(value)}
                    style={{ marginTop: 10, minHeight: 44, borderRadius: 10, paddingHorizontal: 12, backgroundColor: C.s2, color: C.ink,
                      fontSize: 15, borderWidth: 1, borderColor: C.line }}
                    placeholderTextColor={C.ink3} autoCapitalize="none" returnKeyType="done"
                    keyboardType={kind === "number" || kind === "integer" ? "numbers-and-punctuation" : "default"}
                    onSubmitEditing={e => save(section, key, (kind === "number" || kind === "integer") ? Number(e.nativeEvent.text) : e.nativeEvent.text)} />)}
              </View>);
          })}
        </Card>
      </Section>
    </>);
}

/* ------------------------------------------------------------------ log */
export function Log({ host, token, state, onError }) {
  const [lines, setLines] = useState([]);
  useEffect(() => {
    let alive = true;
    call(host, "/api/system/panel", { token })
      .then(r => { if (alive && r.ok) setLines((r.data.events || []).slice(-60).reverse()); })
      .catch(() => onError());
    return () => { alive = false; };
  }, [host, token, onError]);
  const trouble = state.trouble || [];
  return (
    <>
      <Section title="Trouble">
        <Card>
          {trouble.length === 0 ? (
            <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
              <Icon name="checkmark-circle" size={18} color={C.good} /><Text style={t.body}>Nothing wrong.</Text>
            </View>)
            : trouble.map((x, i) => (
              <View key={i} style={{ flexDirection: "row", gap: 10, paddingVertical: 4 }}>
                <Icon name="alert-circle" size={17} color={C.bad} /><Text style={[t.body, { flex: 1 }]}>{x}</Text>
              </View>))}
        </Card>
      </Section>
      <Section title="What it has been doing">
        <Card>
          {lines.length === 0 ? <Text style={t.faint}>Nothing logged yet.</Text>
            : lines.map((e, i) => (
              <Text key={i} style={{ fontFamily: MONO, fontSize: 12, lineHeight: 19, color: C.ink2 }}>
                <Text style={{ color: C.ink3 }}>{e.time || ""} </Text>{e.text || e.msg || ""}
              </Text>))}
        </Card>
      </Section>
    </>);
}
