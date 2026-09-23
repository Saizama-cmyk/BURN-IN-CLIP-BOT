/**
 * The assistant's conversation pieces: chat history, the skill bar, attachments, code blocks,
 * and the editor for your instructions, rules and skills.
 *
 * Everything thinks on the phone. The PC is only asked for what a phone model cannot do - see a
 * picture, watch a video - and it answers with text that the phone's model then reasons over.
 */
import React, { useState } from "react";
import { Alert, Pressable, ScrollView, Share, Switch, Text, TextInput, View } from "react-native";
import * as DocumentPicker from "expo-document-picker";
import * as FileSystem from "expo-file-system";
import * as ImagePicker from "expo-image-picker";
import { FILE_TEXT_MAX, importSkillFromUrl, newId, parseSkillMd } from "./brain";
import { C, MONO } from "./theme";

const TEXT_TYPES = /\.(txt|md|markdown|csv|json|js|jsx|ts|tsx|py|html|css|xml|yml|yaml|log|ini|sh|java|c|cpp|h|cs|go|rs|rb|php|swift|kt|sql)$/i;
const URL_RE = /https?:\/\/\S+/i;
const IMAGE_QUALITY = 0.6;       // pictures are shrunk before they travel to the PC
const WATCH_TIMEOUT_MS = 330000; // the PC fetches, looks and listens: give it time

/* ------------------------------------------------------------------ messages with code blocks */
export function MessageBody({ text, mine, ui }) {
  const { s } = ui;
  const parts = String(text || "").split(/```(\w*)\n?([\s\S]*?)```/g);
  const out = [];
  for (let i = 0; i < parts.length; i += 3) {
    if (parts[i]?.trim()) {
      out.push(<Text key={`t${i}`} style={mine ? s.bubbleMineText : s.bubbleText}>{parts[i].trim()}</Text>);
    }
    if (i + 2 < parts.length) {
      const lang = parts[i + 1] || "code", code = parts[i + 2].replace(/\n$/, "");
      out.push(
        <View key={`c${i}`} style={{ backgroundColor: C.bg, borderRadius: 8, marginVertical: 6,
          borderWidth: 1, borderColor: C.s3 }}>
          <View style={{ flexDirection: "row", paddingHorizontal: 10, paddingTop: 6 }}>
            <Text style={[s.label, { flex: 1 }]}>{lang}</Text>
            <Pressable onPress={() => Share.share({ message: code })} hitSlop={8}>
              <Text style={[s.label, { color: C.ember }]}>Share</Text>
            </Pressable>
          </View>
          <ScrollView horizontal>
            <Text selectable style={{ fontFamily: MONO, fontSize: 12, color: C.ink, padding: 10 }}>
              {code}
            </Text>
          </ScrollView>
        </View>);
    }
  }
  return <>{out}</>;
}

/* ------------------------------------------------------------------ skill bar */
export function SkillBar({ skills, picked, onPick, ui }) {
  const { s } = ui;
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false}
      contentContainerStyle={{ paddingHorizontal: 10, paddingVertical: 6, gap: 6 }}>
      {skills.map(k => {
        const on = picked?.id === k.id;
        return (
          <Pressable key={k.id} onPress={() => onPick(on ? null : k)}
            style={{ paddingHorizontal: 10, paddingVertical: 5, borderRadius: 14, borderWidth: 1,
              borderColor: on ? C.ember : C.s3, backgroundColor: on ? C.s3 : "transparent" }}>
            <Text style={[s.label, { color: on ? C.ink : C.ink2 }]}>/{k.name}</Text>
          </Pressable>);
      })}
    </ScrollView>);
}

