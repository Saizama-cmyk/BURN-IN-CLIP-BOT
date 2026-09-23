/**
 * Projects: folders of files on the phone, plus the agent's conversation for each one.
 * Everything here goes through fs.js, so it works the same on the phone and in the web preview.
 */
import * as fs from "./fs";

export const META_DIR = ".ashvane";            // the workspace's own notes, hidden from the tree
const AGENT_FILE = `${META_DIR}/agent.json`;
const TREE_MAX = 400;                          // files listed per project
export const TEXT_MAX = 200000;                // characters the editor opens

export const TEMPLATES = [
  { id: "web", name: "Web page", icon: "globe-outline", blurb: "HTML, CSS and JavaScript. Runs right here.",
    files: {
      "index.html": "<!doctype html>\n<html>\n<head>\n  <meta charset=\"utf-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n  <title>My page</title>\n  <link rel=\"stylesheet\" href=\"style.css\">\n</head>\n<body>\n  <main>\n    <h1>Hello</h1>\n    <p>Edit index.html, style.css and app.js, then press Run.</p>\n    <button id=\"go\">Click me</button>\n  </main>\n  <script src=\"app.js\"></script>\n</body>\n</html>\n",
      "style.css": "body {\n  font-family: system-ui, sans-serif;\n  background: #111;\n  color: #eee;\n  display: grid;\n  place-items: center;\n  min-height: 100vh;\n  margin: 0;\n}\n\nbutton {\n  font: inherit;\n  padding: 10px 18px;\n  border-radius: 10px;\n  border: 0;\n  background: #f06834;\n  color: #1a0d06;\n}\n",
      "app.js": "const button = document.getElementById(\"go\");\nlet clicks = 0;\n\nbutton.addEventListener(\"click\", () => {\n  clicks += 1;\n  button.textContent = `Clicked ${clicks} time${clicks === 1 ? \"\" : \"s\"}`;\n  console.log(\"clicked\", clicks);\n});\n",
    } },
  { id: "js", name: "JavaScript", icon: "logo-javascript", blurb: "A script. Run it and see the console.",
    files: { "main.js": "// Press Run to execute this and see the console.\nfunction greet(name) {\n  return `Hello, ${name}!`;\n}\n\nconsole.log(greet(\"world\"));\n" } },
  { id: "python", name: "Python", icon: "logo-python", blurb: "Write here; run it on a computer.",
    files: { "main.py": "def greet(name: str) -> str:\n    return f\"Hello, {name}!\"\n\n\nif __name__ == \"__main__\":\n    print(greet(\"world\"))\n",
             "README.md": "# My project\n\nRun with `python main.py`.\n" } },
  { id: "blank", name: "Blank", icon: "document-outline", blurb: "An empty folder.", files: { "README.md": "# New project\n" } },
];

export const safeName = (name) => String(name || "").trim().replace(/[\\/:*?"<>|]+/g, "-").slice(0, 60);
export const cleanPath = (p) => String(p || "").trim().replace(/\\/g, "/").replace(/^\/+|\/+$/g, "")
  .split("/").filter(x => x && x !== "." && x !== "..").join("/");

export async function listProjects() {
  await fs.ensureRoot();
  const names = await fs.listDir("");
  const out = [];
  for (const name of names) {
    if (!(await fs.isDir(name))) continue;
    const files = await tree(name);
    out.push({ name, files: files.length, modified: Math.max(0, ...files.map(f => f.modified || 0)) });
  }
  return out.sort((a, b) => b.modified - a.modified || a.name.localeCompare(b.name));
}

export async function createProject(name, templateId) {
  const n = safeName(name);
  if (!n) throw new Error("give the project a name");
  if ((await fs.stat(n)).exists) throw new Error(`there is already a project called ${n}`);
  const tpl = TEMPLATES.find(x => x.id === templateId) || TEMPLATES[TEMPLATES.length - 1];
  for (const [path, text] of Object.entries(tpl.files)) await fs.write(`${n}/${path}`, text);
  return n;
}

export const deleteProject = (name) => fs.remove(name);

/** Every file in a project, depth-first, folders first. Paths are relative to the project. */
export async function tree(project) {
  const out = [];
  const walk = async (rel, depth) => {
    const names = (await fs.listDir(rel ? `${project}/${rel}` : project))
      .filter(n => n !== META_DIR && n !== ".keep").sort((a, b) => a.localeCompare(b));
    const dirs = [], files = [];
    for (const n of names) {
      const path = rel ? `${rel}/${n}` : n;
      ((await fs.isDir(`${project}/${path}`)) ? dirs : files).push(path);
    }
    for (const d of dirs) {
      if (out.length >= TREE_MAX) return;
      out.push({ path: d, dir: true, depth });
      await walk(d, depth + 1);
    }
    for (const f of files) {
      if (out.length >= TREE_MAX) return;
      const st = await fs.stat(`${project}/${f}`);
      out.push({ path: f, dir: false, depth, size: st.size, modified: st.modified });
    }
  };
  await walk("", 0);
  return out;
}

export const readFile = (project, path) => fs.read(`${project}/${path}`);
export const writeFile = (project, path, text) => fs.write(`${project}/${cleanPath(path)}`, text);
export const deletePath = (project, path) => fs.remove(`${project}/${path}`);
export const movePath = (project, from, to) => fs.move(`${project}/${from}`, `${project}/${cleanPath(to)}`);
export const makeFolder = (project, path) => fs.mkdir(`${project}/${cleanPath(path)}`);

export async function exists(project, path) {
  return (await fs.stat(`${project}/${path}`)).exists;
}

export async function loadAgent(project) {
  try { return JSON.parse(await fs.read(`${project}/${AGENT_FILE}`)); }
  catch { return { turns: [] }; }
}

export async function saveAgent(project, data) {
  await fs.write(`${project}/${AGENT_FILE}`, JSON.stringify(data));
}

export function langOf(path) {
  const ext = (String(path).split(".").pop() || "").toLowerCase();
  return { js: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript", py: "python",
    html: "html", htm: "html", css: "css", json: "json", md: "markdown", sh: "shell", txt: "text" }[ext] || ext || "text";
}

export function iconOf(path, dir) {
  if (dir) return "folder-outline";
  const lang = langOf(path);
  return { javascript: "logo-javascript", typescript: "code-slash-outline", python: "logo-python",
    html: "logo-html5", css: "logo-css3", json: "code-working-outline", markdown: "reader-outline" }[lang]
    || "document-text-outline";
}
