/**
 * Code: a small IDE on the phone.
 *
 *   Projects  folders of files that live on the phone
 *   Files     the project tree: open, add, rename, delete
 *   Editor    tabs, line numbers, a symbol row for the keys phones hide, autosave and undo
 *   Agent     describe a change; the model (the phone's own, or your PC's) proposes whole-file
 *             edits, each shown as a diff you apply or discard - nothing is written until you do
 *   Run       web projects render right here, JavaScript runs with its console shown
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Alert, KeyboardAvoidingView, Modal, Platform, Pressable, ScrollView,
  Share, Text, TextInput, useWindowDimensions, View } from "react-native";
import { Button, C, Card, Empty, Field, Icon, IconButton, ListRow, MONO, Pill, Segmented, t } from "../theme";
import { ModelCard } from "../useModel";
import { MessageBody } from "../Chat";
import { chatStyles } from "../theme";
import * as store from "./store";
import { AGENT_RULES, ASK_RULES, buildContext, countDiff, hunks, lineDiff, parseEdits } from "./agent";
import { buildPage, runPlan } from "./run";
import Preview from "./Preview";

const SAVE_DELAY_MS = 700;
const UNDO_MAX = 60;
const LINE_H = 20, FONT = 13, CHAR_W = 7.85;   // editor metrics: monospace, so width is predictable
const SYMBOLS = ["⇥", "{", "}", "(", ")", "[", "]", "<", ">", "=", ";", ":", "\"", "'", "`", "/", "+", "-", "*", "&", "|", "!", "$", "#", "_"];
const INDENT = "  ";
const CONSOLE_MAX = 300;
const HISTORY_SENT = 6;                         // earlier agent turns the model sees
const straighten = (s) => s.replace(/[‘’]/g, "'").replace(/[“”]/g, "\"");

/* ================================================================== projects */
export default function Code({ m }) {
  const [projects, setProjects] = useState(null);
  const [open, setOpen] = useState(null);
  const [creating, setCreating] = useState(false);

  const refresh = useCallback(() => store.listProjects().then(setProjects).catch(() => setProjects([])), []);
  useEffect(() => { refresh(); }, [refresh]);

  if (open) return <Workspace project={open} m={m} onBack={() => { setOpen(null); refresh(); }} />;

  return (
    <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 40 }}>
      <ModelCard m={m} compact />
      <View style={{ flexDirection: "row", alignItems: "center", marginBottom: 12 }}>
        <Text style={[t.title, { flex: 1 }]}>Projects</Text>
        <Button label="New" icon="add" kind="primary" small onPress={() => setCreating(true)} />
      </View>
      {projects === null ? <ActivityIndicator color={C.ink2} style={{ marginTop: 30 }} />
        : projects.length === 0 ? (
          <Card><Empty icon="code-slash-outline" title="No projects yet"
            body="Start one from a template. Files live on this phone; the agent can read and change them.">
            <View style={{ marginTop: 16, alignSelf: "stretch" }}>
              <Button label="New project" icon="add" kind="primary" onPress={() => setCreating(true)} />
            </View>
          </Empty></Card>)
        : (
          <Card pad={false}>
            {projects.map((p, i) => (
              <ListRow key={p.name} first={i === 0} icon="folder-open-outline" title={p.name}
                sub={`${p.files} file${p.files === 1 ? "" : "s"}${p.modified ? ` · edited ${new Date(p.modified).toLocaleDateString()}` : ""}`}
                right={<Icon name="chevron-forward" size={18} color={C.ink3} />}
                onPress={() => setOpen(p.name)}
                onLongPress={() => Alert.alert(`Delete ${p.name}?`, "Its files are removed from this phone.", [
                  { text: "Cancel", style: "cancel" },
                  { text: "Delete", style: "destructive", onPress: () => store.deleteProject(p.name).then(refresh) }])} />
            ))}
          </Card>)}
      {!!projects?.length && <Text style={[t.faint, { marginTop: 10, paddingHorizontal: 4 }]}>Hold a project to delete it.</Text>}
      <NewProject visible={creating} onClose={() => setCreating(false)}
        onMade={(name) => { setCreating(false); setOpen(name); }} />
    </ScrollView>);
}

