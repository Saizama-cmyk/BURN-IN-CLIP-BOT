/**
 * Ashvane on the phone: three places, one tab bar.
 *
 *   Desk       the clip desk on your PC, over your own Wi-Fi or Tailscale
 *   Assistant  a private chat that thinks on the phone (the PC is backup)
 *   Code       a small IDE whose agent is that same model
 *
 * The phone signs in to the PC once (your profile password) and keeps the session token in the
 * keychain; the PC keeps that sign-in for weeks. Face ID / fingerprint guards the app itself.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Image, Linking, Pressable, RefreshControl, ScrollView, StatusBar, Text, View } from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import Constants from "expo-constants";
import { LockScreen, useLock } from "./Lock";
import { call, KEY_HOST, KEY_TOKEN } from "./api";
import * as secure from "./secure";
import { useModel } from "./useModel";
import Assistant from "./Assistant";
import Code from "./code/Code";
import { Clips, Live, Log, SettingsTab, SignIn, Studio } from "./Remote";
import { C, Icon, IconButton, Pill, t } from "./theme";

const POLL_MS = 2500;
const RELEASE_API = "https://api.github.com/repos/Saizama-cmyk/Ashvane/releases/tags/phone-latest";
const RELEASE_PAGE = "https://github.com/Saizama-cmyk/Ashvane/releases/tag/phone-latest";
const TABS = [["desk", "Desk", "albums"], ["chat", "Assistant", "chatbubble-ellipses"], ["code", "Code", "code-slash"]];
const DESK_TABS = [["live", "Live"], ["clips", "Clips"], ["studio", "Studio"], ["settings", "Settings"], ["log", "Log"]];

/* The same releases the sideloaders read. AltStore updates by itself once its source is added;
   this banner is for everyone else (and Android, where you tap and install). */
function useUpdate(current) {
  const [latest, setLatest] = useState("");
  useEffect(() => {
    let alive = true;
    fetch(RELEASE_API).then(r => r.json()).then(d => {
      const found = (String(d.tag_name || d.name || "").match(/\d+\.\d+\.\d+/) || [])[0] || "";
      if (alive && found && current && found !== current) setLatest(found);
    }).catch(() => {});
    return () => { alive = false; };
  }, [current]);
  return latest;
}

