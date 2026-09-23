/**
 * The brain both the Assistant and Code use: the phone's own model first, the PC as backup.
 *
 * The device is measured once and the best model it can host is chosen for it - nothing to
 * pick. If the phone cannot host one, or you switch to the PC, the same question goes to the
 * much larger model on your PC instead. `ask()` hides which one answered; `where` says it.
 */
import React, { useEffect, useRef, useState } from "react";
import { Switch, Text, View } from "react-native";
import { deviceProfile, download, downloadedIds, freeBytes, isDownloaded, load, pendingProgress, pickModel,
  reply } from "./localAi";
import { call } from "./api";
import { Button, C, Card, t } from "./theme";

const CHAT_TIMEOUT_MS = 180000;
const PC_POLL_MS = 1500;           // how often the PC is asked how its copy is coming along
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

export function useModel({ host, token }) {
  const linked = !!(host && token);
  const [profile] = useState(() => deviceProfile());
  const [model, setModel] = useState(null);
  const [ready, setReady] = useState(false);
  const [progress, setProgress] = useState(-1);
  const [preferPhone, setPreferPhone] = useState(true);
  const [problem, setProblem] = useState("");
  const [checked, setChecked] = useState(false);
  const [stage, setStage] = useState("");          // what the download is doing, in words
  const [resumeAt, setResumeAt] = useState(0);     // an interrupted download waiting to continue
  const context = useRef(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      const best = pickModel(profile, await freeBytes(), await downloadedIds());
      if (!alive) return;
      setModel(best);
      if (best) setResumeAt(await pendingProgress(best));
      if (best && await isDownloaded(best)) {
        try {
          context.current = await load(best);
          if (alive) setReady(true);
        } catch (e) { if (alive) setProblem(`Could not load ${best.name}: ${e.message}`); }
      }
      if (alive) setChecked(true);
    })();
    return () => { alive = false; };
  }, [profile]);

  /** Ask the PC for its copy: it fetches the model once, then the phone copies it over Wi-Fi. */
  const viaPc = async () => {
    const path = `/api/assistant/models/${model.id}`;
    let st = (await call(host, `${path}/fetch`, { token, method: "POST" })).data;
    while (st && !st.ready) {
      if (st.error) throw new Error(st.error);
      setStage("Your PC is fetching it");
      setProgress(st.bytes ? st.have / st.bytes : 0);
      await sleep(PC_POLL_MS);
      st = (await call(host, path, { token })).data;
    }
    return { url: `${host}${path}/file`, headers: { Authorization: `Bearer ${token}`, "X-ClipBot": "1" } };
  };

  const fetchWeights = async () => {
    setProgress(0); setProblem("");
    try {
      let source = null;
      if (linked) {
        try { source = await viaPc(); }
        catch (e) { setStage(""); }            // the PC can't help: go straight to the internet
      }
      setStage(source ? "Copying from your PC" : "Downloading");
      await download(model, setProgress, source);
      setStage("Loading");
      context.current = await load(model);
      setReady(true); setResumeAt(0);
    } catch (e) {
      setProblem(`Download stopped: ${e.message}. Tap to carry on from where it stopped.`);
      setResumeAt(await pendingProgress(model));
    }
    setStage(""); setProgress(-1);
  };

  const onPhone = ready && !!context.current && (preferPhone || !linked);
  const where = onPhone ? "phone" : linked ? "pc" : "none";

  /** One answer. `messages` is [{role, content}]; `onToken` streams (phone only). */
  const ask = async (messages, system, onToken, long = false) => {
    if (onPhone) return reply(context.current, messages, onToken, system, long);
    if (linked) {
      const r = await call(host, "/api/chat", { token, method: "POST",
        body: { messages, system }, timeout: CHAT_TIMEOUT_MS });
      if (!r.ok) throw new Error(r.data.error || `the PC answered ${r.status}`);
      const text = r.data.reply || "";
      if (onToken && text) onToken(text);
      return text;
    }
    throw new Error(model ? "Download the model first. It runs right here, no PC needed."
      : "This phone can't host a model. Connect your PC under Desk to use the one there.");
  };

  return { profile, model, ready, progress, problem, checked, linked, where, preferPhone, stage, resumeAt,
           setPreferPhone, fetchWeights, ask };
}

const gb = (n) => `${n.toFixed(1)} GB`;

/** The card that explains where answers come from, and downloads the model the first time. */
export function ModelCard({ m, compact }) {
  if (!m.checked) return null;
  const { profile, model, ready, progress, problem, linked, where, preferPhone, setPreferPhone } = m;
  if (compact && (ready || (!model && linked))) return null;
  return (
    <Card style={{ marginBottom: 16 }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
        <View style={{ width: 8, height: 8, borderRadius: 4,
          backgroundColor: where === "phone" ? C.good : where === "pc" ? C.ember : C.ink3 }} />
        <Text style={[t.h2, { flex: 1 }]}>
          {where === "phone" ? "Running on this phone" : where === "pc" ? "Using your PC's model" : "No model yet"}
        </Text>
      </View>
      <Text style={[t.faint, { marginTop: 6 }]}>{profile.name} · {gb(profile.totalGb)} memory · {profile.os}</Text>
      {model ? (
        <>
          <Text style={[t.body, { marginTop: 12, fontWeight: "600" }]}>{model.name}</Text>
          <Text style={[t.muted, { marginTop: 2 }]}>{model.blurb} This phone can give an app about {gb(profile.usableGb)}; it needs {gb(model.needsGb)}.</Text>
          {!ready && progress < 0 && (
            <View style={{ marginTop: 14 }}>
              <Button label={m.resumeAt > 0 ? `Carry on (${Math.round(m.resumeAt * 100)}% done)`
                : `Download ${(model.bytes / 1e9).toFixed(1)} GB`} icon="cloud-download-outline" kind="primary" onPress={m.fetchWeights} />
              <Text style={[t.faint, { marginTop: 8 }]}>
                {linked ? "Once, copied from your PC over Wi-Fi (much faster). After that it works with no internet at all."
                  : "Once. It keeps going with the screen locked, and picks up where it stopped. After that it works offline."}
              </Text>
            </View>)}
          {progress >= 0 && (
            <View style={{ marginTop: 14 }}>
              <View style={{ height: 6, borderRadius: 3, backgroundColor: C.s3, overflow: "hidden" }}>
                <View style={{ height: 6, width: `${Math.round(progress * 100)}%`, backgroundColor: C.ember }} />
              </View>
              <Text style={[t.faint, { marginTop: 8 }]}>{m.stage || "Downloading"}… {Math.round(progress * 100)}%</Text>
            </View>)}
        </>
      ) : (
        <Text style={[t.muted, { marginTop: 12 }]}>
          {linked ? "This phone is too small to host a model, so answers come from your PC."
            : "This phone is too small to host a model. Connect your PC under Desk to use the one there."}
        </Text>)}
      {!!problem && <Text style={[t.error, { marginTop: 10 }]}>{problem}</Text>}
      {linked && ready && (
        <View style={{ flexDirection: "row", alignItems: "center", marginTop: 14 }}>
          <View style={{ flex: 1 }}>
            <Text style={[t.body, { fontWeight: "600" }]}>Answer on this phone</Text>
            <Text style={t.faint}>{preferPhone ? "Private and offline, phone-sized." : "The PC's bigger model. Needs Ashvane running."}</Text>
          </View>
          <Switch value={preferPhone} onValueChange={setPreferPhone}
            trackColor={{ true: C.ember, false: C.s4 }} thumbColor="#fff" />
        </View>)}
    </Card>);
}