function NewProject({ visible, onClose, onMade }) {
  const [name, setName] = useState("");
  const [tpl, setTpl] = useState("web");
  const [error, setError] = useState("");
  const make = async () => {
    try { onMade(await store.createProject(name, tpl)); setName(""); setError(""); }
    catch (e) { setError(e.message); }
  };
  return (
    <Sheet visible={visible} onClose={onClose} title="New project">
      <Field label="Name" value={name} onChangeText={setName} placeholder="my-app" autoCapitalize="none" autoCorrect={false} autoFocus />
      <Text style={[t.label, { marginTop: 16, marginBottom: 8 }]}>Start from</Text>
      {store.TEMPLATES.map(x => (
        <Pressable key={x.id} onPress={() => setTpl(x.id)}
          style={[{ flexDirection: "row", alignItems: "center", gap: 12, padding: 12, borderRadius: 12, marginBottom: 8,
            borderWidth: 1, borderColor: tpl === x.id ? C.emberLine : C.line, backgroundColor: tpl === x.id ? C.emberSoft : C.s2 }]}>
          <Icon name={x.icon} size={20} color={tpl === x.id ? C.ember : C.ink2} />
          <View style={{ flex: 1 }}>
            <Text style={[t.body, { fontWeight: "600" }]}>{x.name}</Text>
            <Text style={t.faint}>{x.blurb}</Text>
          </View>
        </Pressable>))}
      {!!error && <Text style={[t.error, { marginTop: 6 }]}>{error}</Text>}
      <Button label="Create" kind="primary" onPress={make} style={{ marginTop: 10 }} disabled={!name.trim()} />
    </Sheet>);
}

function Sheet({ visible, onClose, title, children }) {
  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1, justifyContent: "flex-end" }}>
        <Pressable style={{ flex: 1, backgroundColor: "rgba(0,0,0,0.55)" }} onPress={onClose} />
        <View style={{ backgroundColor: C.s1, borderTopLeftRadius: 20, borderTopRightRadius: 20, padding: 18,
          paddingBottom: 34, borderWidth: 1, borderColor: C.line, maxHeight: "88%" }}>
          <View style={{ flexDirection: "row", alignItems: "center", marginBottom: 14 }}>
            <Text style={[t.h2, { flex: 1 }]}>{title}</Text>
            <IconButton icon="close" label="Close" onPress={onClose} />
          </View>
          <ScrollView keyboardShouldPersistTaps="handled">{children}</ScrollView>
        </View>
      </KeyboardAvoidingView>
    </Modal>);
}

