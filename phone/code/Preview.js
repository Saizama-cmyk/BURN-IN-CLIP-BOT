/** The running page, in a WebView. Console messages come back through onMessage. */
import React from "react";
import { WebView } from "react-native-webview";
import { C } from "../theme";

export default function Preview({ html, runKey, onConsole }) {
  return (
    <WebView key={runKey} originWhitelist={["*"]} source={{ html }} javaScriptEnabled
      style={{ flex: 1, backgroundColor: C.bg }}
      onMessage={(e) => {
        try { onConsole(JSON.parse(e.nativeEvent.data)); } catch { onConsole({ kind: "log", text: e.nativeEvent.data }); }
      }} />);
}
