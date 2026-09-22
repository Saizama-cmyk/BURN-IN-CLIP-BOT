"""Skills, rules and workflows: plain files you can edit, not a database.

A skill is one Markdown file with a short front matter block - a name, a one-line description,
and the instructions underneath. Rules are a single file whose text is added to every
conversation. A workflow is a numbered list of steps run one after another, each step a prompt.

They all live in the profile's data folder so they travel with the profile and can be edited in
any text editor:

    <data>/agent/skills/clip-review.md
    <data>/agent/rules.md
    <data>/agent/workflows/nightly.md
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("clipbot.agent.skills")

FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
STEP_LINE = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+(.*\S)\s*$")
NAME_MAX = 60
DESC_MAX = 200


@dataclass
class Skill:
    """Instructions the model is given when this skill is switched on."""
    id: str
    name: str
    description: str
    body: str
    path: Path | None = None

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "description": self.description,
                "body": self.body}


@dataclass
class Workflow:
    """A named list of prompts run in order, each one seeing what came before."""
    id: str
    name: str
    description: str
    steps: list[str] = field(default_factory=list)
    path: Path | None = None

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "description": self.description,
                "steps": list(self.steps)}


def agent_dir(data_dir: Path) -> Path:
    return data_dir / "agent"


def _front_matter(text: str) -> tuple[dict, str]:
    """Split "--- key: value ---" from the body. Unknown keys are kept, none are required."""
    match = FRONT_MATTER.match(text)
    if not match:
        return {}, text.strip()
    meta = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip().lower()] = value.strip()
    return meta, text[match.end():].strip()


def parse_skill(path: Path, text: str) -> Skill:
    meta, body = _front_matter(text)
    return Skill(
        id=path.stem.lower(),
        name=(meta.get("name") or path.stem.replace("-", " ").title())[:NAME_MAX],
        description=(meta.get("description") or "")[:DESC_MAX],
        body=body,
        path=path,
    )


def parse_workflow(path: Path, text: str) -> Workflow:
    meta, body = _front_matter(text)
    steps = [m.group(1) for m in (STEP_LINE.match(line) for line in body.splitlines()) if m]
    if not steps:                      # no list markers: every non-empty line is a step
        steps = [line.strip() for line in body.splitlines() if line.strip()]
    return Workflow(
        id=path.stem.lower(),
        name=(meta.get("name") or path.stem.replace("-", " ").title())[:NAME_MAX],
        description=(meta.get("description") or "")[:DESC_MAX],
        steps=steps,
        path=path,
    )


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("could not read %s: %s", path.name, exc)
        return ""


def load_skills(data_dir: Path) -> list[Skill]:
    folder = agent_dir(data_dir) / "skills"
    if not folder.is_dir():
        return []
    out = [parse_skill(p, _read(p)) for p in sorted(folder.glob("*.md"))]
    return [s for s in out if s.body]


def load_workflows(data_dir: Path) -> list[Workflow]:
    folder = agent_dir(data_dir) / "workflows"
    if not folder.is_dir():
        return []
    out = [parse_workflow(p, _read(p)) for p in sorted(folder.glob("*.md"))]
    return [w for w in out if w.steps]


def load_rules(data_dir: Path) -> str:
    """Text added to every conversation: how you want the assistant to behave, always."""
    path = agent_dir(data_dir) / "rules.md"
    return _read(path).strip() if path.exists() else ""


def save_skill(data_dir: Path, skill_id: str, name: str, description: str, body: str) -> Skill:
    folder = agent_dir(data_dir) / "skills"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{safe_id(skill_id)}.md"
    text = f"---\nname: {name}\ndescription: {description}\n---\n\n{body.strip()}\n"
    path.write_text(text, encoding="utf-8")
    logger.info("skill %s saved", path.stem)
    return parse_skill(path, text)


def save_rules(data_dir: Path, text: str) -> None:
    folder = agent_dir(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "rules.md").write_text(text.strip() + "\n", encoding="utf-8")


def delete_skill(data_dir: Path, skill_id: str) -> bool:
    path = agent_dir(data_dir) / "skills" / f"{safe_id(skill_id)}.md"
    if not path.exists():
        return False
    path.unlink()
    return True


def safe_id(value: str) -> str:
    """A file name that cannot escape the skills folder."""
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", value.strip().lower()).strip("-.")
    return cleaned[:NAME_MAX] or "skill"


def starter_skills() -> list[tuple[str, str, str, str]]:
    """What a new profile gets, so the feature is not an empty screen."""
    return [
        ("clip-doctor", "Clip doctor",
         "Explain why clips are being rejected and what to change.",
         "Look at the recent candidates and their verdicts. Group the rejections by reason, say "
         "which reason dominates, and name the one setting most likely to fix it. Quote real "
         "numbers from the log rather than describing them in general terms. Finish with a "
         "single recommended change, not a list of options."),
        ("post-writer", "Post writer",
         "Rewrite a clip's title and caption in a different voice.",
         "Ask which clip if it is not obvious. Read its title, caption and the judge's reason, "
         "then offer three alternative titles in different voices: blunt, funny, and curious. "
         "Keep each under the platform limit and never invent anything that is not in the clip."),
        ("night-check", "Night check",
         "A short status read: what worked, what is stuck, what to do.",
         "Check the pipeline status, the backlog, the disk, and what posted in the last day. "
         "Write at most six lines: what is healthy, what is stuck, and the single most useful "
         "thing to do next. No filler."),
    ]


def install_starters(data_dir: Path) -> int:
    """Write the starter skills the first time, and never overwrite an edited one."""
    folder = agent_dir(data_dir) / "skills"
    if folder.exists() and any(folder.glob("*.md")):
        return 0
    made = 0
    for skill_id, name, description, body in starter_skills():
        save_skill(data_dir, skill_id, name, description, body)
        made += 1
    return made