/* ================================================================== workspace */
function Workspace({ project, m, onBack }) {
  const [files, setFiles] = useState([]);
  const [tabs, setTabs] = useState([]);
  const [active, setActive] = useState(null);
  const [texts, setTexts] = useState({});
  const [dirty, setDirty] = useState({});
  const [pane, setPane] = useState("files");
  const [logs, setLogs] = useState([]);
  const [runKey, setRunKey] = useState(0);
  const saveTimers = useRef({});
  const undo = useRef({});

  const reload = useCallback(async () => {
    const list = await store.tree(project);
    setFiles(list);
    return list;
  }, [project]);

  useEffect(() => {
    reload().then(list => {
      const first = list.find(f => f.path === "index.html") || list.find(f => !f.dir);
      if (first) openFile(first.path, false);
    });
    return () => Object.values(saveTimers.current).forEach(clearTimeout);
  }, [reload]);   // eslint-disable-line react-hooks/exhaustive-deps

  const readText = async (path) => {
    if (texts[path] !== undefined) return texts[path];
    const body = await store.readFile(project, path);
    const text = body.length > store.TEXT_MAX ? body.slice(0, store.TEXT_MAX) : body;
    setTexts(prev => ({ ...prev, [path]: text }));
    return text;
  };

  const openFile = async (path, show = true) => {
    try { await readText(path); }
    catch (e) { return Alert.alert("Could not open", e.message); }
    setTabs(prev => prev.includes(path) ? prev : [...prev, path]);
    setActive(path);
    if (show) setPane("edit");
  };

  const closeTab = (path) => {
    setTabs(prev => {
      const next = prev.filter(p => p !== path);
      if (active === path) setActive(next[next.length - 1] || null);
      return next;
    });
  };

  const edit = (path, text, remember = true) => {
    if (remember) {
      const stack = undo.current[path] || (undo.current[path] = []);
      if (texts[path] !== undefined && stack[stack.length - 1] !== texts[path]) {
        stack.push(texts[path]);
        if (stack.length > UNDO_MAX) stack.shift();
      }
    }
    setTexts(prev => ({ ...prev, [path]: text }));
    setDirty(prev => ({ ...prev, [path]: true }));
    clearTimeout(saveTimers.current[path]);
    saveTimers.current[path] = setTimeout(async () => {
      await store.writeFile(project, path, text);
      setDirty(prev => ({ ...prev, [path]: false }));
    }, SAVE_DELAY_MS);
  };

  const undoLast = (path) => {
    const stack = undo.current[path] || [];
    if (!stack.length) return;
    edit(path, stack.pop(), false);
  };

  /** Write a proposed change for real. */
  const applyEdit = async (e) => {
    if (e.deleted) {
      await store.deletePath(project, e.path);
      closeTab(e.path);
      setTexts(prev => { const n = { ...prev }; delete n[e.path]; return n; });
    } else {
      await store.writeFile(project, e.path, e.content);
      if (texts[e.path] !== undefined) {
        const stack = undo.current[e.path] || (undo.current[e.path] = []);
        stack.push(texts[e.path]);
      }
      setTexts(prev => ({ ...prev, [e.path]: e.content }));
    }
    await reload();
  };

  const run = () => { setLogs([]); setRunKey(k => k + 1); setPane("run"); };

  const allTexts = async () => {
    const out = { ...texts };
    for (const f of files) {
      if (f.dir || out[f.path] !== undefined || (f.size || 0) > store.TEXT_MAX) continue;
      try { out[f.path] = await store.readFile(project, f.path); } catch { /* unreadable: skip */ }
    }
    return out;
  };

  return (
    <View style={{ flex: 1 }}>
      <View style={{ flexDirection: "row", alignItems: "center", paddingHorizontal: 8, paddingTop: 2, gap: 4 }}>
        <IconButton icon="chevron-back" label="Projects" onPress={onBack} color={C.ink} />
        <Text style={[t.h2, { flex: 1 }]} numberOfLines={1}>{project}</Text>
        <Button label="Run" icon="play" kind="primary" small onPress={run} />
      </View>
      <Segmented style={{ marginHorizontal: 16, marginTop: 10, marginBottom: 10 }} value={pane} onChange={setPane}
        options={[["files", "Files"], ["edit", "Editor"], ["agent", "Agent"], ["run", "Run"]]} />
      {pane === "files" && <FilesPane project={project} files={files} active={active} dirty={dirty}
        onOpen={openFile} reload={reload} onClosed={closeTab} />}
      {pane === "edit" && <EditorPane tabs={tabs} active={active} setActive={setActive} texts={texts} dirty={dirty}
        onEdit={edit} onClose={closeTab} onUndo={undoLast} onFiles={() => setPane("files")} />}
      {pane === "agent" && <AgentPane project={project} m={m} files={files} active={active}
        allTexts={allTexts} applyEdit={applyEdit} openFile={openFile} />}
      {pane === "run" && <RunPane files={files} active={active} allTexts={allTexts} runKey={runKey}
        logs={logs} onConsole={(e) => setLogs(prev => [...prev.slice(-CONSOLE_MAX), { ...e, at: Date.now() }])}
        onRerun={run} />}
    </View>);
}

