/**
 * What the assistant knows about you and how it should work: chats, skills, rules and your own
 * instructions. All of it lives on the phone, in one JSON file in the app's documents folder,
 * and nothing here needs the PC.
 *
 * Skills follow the SKILL.md shape used across AI tools - a name, a one-line description and the
 * instructions - so a skill written for another assistant can be pasted or imported as it is.
 * Only the names and descriptions of your skills sit in the model's context all the time; a
 * skill's full instructions are added only when you use it. That keeps a small phone model's
 * limited context for the conversation itself.
 */
import * as FileSystem from "expo-file-system";

const FILE = `${FileSystem.documentDirectory}brain.json`;
const CHATS_KEPT = 60;        // older chats are dropped past this
const TITLE_CHARS = 48;
const FILE_TEXT_MAX = 12000;  // characters of an attached text file sent to the model

export const STARTER_SKILLS = [
  {
    id: "research", name: "research",
    description: "Dig into a topic properly and come back with a clear, sourced answer.",
    body: "Treat the request as a research question. Break it into the few sub-questions that "
      + "actually decide the answer. For each, say what is well established, what is disputed, and "
      + "what you are unsure of. Separate facts from your own reasoning. If you would need to look "
      + "something up that you cannot, say exactly what to search for. Finish with a short answer "
      + "first, then the detail, then what would change your mind.",
  },
  {
    id: "learn", name: "learn",
    description: "Teach me something step by step until I actually understand it.",
    body: "Be a patient tutor. First ask what the person already knows, in one short question, "
      + "unless it is obvious. Then teach in small steps: one idea at a time, a plain example for "
      + "each, and a quick check question before moving on. Build from what they know to what they "
      + "don't. Use analogies from everyday life. End with a two-line summary they could repeat "
      + "to someone else.",
  },
  {
    id: "go-over", name: "go-over",
    description: "Go over something I wrote or made and tell me honestly what works and what doesn't.",
    body: "Review what the person shares like a sharp, fair editor. Start with what genuinely "
      + "works, in one or two lines. Then list the problems that matter most, most important first, "
      + "each with the fix. Be specific: quote the part you mean. Skip nitpicks unless asked. If it "
      + "is good, say so plainly instead of inventing problems.",
  },
  {
    id: "grill-me", name: "grill-me",
    description: "Quiz me hard on a topic or pressure-test my idea like a tough interviewer.",
    body: "Act as a tough but fair examiner. Ask one question at a time and wait for the answer. "
      + "Start easy, then push harder where the answers are weak. After each answer, say briefly "
      + "whether it holds up and why, then ask the next question. If they are pitching an idea, "
      + "attack its weakest assumptions the way a sceptical investor would. At the end, give a "
      + "score out of ten and the three things to work on.",
  },
  {
    id: "watch-this", name: "watch-this",
    description: "Watch a video I send (a link or an attached video) and tell me what's in it.",
    body: "The person wants to know about a video. You will be given what is said in it and what "
      + "is on screen. Summarise it in a few lines, then list the key moments in order with what "
      + "happens in each. Answer any question they asked about it directly. Never pretend to have "
      + "seen something that is not in what you were given.",
    needsVideo: true,
  },
  {
    id: "reverse-engineer", name: "reverse-engineer",
    description: "Work out how something was made or how it works from the outside in.",
    body: "Reverse-engineer what the person shows you: a product, a piece of content, code, a "
      + "business, a trick. Work from what is visible to what must be true underneath. Lay out the "
      + "parts, how they connect, and the likely steps used to build it. Mark each claim as seen, "
      + "very likely, or a guess. Finish with how they could make their own version.",
  },
  {
    id: "summarize", name: "summarize",
    description: "Boil something long down to what matters.",
    body: "Summarise what the person gives you. Lead with the single most important point in one "
      + "sentence. Then up to five bullets of what else matters. Keep names, numbers and decisions; "
      + "drop filler. If they asked for a length or format, follow it exactly.",
  },
  {
    id: "brainstorm", name: "brainstorm",
    description: "Throw lots of ideas at a problem, then pick the best ones.",
    body: "Generate many different ideas fast - at least ten, and make them genuinely different, "
      + "not variations of one. Include a few bold or strange ones. Then pick the three strongest, "
      + "say why each could work, and the first small step to try it.",
  },
  {
    id: "plan", name: "plan",
    description: "Turn a goal into a clear, ordered plan with next steps.",
    body: "Turn the goal into a plan. Restate the goal and what done looks like. List the steps in "
      + "order, each small enough to start today, with anything it depends on. Flag the riskiest "
      + "step and how to test it early. End with the very next action.",
  },
  {
    id: "explain-code", name: "explain-code",
    description: "Explain what a piece of code does, line by line if needed.",
    body: "Explain the code the person shares. Start with what it does overall in one or two "
      + "sentences. Then walk through it in order, grouping lines that belong together. Point out "
      + "anything surprising, risky or wrong. Match the depth to how experienced they seem.",
  },
];

