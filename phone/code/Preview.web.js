/** The web preview's Run pane: a sandboxed iframe, console forwarded by postMessage. */
import React, { useEffect, useRef } from "react";

export default function Preview({ html, runKey, onConsole }) {
  const frame = useRef(null);
  useEffect(() => {
    const on = (e) => {
      if (!frame.current || e.source !== frame.current.contentWindow) return;
      try { onConsole(JSON.parse(e.data)); } catch { onConsole({ kind: "log", text: String(e.data) }); }
    };
    window.addEventListener("message", on);
    return () => window.removeEventListener("message", on);
  }, [onConsole]);
  return <iframe key={runKey} ref={frame} srcDoc={html} sandbox="allow-scripts allow-modals" title="Preview"
    style={{ flex: 1, border: 0, width: "100%", height: "100%", background: "#0b0b0c" }} />;
}
