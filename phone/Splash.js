/**
 * The launch splash, every time the app starts: the Ashvane mark settles in, an ember line
 * fills, and it lifts away to whatever the app is showing underneath (sign-in, lock, or Desk).
 * The system splash in app.json only covers the moment the app is loading; this is the real one.
 */
import React, { useEffect, useRef } from "react";
import { Animated, Easing, Image, StyleSheet, Text, View } from "react-native";
import { C } from "./theme";

const MARK = require("./assets/splash.png");
const IN_MS = 520;          // the mark settling in
const FILL_MS = 1100;       // the ember line
const OUT_MS = 320;         // lifting away
const BAR_W = 160;

export default function Splash({ onDone }) {
  const mark = useRef(new Animated.Value(0)).current;
  const fill = useRef(new Animated.Value(0)).current;
  const out = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    const ease = Easing.bezier(0.2, 0.8, 0.2, 1);
    const run = Animated.sequence([
      Animated.timing(mark, { toValue: 1, duration: IN_MS, easing: ease, useNativeDriver: true }),
      Animated.timing(fill, { toValue: 1, duration: FILL_MS, easing: ease, useNativeDriver: true }),
      Animated.timing(out, { toValue: 1, duration: OUT_MS, easing: Easing.in(Easing.quad), useNativeDriver: true }),
    ]);
    run.start(({ finished }) => finished && onDone());
    return () => run.stop();
  }, []);

  return (
    <Animated.View pointerEvents="none" style={[StyleSheet.absoluteFill, s.wrap, {
      opacity: out.interpolate({ inputRange: [0, 1], outputRange: [1, 0] }),
      transform: [{ scale: out.interpolate({ inputRange: [0, 1], outputRange: [1, 1.04] }) }] }]}>
      <Animated.View style={{ alignItems: "center", opacity: mark,
        transform: [{ scale: mark.interpolate({ inputRange: [0, 1], outputRange: [0.9, 1] }) }] }}>
        <Image source={MARK} style={s.mark} resizeMode="contain" />
        <Text style={s.name}>ASHVANE</Text>
        <View style={s.track}>
          <Animated.View style={[s.bar, { transform: [{
            translateX: fill.interpolate({ inputRange: [0, 1], outputRange: [-BAR_W, 0] }) }] }]} />
        </View>
      </Animated.View>
    </Animated.View>);
}

const s = StyleSheet.create({
  wrap: { backgroundColor: "#070708", alignItems: "center", justifyContent: "center", zIndex: 100 },
  mark: { width: 220, height: 220 },
  name: { color: C.ink, fontSize: 26, fontWeight: "700", letterSpacing: 8, marginTop: 4, marginLeft: 8 },
  track: { width: BAR_W, height: 3, borderRadius: 2, backgroundColor: "rgba(225,229,236,0.12)",
           overflow: "hidden", marginTop: 18 },
  bar: { width: BAR_W, height: 3, backgroundColor: C.ember },
});