const EMPTY = () => ({
  version: 1,
  instructions: { about: "", style: "" },
  rules: [],            // [{id, name, text, on}]
  skills: [],           // your own and imported skills, same shape as STARTER_SKILLS
  hidden: [],           // starter skill ids you switched off
  chats: [],            // [{id, title, mode, updated, messages: [{role, content, note?}]}]
});

export const newId = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;

export async function loadBrain() {
  try {
    const info = await FileSystem.getInfoAsync(FILE);
    if (!info.exists) return EMPTY();
    return { ...EMPTY(), ...JSON.parse(await FileSystem.readAsStringAsync(FILE)) };
  } catch {
    return EMPTY();                // a damaged file is replaced rather than crashing the app
  }
}

export async function saveBrain(brain) {
  const trimmed = { ...brain, chats: [...brain.chats]
    .sort((a, b) => b.updated - a.updated).slice(0, CHATS_KEPT) };
  await FileSystem.writeAsStringAsync(FILE, JSON.stringify(trimmed));
  return trimmed;
}

export function allSkills(brain) {
  const hidden = new Set(brain.hidden || []);
  return [...STARTER_SKILLS.filter(s => !hidden.has(s.id)).map(s => ({ ...s, starter: true })),
          ...brain.skills];
}

/** Read a SKILL.md: YAML-ish front matter (name, description) and the instructions below it. */
export function parseSkillMd(text) {
  const src = String(text || "").replace(/\r\n/g, "\n");
  const m = src.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  const head = {}, body = (m ? m[2] : src).trim();
  if (m) {
    for (const line of m[1].split("\n")) {
      const kv = line.match(/^([A-Za-z_-]+):\s*(.*)$/);
      if (kv) head[kv[1].toLowerCase()] = kv[2].replace(/^["']|["']$/g, "").trim();
    }
  }
  const name = (head.name || body.split("\n")[0].replace(/^#+\s*/, "") || "skill")
    .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "skill";
  return { id: newId(), name, description: head.description || "", body };
}

/** A GitHub page link turned into the raw file behind it; other links are fetched as they are. */
export function rawUrl(url) {
  const u = String(url || "").trim();
  const gh = u.match(/^https:\/\/github\.com\/([^/]+)\/([^/]+)\/blob\/(.+)$/);
  if (gh) return `https://raw.githubusercontent.com/${gh[1]}/${gh[2]}/${gh[3]}`;
  const tree = u.match(/^https:\/\/github\.com\/([^/]+)\/([^/]+)\/tree\/([^/]+)\/(.+)$/);
  if (tree) return `https://raw.githubusercontent.com/${tree[1]}/${tree[2]}/${tree[3]}/${tree[4]}/SKILL.md`;
  return u;
}

export async function importSkillFromUrl(url) {
  const r = await fetch(rawUrl(url));
  if (!r.ok) throw new Error(`could not fetch that (${r.status})`);
  return parseSkillMd(await r.text());
}

/** Which skill a message calls for: "/name" at the start, or the one picked in the menu. */
export function skillFor(text, skills, picked) {
  const m = String(text || "").match(/^\/([a-z0-9-]+)\b\s*/i);
  if (m) {
    const hit = skills.find(s => s.name === m[1].toLowerCase());
    if (hit) return { skill: hit, text: text.slice(m[0].length) };
  }
  return { skill: picked || null, text };
}

/**
 * The system prompt for one turn. Custom instructions and rules that are on always apply; the
 * skill list is names and one-liners only; the chosen skill's full instructions go last so they
 * are freshest in a small model's attention.
 */
export function buildSystem(base, brain, skills, skill) {
  const parts = [base];
  const { about, style } = brain.instructions || {};
  if (about?.trim()) parts.push(`About the person you are helping:\n${about.trim()}`);
  if (style?.trim()) parts.push(`How they want you to respond:\n${style.trim()}`);
  const rules = (brain.rules || []).filter(r => r.on && r.text.trim());
  if (rules.length) parts.push(`Rules you must follow:\n${rules.map(r => `- ${r.text.trim()}`).join("\n")}`);
  if (skills.length) {
    parts.push("Skills the person can call with /name:\n"
      + skills.map(s => `- /${s.name}: ${s.description}`).join("\n"));
  }
  if (skill) parts.push(`Use the "${skill.name}" skill for this reply:\n${skill.body}`);
  return parts.join("\n\n");
}

export function titleFrom(text) {
  const t = String(text || "").replace(/\s+/g, " ").trim();
  return t.length > TITLE_CHARS ? `${t.slice(0, TITLE_CHARS - 1)}…` : t || "New chat";
}

export { FILE_TEXT_MAX };
