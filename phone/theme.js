/**
 * The look, in one place. Warm near-black like anodised edit-suite hardware, the warm satin ink
 * of the Ashvane mark for text, and its ember as the one accent - used for "live", the selected
 * tab and the primary action, nowhere else. Depth comes from tonal steps and hairlines, never
 * shadows. Labels are sentence case; numbers, timecode and code are monospaced.
 */
import React from "react";
import { ActivityIndicator, Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";

export const C = {
  bg: "#0B0B0C", s1: "#151517", s2: "#1D1E21", s3: "#27282C", s4: "#323338",
  line: "rgba(255,255,255,0.07)", line2: "rgba(255,255,255,0.14)",
  ink: "#F3F0E9", ink2: "#B9B4AB", ink3: "#77736C",
  ember: "#F06834", emberSoft: "rgba(240,104,52,0.14)", emberLine: "rgba(240,104,52,0.45)",
  good: "#5FCB8E", goodSoft: "rgba(95,203,142,0.13)",
  bad: "#EC5B52", badSoft: "rgba(236,91,82,0.13)",
  warn: "#E8B04B",
};
export const MONO = Platform.select({ ios: "Menlo", android: "monospace", default: "ui-monospace, Menlo, monospace" });
export const GUTTER = 16;

export const t = StyleSheet.create({
  title: { color: C.ink, fontSize: 22, fontWeight: "700", letterSpacing: -0.3 },
  h2: { color: C.ink, fontSize: 17, fontWeight: "600", letterSpacing: -0.1 },
  body: { color: C.ink, fontSize: 15, lineHeight: 21 },
  muted: { color: C.ink2, fontSize: 13.5, lineHeight: 19 },
  faint: { color: C.ink3, fontSize: 12.5, lineHeight: 17 },
  label: { color: C.ink3, fontSize: 12.5, fontWeight: "600", letterSpacing: 0.2 },
  mono: { color: C.ink, fontFamily: MONO, fontSize: 13 },
  num: { color: C.ink, fontSize: 24, fontWeight: "700", fontVariant: ["tabular-nums"], letterSpacing: -0.5 },
  error: { color: C.bad, fontSize: 13.5, lineHeight: 19 },
});

/** A surface. `tone` steps it lighter; `pad={false}` for full-bleed lists. */
export function Card({ children, style, tone = 1, pad = true }) {
  return <View style={[st.card, { backgroundColor: tone === 2 ? C.s2 : C.s1 }, pad && st.cardPad, style]}>{children}</View>;
}

export function Section({ title, right, children, style }) {
  return (
    <View style={[{ marginBottom: 20 }, style]}>
      {(title || right) && (
        <View style={st.sectionHead}>
          <Text style={[t.label, { flex: 1 }]}>{title}</Text>
          {right}
        </View>)}
      {children}
    </View>);
}

export function Icon({ name, size = 20, color = C.ink2, style }) {
  return <Ionicons name={name} size={size} color={color} style={style} />;
}

/** kind: primary (ember), plain (tonal), ghost (text only), danger. */
export function Button({ label, icon, onPress, kind = "plain", busy, disabled, small, style, grow }) {
  const k = BTN[kind] || BTN.plain;
  return (
    <Pressable onPress={onPress} disabled={disabled || busy} accessibilityRole="button" accessibilityLabel={label}
      style={({ pressed }) => [st.btn, small && st.btnSmall, { backgroundColor: k.bg, borderColor: k.border },
        grow && { flex: 1 }, (disabled || busy) && { opacity: 0.45 }, pressed && { opacity: 0.78, transform: [{ scale: 0.985 }] }, style]}>
      {busy ? <ActivityIndicator color={k.ink} size="small" /> : (
        <>
          {!!icon && <Icon name={icon} size={small ? 16 : 18} color={k.ink} />}
          {!!label && <Text style={[st.btnText, small && { fontSize: 13.5 }, { color: k.ink }]}>{label}</Text>}
        </>)}
    </Pressable>);
}
const BTN = {
  primary: { bg: C.ember, border: C.ember, ink: "#1A0D06" },
  plain: { bg: C.s3, border: C.line, ink: C.ink },
  ghost: { bg: "transparent", border: "transparent", ink: C.ink2 },
  danger: { bg: C.badSoft, border: "rgba(236,91,82,0.35)", ink: C.bad },
};

export function IconButton({ icon, onPress, label, color = C.ink2, size = 20, style, disabled }) {
  return (
    <Pressable onPress={onPress} disabled={disabled} hitSlop={10} accessibilityRole="button" accessibilityLabel={label}
      style={({ pressed }) => [st.iconBtn, pressed && { backgroundColor: C.s3 }, disabled && { opacity: 0.35 }, style]}>
      <Icon name={icon} size={size} color={color} />
    </Pressable>);
}

/** A row of choices where exactly one is on. */
export function Segmented({ options, value, onChange, style }) {
  return (
    <View style={[st.seg, style]}>
      {options.map(([key, label]) => {
        const on = key === value;
        return (
          <Pressable key={key} onPress={() => onChange(key)} accessibilityRole="tab" accessibilityState={{ selected: on }}
            style={[st.segItem, on && st.segOn]}>
            <Text style={[st.segText, on && { color: C.ink }]} numberOfLines={1}>{label}</Text>
          </Pressable>);
      })}
    </View>);
}

export function Pill({ label, tone = "plain", icon }) {
  const p = PILL[tone] || PILL.plain;
  return (
    <View style={[st.pill, { backgroundColor: p.bg, borderColor: p.border }]}>
      {tone !== "plain" && !icon && <View style={[st.pillDot, { backgroundColor: p.ink }]} />}
      {!!icon && <Icon name={icon} size={12} color={p.ink} />}
      <Text style={[st.pillText, { color: p.ink }]} numberOfLines={1}>{label}</Text>
    </View>);
}
const PILL = {
  plain: { bg: C.s3, border: C.line, ink: C.ink2 },
  live: { bg: C.emberSoft, border: C.emberLine, ink: C.ember },
  good: { bg: C.goodSoft, border: "rgba(95,203,142,0.35)", ink: C.good },
  bad: { bg: C.badSoft, border: "rgba(236,91,82,0.35)", ink: C.bad },
};

export function Field({ label, style, inputStyle, mono, ...props }) {
  return (
    <View style={style}>
      {!!label && <Text style={[t.label, { marginBottom: 6 }]}>{label}</Text>}
      <TextInput placeholderTextColor={C.ink3} selectionColor={C.ember} cursorColor={C.ember}
        style={[st.input, mono && { fontFamily: MONO, fontSize: 14 }, props.multiline && { minHeight: 88, paddingTop: 12, textAlignVertical: "top" }, inputStyle]}
        {...props} />
    </View>);
}

export function Divider({ style }) { return <View style={[st.divider, style]} />; }

export function Empty({ icon = "sparkles-outline", title, body, children }) {
  return (
    <View style={st.empty}>
      <View style={st.emptyIcon}><Icon name={icon} size={22} color={C.ink2} /></View>
      {!!title && <Text style={[t.h2, { textAlign: "center" }]}>{title}</Text>}
      {!!body && <Text style={[t.muted, { textAlign: "center", marginTop: 6 }]}>{body}</Text>}
      {children}
    </View>);
}

/** A list row: icon, title, subtitle, and whatever sits on the right. */
export function ListRow({ icon, title, sub, right, onPress, onLongPress, first, danger }) {
  const body = (
    <View style={[st.row, !first && st.rowLine]}>
      {!!icon && <View style={st.rowIcon}><Icon name={icon} size={18} color={danger ? C.bad : C.ink2} /></View>}
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={[t.body, { fontWeight: "600" }, danger && { color: C.bad }]} numberOfLines={1}>{title}</Text>
        {!!sub && <Text style={[t.faint, { marginTop: 2 }]} numberOfLines={2}>{sub}</Text>}
      </View>
      {right}
    </View>);
  if (!onPress && !onLongPress) return body;
  return <Pressable onPress={onPress} onLongPress={onLongPress} style={({ pressed }) => pressed && { backgroundColor: C.s2 }}>{body}</Pressable>;
}

/** The shared style keys the chat pieces (Chat.js) were written against. */
export const chatStyles = StyleSheet.create({
  label: t.label,
  help: { ...t.faint, marginTop: 4 },
  monName: { color: C.ink, fontSize: 15, fontWeight: "600" },
  row: { flexDirection: "row", alignItems: "center", gap: 10 },
  input: { marginTop: 6, minHeight: 46, borderRadius: 12, paddingHorizontal: 14, backgroundColor: C.s2,
    color: C.ink, fontSize: 15.5, borderWidth: 1, borderColor: C.line },
  error: { ...t.error, marginTop: 10 },
  bubbleText: { color: C.ink, fontSize: 15.5, lineHeight: 22 },
  bubbleMineText: { color: C.ink, fontSize: 15.5, lineHeight: 22 },
});

const st = StyleSheet.create({
  card: { borderRadius: 16, borderWidth: 1, borderColor: C.line, overflow: "hidden" },
  cardPad: { padding: 16 },
  sectionHead: { flexDirection: "row", alignItems: "center", marginBottom: 8, paddingHorizontal: 4, minHeight: 22 },
  btn: { minHeight: 48, borderRadius: 12, paddingHorizontal: 18, flexDirection: "row", gap: 8,
    alignItems: "center", justifyContent: "center", borderWidth: 1 },
  btnSmall: { minHeight: 36, paddingHorizontal: 12, borderRadius: 10 },
  btnText: { fontSize: 15.5, fontWeight: "600" },
  iconBtn: { width: 38, height: 38, borderRadius: 10, alignItems: "center", justifyContent: "center" },
  seg: { flexDirection: "row", backgroundColor: C.s1, borderRadius: 12, padding: 3, borderWidth: 1, borderColor: C.line },
  segItem: { flex: 1, minHeight: 34, borderRadius: 9, alignItems: "center", justifyContent: "center", paddingHorizontal: 6 },
  segOn: { backgroundColor: C.s3 },
  segText: { color: C.ink3, fontSize: 13.5, fontWeight: "600" },
  pill: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 10, height: 26, borderRadius: 13, borderWidth: 1 },
  pillDot: { width: 6, height: 6, borderRadius: 3 },
  pillText: { fontSize: 12.5, fontWeight: "600" },
  input: { minHeight: 48, borderRadius: 12, paddingHorizontal: 14, backgroundColor: C.s2, color: C.ink,
    fontSize: 15.5, borderWidth: 1, borderColor: C.line },
  divider: { height: 1, backgroundColor: C.line },
  empty: { alignItems: "center", paddingVertical: 28, paddingHorizontal: 20 },
  emptyIcon: { width: 48, height: 48, borderRadius: 14, backgroundColor: C.s2, alignItems: "center",
    justifyContent: "center", marginBottom: 12, borderWidth: 1, borderColor: C.line },
  row: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingVertical: 13, minHeight: 56 },
  rowLine: { borderTopWidth: 1, borderTopColor: C.line },
  rowIcon: { width: 34, height: 34, borderRadius: 10, backgroundColor: C.s2, alignItems: "center", justifyContent: "center" },
});