/* ------------------------------------------------------------------ files */
function FilesPane({ project, files, active, dirty, onOpen, reload, onClosed }) {
  const [adding, setAdding] = useState(null);      // "file" | "folder" | {rename: path}
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  const submit = async () => {
    const clean = store.cleanPath(name);
    if (!clean) return setError("type a name");
    try {
      if (adding?.rename) {
        if (await store.exists(project, clean)) return setError("that name is taken");
        await store.movePath(project, adding.rename, clean);
        onClosed(adding.rename);
      } else if (adding === "folder") {
        await store.makeFolder(project, clean);
      } else {
        if (await store.exists(project, clean)) return setError("that file already exists");
        await store.writeFile(project, clean, "");
      }
      setAdding(null); setName(""); setError("");
      await reload();
      if (adding === "file") onOpen(clean);
    } catch (e) { setError(e.message); }
  };

  const menu = (f) => Alert.alert(f.path, "", [
    { text: "Rename", onPress: () => { setAdding({ rename: f.path }); setName(f.path); } },
    { text: "Delete", style: "destructive", onPress: async () => {
      await store.deletePath(project, f.path); onClosed(f.path); reload(); } },
    { text: "Cancel", style: "cancel" }]);

  return (
    <ScrollView contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 40 }}>
      <View style={{ flexDirection: "row", gap: 8, marginBottom: 12 }}>
        <Button label="New file" icon="document-outline" small grow onPress={() => { setAdding("file"); setName(""); }} />
        <Button label="New folder" icon="folder-outline" small grow onPress={() => { setAdding("folder"); setName(""); }} />
      </View>
      {files.length === 0 ? <Card><Empty icon="document-outline" title="Empty project" body="Add a file, or ask the agent to make some." /></Card> : (
        <Card pad={false}>
          {files.map((f, i) => (
            <Pressable key={f.path} onPress={() => !f.dir && onOpen(f.path)} onLongPress={() => menu(f)}
              style={({ pressed }) => [{ flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 11,
                paddingRight: 14, paddingLeft: 14 + f.depth * 16, borderTopWidth: i ? 1 : 0, borderTopColor: C.line },
                f.path === active && { backgroundColor: C.s2 }, pressed && { backgroundColor: C.s3 }]}>
              <Icon name={store.iconOf(f.path, f.dir)} size={17} color={f.dir ? C.ink3 : f.path === active ? C.ember : C.ink2} />
              <Text style={[t.mono, { flex: 1, fontSize: 13.5 }, f.dir && { color: C.ink2 }]} numberOfLines={1}>
                {f.path.split("/").pop()}{f.dir ? "/" : ""}</Text>
              {dirty[f.path] && <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: C.ember }} />}
              {!f.dir && <Text style={t.faint}>{fmtSize(f.size)}</Text>}
            </Pressable>))}
        </Card>)}
      <Text style={[t.faint, { marginTop: 10, paddingHorizontal: 4 }]}>Tap to open. Hold to rename or delete.</Text>
      <Sheet visible={!!adding} onClose={() => { setAdding(null); setError(""); }}
        title={adding?.rename ? "Rename" : adding === "folder" ? "New folder" : "New file"}>
        <Field label={adding === "folder" ? "Folder" : "Path"} value={name} onChangeText={setName} mono autoFocus
          placeholder={adding === "folder" ? "src" : "src/app.js"} autoCapitalize="none" autoCorrect={false} onSubmitEditing={submit} />
        {!!error && <Text style={[t.error, { marginTop: 8 }]}>{error}</Text>}
        <Button label={adding?.rename ? "Rename" : "Create"} kind="primary" onPress={submit} style={{ marginTop: 14 }} />
      </Sheet>
    </ScrollView>);
}

const fmtSize = (n = 0) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;

