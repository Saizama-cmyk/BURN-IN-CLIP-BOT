/**
 * The lock: Face ID, Touch ID or the passcode on iPhone; fingerprint, face unlock or the PIN on
 * Android. The phone's own security does the checking - this app never sees or stores any of it.
 *
 * It asks when the app opens and again when you come back after being away for a while. The
 * short grace period is there because attaching a photo or a file briefly sends the app to the
 * background on Android, and locking in the middle of that would be maddening. A phone with no
 * passcode set at all cannot be protected this way, so it is simply not locked.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { AppState, Image, Pressable, Text, View } from "react-native";
import * as LocalAuthentication from "expo-local-authentication";

const LOCK_AFTER_MS = 60000;      // away longer than this and it asks again

export function useLock() {
  const [locked, setLocked] = useState(true);
  const [protectable, setProtectable] = useState(true);
  const [problem, setProblem] = useState("");
  const leftAt = useRef(0);
  const asking = useRef(false);

  const unlock = useCallback(async () => {
    if (asking.current) return;
    asking.current = true;
    try {
      const level = await LocalAuthentication.getEnrolledLevelAsync();
      if (level === LocalAuthentication.SecurityLevel.NONE) {
        setProtectable(false); setLocked(false); return;   // no passcode on this phone at all
      }
      const r = await LocalAuthentication.authenticateAsync({
        promptMessage: "Unlock", fallbackLabel: "Use passcode",
        cancelLabel: "Cancel", disableDeviceFallback: false,
      });
      if (r.success) { setLocked(false); setProblem(""); }
      else if (r.error && r.error !== "user_cancel" && r.error !== "app_cancel") setProblem(r.error);
    } catch (e) {
      setProblem(e.message);
    } finally {
      asking.current = false;
    }
  }, []);

  useEffect(() => { unlock(); }, [unlock]);

  useEffect(() => {
    const sub = AppState.addEventListener("change", (state) => {
      if (state === "background") leftAt.current = Date.now();
      if (state === "active" && leftAt.current && protectable
          && Date.now() - leftAt.current > LOCK_AFTER_MS) {
        leftAt.current = 0;
        setLocked(true);
        unlock();
      }
    });
    return () => sub.remove();
  }, [protectable, unlock]);

  return { locked, unlock, problem };
}

export function LockScreen({ onUnlock, problem, ui }) {
  const { s, C } = ui;
  return (
    <View style={[s.screen, { alignItems: "center", justifyContent: "center", padding: 30 }]}>
      <Image source={require("./assets/icon.png")} style={{ width: 96, height: 96, borderRadius: 22 }} />
      <Text style={[s.signTitle, { marginTop: 18 }]}>Locked</Text>
      <Text style={[s.help, { textAlign: "center", marginTop: 6 }]}>
        Unlock with Face ID, your fingerprint or your passcode.
      </Text>
      <Pressable onPress={onUnlock} style={{ marginTop: 22, paddingHorizontal: 28, paddingVertical: 12,
        borderRadius: 12, backgroundColor: C.chrome }}>
        <Text style={{ color: C.base, fontWeight: "700", letterSpacing: 1 }}>UNLOCK</Text>
      </Pressable>
      {!!problem && <Text style={[s.error, { marginTop: 14, textAlign: "center" }]}>{problem}</Text>}
    </View>);
}