export default function App() {
  const [host, setHost] = useState(null);
  const [token, setToken] = useState(null);
  const [ready, setReady] = useState(false);
  const [state, setState] = useState({});
  const [tab, setTab] = useState("desk");
  const [deskTab, setDeskTab] = useState("live");
  const [offline, setOffline] = useState(false);
  const [signedOut, setSignedOut] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [note, setNote] = useState("");
  const timer = useRef(null);
  const toast = (msg) => { setNote(msg); setTimeout(() => setNote(""), 2600); };
  const update = useUpdate(Constants.expoConfig?.version || "");
  const lock = useLock();
  const m = useModel({ host, token });

  useEffect(() => {
    (async () => {
      const [h, tk] = await Promise.all([secure.get(KEY_HOST), secure.get(KEY_TOKEN)]);
      if (h && tk) { setHost(h); setToken(tk); }
      setReady(true);
    })();
  }, []);

  const signOut = useCallback(async (expired = false) => {
    await secure.del(KEY_TOKEN);
    setToken(null); setState({}); setSignedOut(expired);
  }, []);

  const poll = useCallback(async () => {
    if (!host || !token) return;
    try {
      const r = await call(host, "/api/state", { token });
      if (r.status === 401) { await signOut(true); return; }
      if (r.ok) { setState(r.data); setOffline(false); }
    } catch (e) { setOffline(true); }
  }, [host, token, signOut]);

  useEffect(() => {
    if (!host || !token || tab !== "desk") return undefined;
    poll();
    timer.current = setInterval(poll, POLL_MS);
    return () => clearInterval(timer.current);
  }, [host, token, poll, tab]);

  const pause = async () => {
    setBusy(true);
    try {
      await call(host, state.manual_paused ? "/api/control/resume" : "/api/control/pause", { token, method: "POST" });
      await poll();
    } catch (e) { setOffline(true); }
    setBusy(false);
  };

  if (!ready) return <View style={{ flex: 1, backgroundColor: C.bg, justifyContent: "center" }}><ActivityIndicator color={C.ink2} /></View>;
  if (lock.locked) {
    return (
      <SafeAreaProvider>
        <StatusBar barStyle="light-content" backgroundColor={C.bg} />
        <LockScreen onUnlock={lock.unlock} problem={lock.problem} />
      </SafeAreaProvider>);
  }

  const linked = !!(host && token);
  const onError = () => setOffline(true);
  const connection = !linked ? ["Not connected", "plain"] : offline ? ["Can't reach PC", "bad"] : ["Connected", "good"];
  const title = TABS.find(x => x[0] === tab)[1];

  return (
    <SafeAreaProvider>
      <SafeAreaView style={{ flex: 1, backgroundColor: C.bg }} edges={["top"]}>
        <StatusBar barStyle="light-content" backgroundColor={C.bg} />
        <View style={{ flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 16, paddingTop: 6, paddingBottom: 12 }}>
          <Image source={require("./assets/icon.png")} resizeMode="contain" style={{ width: 28, height: 28 }} />
          <Text style={[t.title, { flex: 1 }]}>{title}</Text>
          {tab === "desk" && <Pill label={connection[0]} tone={connection[1]} />}
          {tab === "desk" && linked && <IconButton icon="log-out-outline" label="Sign out" onPress={() => signOut(false)} />}
        </View>

        {!!note && <Banner tone="plain" text={note} />}
        {!!update && <Banner tone="live" text={`Version ${update} is out · tap to get it`} onPress={() => Linking.openURL(RELEASE_PAGE)} />}

        <View style={{ flex: 1 }}>
          {tab === "desk" && (!linked ? (
            <>
              {signedOut && <Banner tone="plain" text="The PC ended the session. Sign in again below." />}
              <SignIn onDone={(h, tk) => { setHost(h); setToken(tk); setSignedOut(false); setOffline(false); }} />
            </>
          ) : (
            <>
              {offline && <Banner tone="bad" text="Can't reach the PC. Pull down to retry, or open Live → Test." />}
              <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ flexGrow: 0 }}
                contentContainerStyle={{ paddingHorizontal: 16, gap: 8, paddingBottom: 12 }}>
                {DESK_TABS.map(([key, label]) => {
                  const on = key === deskTab;
                  return (
                    <Pressable key={key} onPress={() => setDeskTab(key)}
                      style={{ height: 34, paddingHorizontal: 14, borderRadius: 17, justifyContent: "center",
                        backgroundColor: on ? C.ink : C.s1, borderWidth: 1, borderColor: on ? C.ink : C.line }}>
                      <Text style={{ fontSize: 14, fontWeight: "600", color: on ? C.bg : C.ink2 }}>{label}</Text>
                    </Pressable>);
                })}
              </ScrollView>
              <ScrollView contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 30 }}
                refreshControl={<RefreshControl tintColor={C.ink2} refreshing={refreshing}
                  onRefresh={async () => { setRefreshing(true); await poll(); setRefreshing(false); }} />}>
                {deskTab === "live" && <Live state={state} onPause={pause} busy={busy} host={host} token={token} />}
                {deskTab === "clips" && <Clips host={host} token={token} onError={onError} />}
                {deskTab === "studio" && <Studio host={host} token={token} toast={toast} onError={onError} />}
                {deskTab === "settings" && <SettingsTab host={host} token={token} toast={toast} onError={onError} />}
                {deskTab === "log" && <Log host={host} token={token} state={state} onError={onError} />}
              </ScrollView>
            </>))}
          {tab === "chat" && <Assistant host={host} token={token} m={m} onError={onError} />}
          {tab === "code" && <Code m={m} />}
        </View>

        <SafeAreaView edges={["bottom"]} style={{ backgroundColor: C.s1, borderTopWidth: 1, borderTopColor: C.line }}>
          <View style={{ flexDirection: "row", paddingTop: 6 }}>
            {TABS.map(([key, label, icon]) => {
              const on = key === tab;
              return (
                <Pressable key={key} onPress={() => setTab(key)} accessibilityRole="tab" accessibilityState={{ selected: on }}
                  style={{ flex: 1, alignItems: "center", paddingVertical: 6, gap: 3 }}>
                  <View style={{ width: 22, height: 3, borderRadius: 2, marginBottom: 3, backgroundColor: on ? C.ember : "transparent" }} />
                  <Icon name={on ? icon : `${icon}-outline`} size={22} color={on ? C.ink : C.ink3} />
                  <Text style={{ fontSize: 11.5, fontWeight: "600", color: on ? C.ink : C.ink3 }}>{label}</Text>
                </Pressable>);
            })}
          </View>
        </SafeAreaView>
      </SafeAreaView>
    </SafeAreaProvider>);
}

function Banner({ text, tone, onPress }) {
  const colors = { bad: [C.badSoft, "rgba(236,91,82,0.35)", C.ink], live: [C.emberSoft, C.emberLine, C.ink], plain: [C.s2, C.line, C.ink] }[tone];
  return (
    <Pressable onPress={onPress} disabled={!onPress}
      style={{ marginHorizontal: 16, marginBottom: 10, paddingHorizontal: 14, paddingVertical: 10, borderRadius: 12,
        backgroundColor: colors[0], borderWidth: 1, borderColor: colors[1] }}>
      <Text style={[t.muted, { color: colors[2] }]}>{text}</Text>
    </Pressable>);
}