/* ------------------------------------------------------------------ editor */
function EditorPane({ tabs, active, setActive, texts, dirty, onEdit, onClose, onUndo, onFiles }) {
  const { width } = useWindowDimensions();
  const sel = useRef({ start: 0, end: 0 });
  const [forced, setForced] = useState(null);
  const text = active ? texts[active] ?? "" : "";
  const lines = useMemo(() => text.split("\n"), [text]);
  const gutterW = 18 + String(lines.length).length * CHAR_W;
  const longest = lines.reduce((mx, l) => Math.max(mx, l.length), 0);
  const inputW = Math.max(width - gutterW - 34, longest * CHAR_W + 40);

  if (!active) {
    return <View style={{ padding: 16 }}><Card><Empty icon="create-outline" title="No file open"
      body="Open a file from Files to edit it here."><View style={{ marginTop: 14, alignSelf: "stretch" }}>
        <Button label="Browse files" icon="folder-open-outline" onPress={onFiles} /></View></Empty></Card></View>;
  }

  const insert = (sym) => {
    const piece = sym === "⇥" ? INDENT : sym;
    const { start, end } = sel.current;
    const next = text.slice(0, start) + piece + text.slice(end);
    onEdit(active, next);
    const at = start + piece.length;
    sel.current = { start: at, end: at };
    setForced({ start: at, end: at });
  };

  return (
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} keyboardVerticalOffset={120} style={{ flex: 1 }}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ flexGrow: 0 }}
        contentContainerStyle={{ paddingHorizontal: 12, gap: 6, paddingBottom: 8 }}>
        {tabs.map(p => {
          const on = p === active;
          return (
            <Pressable key={p} onPress={() => setActive(p)}
              style={{ flexDirection: "row", alignItems: "center", gap: 6, height: 32, paddingLeft: 10, paddingRight: 4,
                borderRadius: 9, backgroundColor: on ? C.s3 : C.s1, borderWidth: 1, borderColor: on ? C.line2 : C.line }}>
              {dirty[p] ? <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: C.ember }} /> : null}
              <Text style={[t.mono, { fontSize: 12.5, color: on ? C.ink : C.ink2 }]}>{p.split("/").pop()}</Text>
              <Pressable onPress={() => onClose(p)} hitSlop={8} style={{ padding: 4 }}>
                <Icon name="close" size={13} color={C.ink3} />
              </Pressable>
            </Pressable>);
        })}
      </ScrollView>
      <View style={{ flex: 1, marginHorizontal: 12, borderRadius: 14, borderWidth: 1, borderColor: C.line, backgroundColor: "#0E0E10", overflow: "hidden" }}>
        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingVertical: 10 }}>
          <View style={{ flexDirection: "row" }}>
            <Text style={{ width: gutterW, paddingRight: 8, marginRight: 10, borderRightWidth: 1, borderRightColor: C.line, textAlign: "right", fontFamily: MONO, fontSize: FONT,
              lineHeight: LINE_H, color: C.ink3, paddingTop: Platform.OS === "ios" ? 0 : 1 }} selectable={false}>
              {lines.map((_, i) => i + 1).join("\n")}
            </Text>
            <ScrollView horizontal showsHorizontalScrollIndicator keyboardShouldPersistTaps="handled">
              <TextInput value={text} multiline scrollEnabled={false}
                onChangeText={(v) => onEdit(active, straighten(v))}
                onSelectionChange={(e) => { sel.current = e.nativeEvent.selection; if (forced) setForced(null); }}
                selection={forced || undefined}
                autoCapitalize="none" autoCorrect={false} spellCheck={false} autoComplete="off"
                keyboardType={Platform.OS === "ios" ? "ascii-capable" : "visible-password"}
                selectionColor={C.ember} cursorColor={C.ember}
                style={{ width: inputW, color: C.ink, fontFamily: MONO, fontSize: FONT, lineHeight: LINE_H,
                  padding: 0, paddingRight: 16, textAlignVertical: "top", minHeight: 300 }} />
            </ScrollView>
          </View>
        </ScrollView>
      </View>
      <View style={{ flexDirection: "row", alignItems: "center", paddingHorizontal: 8, paddingVertical: 6, gap: 2 }}>
        <IconButton icon="arrow-undo-outline" label="Undo" onPress={() => onUndo(active)} />
        <IconButton icon="share-outline" label="Share file" onPress={() => Share.share({ message: text, title: active })} />
        <ScrollView horizontal showsHorizontalScrollIndicator={false} keyboardShouldPersistTaps="always"
          contentContainerStyle={{ gap: 4, paddingHorizontal: 4 }}>
          {SYMBOLS.map(sym => (
            <Pressable key={sym} onPress={() => insert(sym)}
              style={({ pressed }) => ({ minWidth: 34, height: 34, borderRadius: 8, alignItems: "center", justifyContent: "center",
                backgroundColor: pressed ? C.s4 : C.s2, borderWidth: 1, borderColor: C.line })}>
              <Text style={[t.mono, { fontSize: 15 }]}>{sym}</Text>
            </Pressable>))}
        </ScrollView>
      </View>
      <Text style={[t.faint, { textAlign: "center", paddingBottom: 6 }]}>
        {dirty[active] ? "Saving…" : "Saved"} · {lines.length} line{lines.length === 1 ? "" : "s"}
      </Text>
    </KeyboardAvoidingView>);
}