/* ------------------------------------------------------------------ attachments */
export async function pickAttachment(kind) {
  if (kind === "file") {
    const r = await DocumentPicker.getDocumentAsync({ copyToCacheDirectory: true, multiple: false });
    if (r.canceled || !r.assets?.[0]) return null;
    const a = r.assets[0];
    const isVideo = /^video\//.test(a.mimeType || "");
    const isImage = /^image\//.test(a.mimeType || "");
    return { id: newId(), kind: isVideo ? "video" : isImage ? "image" : "file",
             name: a.name, uri: a.uri, mime: a.mimeType || "" };
  }
  const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (!perm.granted) throw new Error("allow access to your photos in Settings");
  const r = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: kind === "video" ? ["videos"] : ["images"], quality: IMAGE_QUALITY,
    base64: kind !== "video", allowsMultipleSelection: false,
  });
  if (r.canceled || !r.assets?.[0]) return null;
  const a = r.assets[0];
  return { id: newId(), kind, name: a.fileName || (kind === "video" ? "video.mp4" : "photo.jpg"),
           uri: a.uri, base64: a.base64 || "", mime: a.mimeType || "" };
}

export function AttachRow({ items, onRemove, ui }) {
  const { s } = ui;
  if (!items.length) return null;
  const icon = { image: "Picture", video: "Video", file: "File" };
  return (
    <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6, paddingHorizontal: 10 }}>
      {items.map(a => (
        <Pressable key={a.id} onPress={() => onRemove(a.id)}
          style={{ flexDirection: "row", paddingHorizontal: 8, paddingVertical: 4, borderRadius: 8,
            backgroundColor: C.s2, borderWidth: 1, borderColor: C.s3 }}>
          <Text style={s.label}>{icon[a.kind]}: {a.name.slice(0, 22)}  ✕</Text>
        </Pressable>))}
    </View>);
}

/**
 * Turn what was typed plus what was attached into the text the phone's model reads. Text files
 * are read right here. Pictures and videos go to the PC only to be described; if there is no PC,
 * the model is told plainly what it could not see instead of guessing.
 */
export async function prepareTurn({ text, attachments, skill, linked, host, token, call }) {
  const extra = [], notes = [];
  const auth = { Authorization: `Bearer ${token}`, "X-ClipBot": "1" };

  for (const a of attachments.filter(x => x.kind === "file")) {
    if (TEXT_TYPES.test(a.name) || /^text\//.test(a.mime)) {
      const body = await FileSystem.readAsStringAsync(a.uri);
      const cut = body.length > FILE_TEXT_MAX;
      extra.push(`[Attached file: ${a.name}${cut ? " - first part only" : ""}]\n\`\`\`\n`
        + `${body.slice(0, FILE_TEXT_MAX)}\n\`\`\``);
    } else {
      notes.push(`${a.name}: this kind of file cannot be read on the phone yet - paste the text in instead.`);
    }
  }

  const pictures = attachments.filter(x => x.kind === "image");
  if (pictures.length) {
    if (!linked) {
      extra.push(`[The person attached ${pictures.length} picture(s), but you cannot see pictures. `
        + "Say so and ask them to describe it or connect the PC.]");
    } else {
      const images = [];
      for (const p of pictures) {       // a picture chosen from Files arrives without its data
        images.push(p.base64 || await FileSystem.readAsStringAsync(p.uri,
          { encoding: FileSystem.EncodingType.Base64 }));
      }
      const r = await call(host, "/api/assistant/look", { token, method: "POST",
        body: { images, question: text } });
      extra.push(r.ok ? `[What the attached picture shows: ${r.data.description}]`
                      : `[The picture could not be looked at: ${r.data.error || r.status}]`);
    }
  }

  const videos = attachments.filter(x => x.kind === "video");
  const link = (skill?.needsVideo || /watch/i.test(text)) ? (text.match(URL_RE) || [])[0] : null;
  if (videos.length || link) {
    if (!linked) {
      extra.push("[The person sent a video, but watching needs their PC connected. Say so.]");
    } else {
      let target = link;
      if (videos.length) {
        const up = await FileSystem.uploadAsync(
          `${host}/api/assistant/upload?name=${encodeURIComponent(videos[0].name)}`,
          videos[0].uri, { httpMethod: "POST", headers: auth, sessionType: 0,
                           uploadType: FileSystem.FileSystemUploadType.BINARY_CONTENT });
        const got = JSON.parse(up.body || "{}");
        if (up.status !== 200 || !got.id) throw new Error(got.detail || `upload failed (${up.status})`);
        target = `upload:${got.id}`;
      }
      const r = await call(host, "/api/assistant/watch", { token, method: "POST",
        body: { target }, timeout: WATCH_TIMEOUT_MS });
      extra.push(r.ok
        ? `[The video, watched for ${r.data.seconds_watched}s.\nWhat is said:\n${r.data.said}\n`
          + `What is on screen:\n${r.data.on_screen}]`
        : `[The video could not be watched: ${r.data.error || r.status}]`);
    }
  }
  return { content: [text, ...extra].filter(Boolean).join("\n\n"), notes };
}

