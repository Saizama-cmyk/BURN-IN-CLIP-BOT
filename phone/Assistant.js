/**
 * The Assistant: a private chat that thinks on the phone (the PC is only backup), with skills,
 * rules, your own instructions, attachments and saved chats.
 */
import React, { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Alert, KeyboardAvoidingView, Platform, Pressable, ScrollView, Text,
  TextInput, View } from "react-native";
import { allSkills, buildSystem, loadBrain, newId, saveBrain, skillFor, titleFrom } from "./brain";
import { AttachRow, BrainEditor, History, MessageBody, pickAttachment, prepareTurn, SkillBar } from "./Chat";
import { SYSTEM } from "./localAi";
import { call } from "./api";
import { ModelCard } from "./useModel";
import { Button, C, Card, chatStyles, Empty, Icon, IconButton, Segmented, t } from "./theme";

const STARTERS = [
  ["Plan my week", "Help me plan my week. Ask me what's on first."],
  ["Explain something", "/learn how compound interest works"],
  ["Grill me", "/grill-me on my idea: "],
  ["Summarise text", "/summarize "],
];

export default function Assistant({ host, token, m, onError }) {
  const linked = !!(host && token);
  const [brain, setBrainState] = useState(null);
  const [chatId, setChatId] = useState(null);
  const [view, setView] = useState("chat");
  const [picked, setPicked] = useState(null);
  const [attached, setAttached] = useState([]);
  const [streaming, setStreaming] = useState("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const scroller = useRef(null);

  useEffect(() => { loadBrain().then(setBrainState); }, []);
  const setBrain = (b) => { setBrainState(b); saveBrain(b).catch(() => {}); };
  const skills = brain ? allSkills(brain) : [];
  const chats = brain ? brain.chats.filter(c => (c.mode || "chat") === "chat") : [];
  const chat = chats.find(c => c.id === chatId) || null;
  const turns = chat ? chat.messages : [];
  const ui = { s: chatStyles, C, Btn: (p) => <Button {...p} />, Plate: (p) => <Card {...p} /> };

  const saveTurns = (id, messages, title) => {
    setBrainState(prev => {
      const others = prev.chats.filter(c => c.id !== id);
      const was = prev.chats.find(c => c.id === id);
      const next = { ...prev, chats: [{ id, mode: "chat", title: was?.title || title, updated: Date.now(), messages }, ...others] };
      saveBrain(next).catch(() => {});
      return next;
    });
  };

  const attach = async (kind) => {
    try { const a = await pickAttachment(kind); if (a) setAttached(prev => [...prev, a]); }
    catch (e) { setProblem(e.message); }
  };

  const send = async () => {
    const typed = draft.trim();
    if ((!typed && !attached.length) || busy || !brain) return;
    const { skill, text } = skillFor(typed, skills, picked);
    const id = chatId || newId();
    const shown = [typed, ...attached.map(a => `[${a.kind}: ${a.name}]`)].filter(Boolean).join("\n");
    const history = turns.map(x => ({ role: x.role, content: x.model || x.content }));
    setChatId(id); setDraft(""); setBusy(true); setProblem(""); setStreaming("");
    saveTurns(id, [...turns, { role: "user", content: shown }], titleFrom(typed || attached[0]?.name));
    try {
      const prepared = await prepareTurn({ text: text || typed, attachments: attached, skill, linked, host, token, call });
      setAttached([]);
      const mine = { role: "user", content: shown, model: prepared.content };
      const system = buildSystem(SYSTEM, brain, skills, skill);
      let sofar = "";
      const answer = await m.ask([...history, { role: "user", content: prepared.content }], system,
        (tok) => { sofar += tok; setStreaming(sofar); });
      const notes = prepared.notes.length ? `\n\n${prepared.notes.join("\n")}` : "";
      saveTurns(id, [...turns, mine, { role: "assistant", content: (answer || "(no answer)") + notes, via: m.where }]);
      setPicked(null);
    } catch (e) {
      saveTurns(id, [...turns, { role: "user", content: shown }, { role: "assistant", content: `That did not work: ${e.message}` }]);
      if (m.where === "pc") onError();
    }
    setStreaming(""); setBusy(false);
  };

  if (!brain) return <ActivityIndicator color={C.ink2} style={{ marginTop: 40 }} />;

  const top = (
    <View style={{ flexDirection: "row", alignItems: "center", paddingHorizontal: 16, paddingBottom: 10, gap: 8 }}>
      <Segmented style={{ flex: 1 }} value={view} onChange={setView}
        options={[["chat", "Chat"], ["history", "Chats"], ["brain", "Skills & rules"]]} />
      <IconButton icon="create-outline" label="New chat" color={C.ink}
        onPress={() => { setChatId(null); setPicked(null); setView("chat"); }} />
    </View>);

  if (view === "history") {
    return (<View style={{ flex: 1 }}>{top}
      <History chats={chats} current={chatId} ui={ui}
        onOpen={(id) => { setChatId(id); setView("chat"); }}
        onNew={() => { setChatId(null); setView("chat"); }}
        onDelete={(id) => { setBrain({ ...brain, chats: brain.chats.filter(c => c.id !== id) }); if (id === chatId) setChatId(null); }} />
    </View>);
  }
  if (view === "brain") {
    return (<View style={{ flex: 1 }}>{top}<BrainEditor brain={brain} skills={skills} onChange={setBrain} ui={ui} /></View>);
  }

  const canSend = m.where !== "none";
  return (
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} keyboardVerticalOffset={110} style={{ flex: 1 }}>
      {top}
      <ScrollView ref={scroller} contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 12 }}
        onContentSizeChange={() => scroller.current?.scrollToEnd({ animated: true })} keyboardShouldPersistTaps="handled">
        {turns.length === 0 && (
          <>
            <ModelCard m={m} />
            <Card>
              <Empty icon="chatbubble-ellipses-outline" title="Ask anything"
                body="Private by default: it thinks on this phone. Type / for a skill, or attach a photo, video or file." />
              <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, justifyContent: "center", marginTop: 4 }}>
                {STARTERS.map(([label, text]) => (
                  <Pressable key={label} onPress={() => setDraft(text)}
                    style={{ paddingHorizontal: 12, height: 34, borderRadius: 17, justifyContent: "center", backgroundColor: C.s2, borderWidth: 1, borderColor: C.line }}>
                    <Text style={[t.muted, { color: C.ink }]}>{label}</Text>
                  </Pressable>))}
              </View>
            </Card>
          </>)}
        {turns.map((x, i) => x.role === "user" ? (
          <View key={i} style={{ alignSelf: "flex-end", maxWidth: "86%", backgroundColor: C.s3, borderRadius: 18,
            borderBottomRightRadius: 6, paddingHorizontal: 14, paddingVertical: 10, marginVertical: 6 }}>
            <MessageBody text={x.content} mine ui={ui} />
          </View>
        ) : (
          <View key={i} style={{ marginVertical: 10 }}>
            <MessageBody text={x.content} ui={ui} />
            {!!x.via && <Text style={[t.faint, { marginTop: 6 }]}>{x.via === "phone" ? "On this phone" : "From your PC"}</Text>}
          </View>))}
        {busy && (
          <View style={{ marginVertical: 10 }}>
            {streaming ? <MessageBody text={streaming} ui={ui} />
              : <View style={{ flexDirection: "row", gap: 10, alignItems: "center" }}><ActivityIndicator color={C.ember} /><Text style={t.muted}>Thinking…</Text></View>}
          </View>)}
        {!!problem && <Text style={[t.error, { marginTop: 8 }]}>{problem}</Text>}
      </ScrollView>
      <View style={{ borderTopWidth: 1, borderTopColor: C.line, backgroundColor: C.bg, paddingTop: 6 }}>
        <SkillBar skills={skills} picked={picked} onPick={setPicked} ui={ui} />
        <AttachRow items={attached} ui={ui} onRemove={(id) => setAttached(prev => prev.filter(a => a.id !== id))} />
        <View style={{ flexDirection: "row", alignItems: "flex-end", gap: 8, paddingHorizontal: 12, paddingBottom: 10, paddingTop: 6 }}>
          <Pressable accessibilityLabel="Attach" onPress={() => Alert.alert("Attach", "", [
            { text: "Photo", onPress: () => attach("image") }, { text: "Video", onPress: () => attach("video") },
            { text: "File", onPress: () => attach("file") }, { text: "Cancel", style: "cancel" }])}
            style={({ pressed }) => ({ width: 46, height: 46, borderRadius: 14, alignItems: "center", justifyContent: "center",
              backgroundColor: pressed ? C.s3 : C.s2, borderWidth: 1, borderColor: C.line })}>
            <Icon name="add" size={24} color={C.ink} />
          </Pressable>
          <TextInput value={draft} onChangeText={setDraft} multiline
            placeholder={picked ? `/${picked.name}…` : canSend ? "Message, or / for a skill" : "Download the model above to start"}
            placeholderTextColor={C.ink3} selectionColor={C.ember}
            style={{ flex: 1, minHeight: 46, maxHeight: 140, borderRadius: 14, paddingHorizontal: 14, paddingTop: 12, paddingBottom: 12,
              backgroundColor: C.s2, color: C.ink, fontSize: 15.5, borderWidth: 1, borderColor: C.line }} />
          <Pressable onPress={send} disabled={busy || !canSend || (!draft.trim() && !attached.length)} accessibilityLabel="Send"
            style={({ pressed }) => ({ width: 46, height: 46, borderRadius: 14, alignItems: "center", justifyContent: "center",
              backgroundColor: busy || !canSend || (!draft.trim() && !attached.length) ? C.s3 : C.ember, opacity: pressed ? 0.8 : 1 })}>
            {busy ? <ActivityIndicator color={C.ink} /> : <Icon name="arrow-up" size={22} color={draft.trim() || attached.length ? "#1A0D06" : C.ink3} />}
          </Pressable>
        </View>
      </View>
    </KeyboardAvoidingView>);
}