/* ------------------------------------------------------------------ agent */
function AgentPane({ project, m, files, active, allTexts, applyEdit, openFile }) {
  const [turns, setTurns] = useState(null);
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState("edit");
  const [busy, setBusy] = useState(false);
  const [streaming, setStreaming] = useState("");
  const [error, setError] = useState("");
  const scroller = useRef(null);
  const ui = { s: chatStyles, C };

  useEffect(() => { store.loadAgent(project).then(d => setTurns(d.turns || [])); }, [project]);
  const persist = (next) => { setTurns(next); store.saveAgent(project, { turns: next }).catch(() => {}); };

  const send = async () => {
    const ask = draft.trim();
    if (!ask || busy) return;
    setDraft(""); setBusy(true); setError(""); setStreaming("");
    const base = [...turns, { role: "user", content: ask, mode }];
    persist(base);
    try {
      const texts = await allTexts();
      const system = buildContext(mode === "edit" ? AGENT_RULES : ASK_RULES, project, files, active, texts);
      const history = turns.slice(-HISTORY_SENT).map(x => ({ role: x.role, content: x.raw || x.content }));
      let sofar = "";
      const answer = await m.ask([...history, { role: "user", content: ask }], system,
        (tok) => { sofar += tok; setStreaming(sofar); }, true);
      const { edits, prose } = parseEdits(answer);
      const withBefore = edits.map(e => ({ ...e, before: texts[e.path] ?? null, status: "pending" }));
      persist([...base, { role: "assistant", content: prose || (edits.length ? "Here are the changes." : answer),
        raw: answer, edits: withBefore, via: m.where }]);
    } catch (e) {
      setError(e.message);
    }
    setStreaming(""); setBusy(false);
  };

  const setStatus = (ti, path, status) => {
    persist(turns.map((x, i) => i !== ti ? x : { ...x, edits: x.edits.map(e => e.path === path ? { ...e, status } : e) }));
  };
  const apply = async (ti, e) => {
    try { await applyEdit(e); setStatus(ti, e.path, "applied"); }
    catch (err) { Alert.alert("Could not apply", err.message); }
  };
  const applyAll = async (ti) => {
    const turn = turns[ti];
    let next = turns;
    for (const e of turn.edits.filter(x => x.status === "pending")) {
      try {
        await applyEdit(e);
        next = next.map((x, i) => i !== ti ? x : { ...x, edits: x.edits.map(y => y.path === e.path ? { ...y, status: "applied" } : y) });
      } catch (err) { Alert.alert("Could not apply", `${e.path}: ${err.message}`); }
    }
    persist(next);
  };

  if (turns === null) return <ActivityIndicator color={C.ink2} style={{ marginTop: 30 }} />;
  return (
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} keyboardVerticalOffset={120} style={{ flex: 1 }}>
      <ScrollView ref={scroller} contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 12 }}
        onContentSizeChange={() => scroller.current?.scrollToEnd({ animated: true })}>
        <ModelCard m={m} compact />
        {turns.length === 0 && !busy && (
          <Card><Empty icon="sparkles-outline" title="Your coding agent"
            body={"Describe what to build or change. It reads your files, proposes edits as diffs, and you choose what to apply."
              + (active ? `\n\nIt can see ${active} and the other small files.` : "")} /></Card>)}
        {turns.map((x, i) => x.role === "user" ? (
          <View key={i} style={{ alignSelf: "flex-end", maxWidth: "88%", backgroundColor: C.s3, borderRadius: 16,
            borderBottomRightRadius: 6, paddingHorizontal: 14, paddingVertical: 10, marginVertical: 8 }}>
            {x.mode === "ask" && <Text style={[t.label, { marginBottom: 2 }]}>Question</Text>}
            <Text style={t.body}>{x.content}</Text>
          </View>
        ) : (
          <View key={i} style={{ marginVertical: 8 }}>
            <MessageBody text={x.content} ui={ui} />
            {(x.edits || []).map(e => <EditCard key={e.path} e={e} onApply={() => apply(i, e)}
              onDiscard={() => setStatus(i, e.path, "discarded")} onOpen={() => openFile(e.path)} />)}
            {(x.edits || []).filter(e => e.status === "pending").length > 1 && (
              <Button label="Apply all" icon="checkmark-done" kind="primary" small onPress={() => applyAll(i)} style={{ marginTop: 4 }} />)}
            <Text style={[t.faint, { marginTop: 6 }]}>{x.via === "phone" ? "Answered on this phone" : x.via === "pc" ? "Answered by your PC" : ""}</Text>
          </View>))}
        {busy && (
          <View style={{ marginVertical: 8 }}>
            {streaming ? <Text style={[t.mono, { color: C.ink2, fontSize: 12.5 }]}>{streaming.slice(-1600)}</Text>
              : <View style={{ flexDirection: "row", gap: 10, alignItems: "center" }}>
                  <ActivityIndicator color={C.ember} /><Text style={t.muted}>Reading your project…</Text></View>}
          </View>)}
        {!!error && <Text style={[t.error, { marginTop: 8 }]}>{error}</Text>}
      </ScrollView>
      <View style={{ paddingHorizontal: 12, paddingTop: 8, paddingBottom: 10, borderTopWidth: 1, borderTopColor: C.line, backgroundColor: C.bg }}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 8 }}>
          <Segmented style={{ width: 170 }} value={mode} onChange={setMode} options={[["edit", "Edit files"], ["ask", "Ask"]]} />
          <View style={{ flex: 1 }} />
          {!!active && <Pill label={active.split("/").pop()} icon="document-text-outline" />}
        </View>
        <View style={{ flexDirection: "row", alignItems: "flex-end", gap: 8 }}>
          <TextInput value={draft} onChangeText={(v) => setDraft(straighten(v))} multiline
            placeholder={mode === "edit" ? "Add a dark mode toggle to the page…" : "Why does this crash when…"}
            placeholderTextColor={C.ink3} selectionColor={C.ember}
            style={{ flex: 1, minHeight: 46, maxHeight: 140, borderRadius: 14, paddingHorizontal: 14, paddingTop: 12, paddingBottom: 12,
              backgroundColor: C.s2, color: C.ink, fontSize: 15.5, borderWidth: 1, borderColor: C.line }} />
          <Pressable onPress={send} disabled={busy || !draft.trim()} accessibilityLabel="Send"
            style={({ pressed }) => ({ width: 46, height: 46, borderRadius: 14, alignItems: "center", justifyContent: "center",
              backgroundColor: busy || !draft.trim() ? C.s3 : C.ember, opacity: pressed ? 0.8 : 1 })}>
            {busy ? <ActivityIndicator color={C.ink} /> : <Icon name="arrow-up" size={22} color={draft.trim() ? "#1A0D06" : C.ink3} />}
          </Pressable>
        </View>
      </View>
    </KeyboardAvoidingView>);
}

