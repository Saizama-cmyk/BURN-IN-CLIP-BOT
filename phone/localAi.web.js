/**
 * The web preview's stand-in for localAi.js. A browser cannot run the phone's model, so this
 * pretends a model is loaded and answers with a canned reply that exercises the UI - including a
 * proposed file edit, so the Code agent's diff cards can be seen. Never used on a phone.
 */
export const LADDER = [{ id: "preview", name: "Preview model", blurb: "Stands in for the phone's model in the browser preview.",
  bytes: 0, needsGb: 0, ctx: 4096 }];
export const SYSTEM = "You are a straight-talking assistant running entirely on this phone.";

export function deviceProfile() {
  return { name: "Browser preview", brand: "web", totalGb: 8, usableGb: 3.6, os: "web" };
}
export const pickModel = () => LADDER[0];
export const modelPath = () => "";
export const isDownloaded = async () => true;
export const downloadedIds = async () => new Set(["preview"]);
export const pendingProgress = async () => 0;
export const download = async () => "";
export const removeModel = async () => {};
export const freeBytes = async () => Infinity;
export const load = async () => ({ preview: true });

const EDIT = "I'll make the button count up and change colour after five clicks.\n\n```file:app.js\n"
  + "const button = document.getElementById(\"go\");\nlet clicks = 0;\n\nbutton.addEventListener(\"click\", () => {\n"
  + "  clicks += 1;\n  button.textContent = `Clicked ${clicks} time${clicks === 1 ? \"\" : \"s\"}`;\n"
  + "  if (clicks >= 5) button.style.background = \"#5fcb8e\";\n  console.log(\"clicked\", clicks);\n});\n```";

export async function reply(context, history, onToken, system = SYSTEM) {
  const last = history[history.length - 1]?.content || "";
  const text = /coding agent/i.test(system) ? EDIT
    : `(Preview) You said: "${last.slice(0, 120)}". On a phone, the real model answers here.`;
  for (const piece of text.match(/.{1,12}/gs) || []) {
    await new Promise(r => setTimeout(r, 12));
    if (onToken) onToken(piece);
  }
  return text;
}