/* ------------------------------------------------------------------ chat history */
export function History({ chats, current, onOpen, onNew, onDelete, ui }) {
  const { s, Btn } = ui;
  return (
    <ScrollView contentContainerStyle={{ padding: 14 }}>
      <Btn label="New chat" kind="primary" onPress={onNew} />
      {!chats.length && <Text style={[s.help, { marginTop: 14 }]}>No chats yet.</Text>}
      {chats.map(c => (
        <Pressable key={c.id} onPress={() => onOpen(c.id)}
          onLongPress={() => Alert.alert("Delete this chat?", c.title, [
            { text: "Cancel", style: "cancel" },
            { text: "Delete", style: "destructive", onPress: () => onDelete(c.id) }])}
          style={{ paddingVertical: 12, borderBottomWidth: 1, borderColor: C.s3 }}>
          <Text style={[s.monName, c.id === current && { color: C.ember }]}>{c.title}</Text>
          <Text style={s.help}>{new Date(c.updated).toLocaleString()} · {c.messages.length} messages</Text>
        </Pressable>))}
      {!!chats.length && <Text style={[s.help, { marginTop: 10 }]}>Hold a chat to delete it.</Text>}
    </ScrollView>);
}

/* ------------------------------------------------------------------ instructions, rules, skills */
export function BrainEditor({ brain, skills, onChange, ui }) {
  const { s, Btn, Plate } = ui;
  const [draft, setDraft] = useState({ name: "", description: "", body: "" });
  const [rule, setRule] = useState({ name: "", text: "" });
  const [importing, setImporting] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (patch) => onChange({ ...brain, ...patch });
  const field = (label, value, onText, lines = 3) => (
    <>
      <Text style={[s.label, { marginTop: 10 }]}>{label}</Text>
      <TextInput value={value} onChangeText={onText} multiline numberOfLines={lines}
        placeholderTextColor={C.ink3} style={[s.input, { minHeight: lines * 20, textAlignVertical: "top" }]} />
    </>);

  const addSkill = (sk) => {
    if (!sk.name || !sk.body.trim()) return Alert.alert("A skill needs a name and instructions.");
    if (skills.some(k => k.name === sk.name)) return Alert.alert(`There is already a /${sk.name}.`);
    set({ skills: [...brain.skills, { ...sk, id: sk.id || newId() }] });
  };
  const doImport = async () => {
    const src = importing.trim();
    if (!src) return;
    setBusy(true);
    try {
      addSkill(/^https?:\/\//i.test(src) ? await importSkillFromUrl(src) : parseSkillMd(src));
      setImporting("");
    } catch (e) { Alert.alert("Could not import", e.message); }
    setBusy(false);
  };

  return (
    <ScrollView contentContainerStyle={{ padding: 14, paddingBottom: 40 }}>
      <Plate>
        <Text style={s.label}>Your instructions</Text>
        <Text style={s.help}>Used in every chat. Tell it about you and how you like answers.</Text>
        {field("About you", brain.instructions.about,
          (t) => set({ instructions: { ...brain.instructions, about: t } }))}
        {field("How to respond", brain.instructions.style,
          (t) => set({ instructions: { ...brain.instructions, style: t } }))}
      </Plate>

      <Plate style={{ marginTop: 12 }}>
        <Text style={s.label}>Rules</Text>
        <Text style={s.help}>Short rules you can switch on and off. Every rule that is on applies to every chat.</Text>
        {brain.rules.map(r => (
          <View key={r.id} style={[s.row, { marginTop: 8 }]}>
            <Pressable style={{ flex: 1 }} onLongPress={() => set({ rules: brain.rules.filter(x => x.id !== r.id) })}>
              <Text style={s.monName}>{r.name}</Text><Text style={s.help}>{r.text}</Text>
            </Pressable>
            <Switch value={r.on} trackColor={{ true: C.ember, false: C.s4 }} thumbColor="#fff"
              onValueChange={(on) => set({ rules: brain.rules.map(x => x.id === r.id ? { ...x, on } : x) })} />
          </View>))}
        {field("New rule name", rule.name, (t) => setRule({ ...rule, name: t }), 1)}
        {field("The rule", rule.text, (t) => setRule({ ...rule, text: t }), 2)}
        <View style={{ marginTop: 8 }}>
          <Btn label="Add rule" onPress={() => {
            if (!rule.text.trim()) return;
            set({ rules: [...brain.rules, { id: newId(), name: rule.name.trim() || rule.text.slice(0, 30),
              text: rule.text.trim(), on: true }] });
            setRule({ name: "", text: "" });
          }} />
        </View>
        {!!brain.rules.length && <Text style={s.help}>Hold a rule to delete it.</Text>}
      </Plate>

      <Plate style={{ marginTop: 12 }}>
        <Text style={s.label}>Skills</Text>
        <Text style={s.help}>Type /name at the start of a message, or tap one above the box. Hold one of yours to delete it; switch a built-in one off to hide it.</Text>
        {skills.map(k => (
          <View key={k.id} style={[s.row, { marginTop: 8 }]}>
            <Pressable style={{ flex: 1 }} onLongPress={() => !k.starter
              && set({ skills: brain.skills.filter(x => x.id !== k.id) })}>
              <Text style={s.monName}>/{k.name}{k.starter ? "" : "  (yours)"}</Text>
              <Text style={s.help}>{k.description}</Text>
            </Pressable>
            {k.starter && (
              <Switch value trackColor={{ true: C.ember, false: C.s4 }} thumbColor="#fff"
                onValueChange={() => set({ hidden: [...brain.hidden, k.id] })} />)}
          </View>))}
        {!!brain.hidden.length && (
          <View style={{ marginTop: 8 }}>
            <Btn label={`Show ${brain.hidden.length} hidden built-in skill(s)`} onPress={() => set({ hidden: [] })} />
          </View>)}
      </Plate>

      <Plate style={{ marginTop: 12 }}>
        <Text style={s.label}>New skill</Text>
        {field("Name", draft.name, (t) => setDraft({ ...draft, name: t.toLowerCase().replace(/[^a-z0-9-]/g, "-") }), 1)}
        {field("What it's for (one line)", draft.description, (t) => setDraft({ ...draft, description: t }), 1)}
        {field("Instructions", draft.body, (t) => setDraft({ ...draft, body: t }), 6)}
        <View style={{ marginTop: 8 }}>
          <Btn label="Save skill" kind="primary" onPress={() => {
            addSkill({ ...draft, name: draft.name.replace(/^-+|-+$/g, "") });
            setDraft({ name: "", description: "", body: "" });
          }} />
        </View>
      </Plate>

      <Plate style={{ marginTop: 12 }}>
        <Text style={s.label}>Import a skill</Text>
        <Text style={s.help}>Paste a SKILL.md, or a link to one (GitHub links work). Skills written for other AI assistants work here too.</Text>
        {field("SKILL.md or link", importing, setImporting, 4)}
        <View style={{ marginTop: 8 }}><Btn label="Import" onPress={doImport} busy={busy} /></View>
      </Plate>
    </ScrollView>);
}