function EditCard({ e, onApply, onDiscard, onOpen }) {
  const [open, setOpen] = useState(e.status === "pending");
  const diff = useMemo(() => lineDiff(e.before, e.deleted ? null : e.content), [e]);
  const n = countDiff(diff);
  const shown = useMemo(() => hunks(diff), [diff]);
  const state = e.status === "applied" ? ["Applied", "good"] : e.status === "discarded" ? ["Discarded", "plain"]
    : [e.deleted ? "Delete" : e.before == null ? "New file" : "Change", "live"];
  return (
    <Card pad={false} style={{ marginTop: 10 }}>
      <Pressable onPress={() => setOpen(!open)} style={{ flexDirection: "row", alignItems: "center", gap: 10, padding: 12 }}>
        <Icon name={store.iconOf(e.path)} size={17} color={C.ink2} />
        <Text style={[t.mono, { flex: 1, fontSize: 13 }]} numberOfLines={1}>{e.path}</Text>
        {!e.deleted && <Text style={[t.mono, { color: C.good, fontSize: 12 }]}>+{n.add}</Text>}
        <Text style={[t.mono, { color: C.bad, fontSize: 12 }]}>−{n.del}</Text>
        <Pill label={state[0]} tone={state[1]} />
      </Pressable>
      {open && (
        <ScrollView horizontal style={{ borderTopWidth: 1, borderTopColor: C.line, maxHeight: 340 }}>
          <ScrollView nestedScrollEnabled style={{ maxHeight: 340 }}>
            <View style={{ paddingVertical: 6 }}>
              {shown.map((d, i) => (
                <Text key={i} style={{ fontFamily: MONO, fontSize: 12, lineHeight: 18, paddingHorizontal: 12,
                  color: d.t === "+" ? "#BFF0D2" : d.t === "-" ? "#FFC7C2" : d.t === "…" ? C.ink3 : C.ink2,
                  backgroundColor: d.t === "+" ? "rgba(95,203,142,0.10)" : d.t === "-" ? "rgba(236,91,82,0.10)" : "transparent" }}>
                  {d.t === "…" ? "  ⋯" : `${d.t} ${d.s}`}
                </Text>))}
            </View>
          </ScrollView>
        </ScrollView>)}
      {e.status === "pending" ? (
        <View style={{ flexDirection: "row", gap: 8, padding: 12, borderTopWidth: 1, borderTopColor: C.line }}>
          <Button label="Discard" small grow onPress={onDiscard} />
          <Button label={e.deleted ? "Delete file" : "Apply"} icon="checkmark" kind={e.deleted ? "danger" : "primary"} small grow onPress={onApply} />
        </View>
      ) : e.status === "applied" && !e.deleted ? (
        <View style={{ padding: 10, borderTopWidth: 1, borderTopColor: C.line }}>
          <Button label="Open in editor" icon="create-outline" kind="ghost" small onPress={onOpen} />
        </View>) : null}
    </Card>);
}

