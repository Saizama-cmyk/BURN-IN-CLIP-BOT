# Ashvane interface decisions

- **Subject:** a Windows editing desk that watches live streams and turns earned reactions into short, captioned clips.
- **Ground:** preview monitors, edit brackets, timecode, a playhead, a render queue. Monitoring must stay readable for hours.
- **Palette:** charcoal ground `#101113`, raised surface `#1B1D20`, paper-white text `#F3F0E9`, muted text `#A8A6A1`, rules `#343638`, ember accent `#FF894F`. Neutrals lean slightly cool; the accent is reserved for selected tools and active editing.
- **Type:** Bahnschrift for the compact wordmark and headings; Segoe UI for Windows-native reading; Consolas for time and measurements. Installed system fonts keep the app offline and prevent layout shifts.
- **Space:** 6/10/14 pixels within controls; 24/36 pixels between tasks. A summary is separate from diagnostics.
- **Shape:** 8-pixel surfaces, nested 5-pixel controls. Tonal separation; shadows only on floating dialogs.
- **Motion:** one Blender playhead reveal and short 120–180 ms interaction transitions. No perpetual dashboard decorations; reduced-motion support.
- **Signature:** Blender-rendered edit brackets holding a hot playhead. Art belongs at entry points, not behind tables and graphs.

Critique: a generic dark dashboard would spread equally prominent cards and glow across the screen. Here the live board gets the space, the pipeline is a compact timeline, and detailed counters and posting history are explicitly expandable. The desktop pet opens a persistent, bounded graph panel on click. Labels and values stay readable without relying on metallic effects.