/* ------------------------------------------------------------------ run */
function RunPane({ files, active, allTexts, runKey, logs, onConsole, onRerun }) {
  const [html, setHtml] = useState(null);
  const plan = useMemo(() => runPlan(files, active), [files, active]);
  const [split, setSplit] = useState("both");

  useEffect(() => {
    let alive = true;
    if (!plan || plan.kind === "python") { setHtml(null); return undefined; }
    allTexts().then(texts => { if (alive) setHtml(buildPage(plan, texts)); });
    return () => { alive = false; };
  }, [runKey, plan]);   // eslint-disable-line react-hooks/exhaustive-deps

  if (!plan) {
    return <View style={{ padding: 16 }}><Card><Empty icon="play-outline" title="Nothing to run"
      body="Add an index.html for a web page, or open a .js file to run it." /></Card></View>;
  }
  if (plan.kind === "python") {
    return <View style={{ padding: 16 }}><Card><Empty icon="logo-python" title="Python runs on a computer"
      body="Phones can't run Python apps. Write and fix it here with the agent, then share the files to your PC and run them there." /></Card></View>;
  }
  const colors = { error: C.bad, warn: C.warn, info: C.ink2, debug: C.ink3, log: C.ink };
  return (
    <View style={{ flex: 1, paddingHorizontal: 12 }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <Pill label={plan.kind === "web" ? plan.entry : `${plan.entry} · JavaScript`} icon={plan.kind === "web" ? "globe-outline" : "logo-javascript"} />
        <View style={{ flex: 1 }} />
        <Segmented style={{ width: 150 }} value={split} onChange={setSplit} options={[["both", "Both"], ["console", "Console"]]} />
        <IconButton icon="refresh" label="Run again" onPress={onRerun} />
      </View>
      {split === "both" && (
        <View style={{ flex: plan.kind === "web" ? 3 : 1, borderRadius: 14, overflow: "hidden", borderWidth: 1, borderColor: C.line, backgroundColor: C.bg }}>
          {html ? <Preview html={html} runKey={runKey} onConsole={onConsole} /> : <ActivityIndicator color={C.ink2} style={{ marginTop: 30 }} />}
        </View>)}
      {split === "console" && html && <View style={{ height: 1, overflow: "hidden" }}><Preview html={html} runKey={runKey} onConsole={onConsole} /></View>}
      <View style={{ flex: 2, marginTop: 10, marginBottom: 10, borderRadius: 14, borderWidth: 1, borderColor: C.line, backgroundColor: "#0E0E10" }}>
        <View style={{ flexDirection: "row", alignItems: "center", paddingHorizontal: 12, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: C.line }}>
          <Icon name="terminal-outline" size={15} color={C.ink3} />
          <Text style={[t.label, { flex: 1, marginLeft: 8 }]}>Console</Text>
          <Text style={t.faint}>{logs.length}</Text>
        </View>
        <ScrollView contentContainerStyle={{ padding: 12 }}>
          {logs.length === 0 ? <Text style={t.faint}>Nothing printed yet.</Text> : logs.map((l, i) => (
            <Text key={i} selectable style={{ fontFamily: MONO, fontSize: 12.5, lineHeight: 18, color: colors[l.kind] || C.ink, marginBottom: 2 }}>
              {l.kind === "error" ? "✕ " : l.kind === "warn" ? "! " : "› "}{l.text}
            </Text>))}
        </ScrollView>
      </View>
    </View>);
}
