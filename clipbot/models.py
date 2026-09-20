"""Core data types passed between pipeline stages."""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum


class Platform(StrEnum):
    TWITCH = "twitch"
    KICK = "kick"


class Stage(StrEnum):
    QUEUED = "queued"
    QC = "qc"
    TRANSCRIBE = "transcribe"
    CLIPABILITY = "clipability"
    EDIT = "edit"
    SCHEDULED = "scheduled"
    POSTED = "posted"
    REJECTED = "rejected"
    FAILED = "failed"


ACTIVE_STAGES = (Stage.QUEUED, Stage.QC, Stage.TRANSCRIBE, Stage.CLIPABILITY, Stage.EDIT)


ID_LEN = 12   # hex chars in a candidate id


def new_id() -> str:
    return uuid.uuid4().hex[:ID_LEN]


@dataclass
class StreamTarget:
    platform: Platform
    login: str
    display_name: str
    category: str
    viewers: int
    title: str = ""
    forced: bool = False
    chatroom_id: int | None = None
    user_id: str = ""                    # Twitch broadcaster id (for the clips API)

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.login.lower()}"

    @property
    def url(self) -> str:
        host = "twitch.tv" if self.platform == Platform.TWITCH else "kick.com"
        return f"https://{host}/{self.login}"


@dataclass
class SpikeEvent:
    target: StreamTarget
    t_wall: float
    kind: str  # chat | keyword | audio | mixed
    score: float
    chat_rate: float
    baseline: float
    keywords_hit: list[str] = field(default_factory=list)
    chat_sample: list[str] = field(default_factory=list)
    chat_z: float = 0.0
    keyword_score: float = 0.0
    audio_z: float = 0.0
    id: str = field(default_factory=new_id)


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Candidate:
    id: str
    event: SpikeEvent
    raw_path: str
    start_wall: float
    end_wall: float
    stage: Stage = Stage.QUEUED
    transcript: str = ""
    visual: str = ""                     # vision model's frame-by-frame description
    frame_times: list[float] = field(default_factory=list)   # when the saved vision frames are
    post_copy: dict = field(default_factory=dict)            # per-platform titles/captions/tags
    source: dict = field(default_factory=dict)               # {"type": "twitch_clip", ...} for imports
    words: list[Word] = field(default_factory=list)
    verdict: dict = field(default_factory=dict)
    final_path: str = ""
    error: str = ""
    posted_to: dict[str, str] = field(default_factory=dict)  # platform -> "ok"|"failed"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        """JSON-safe representation used by the store and dashboard."""
        data = asdict(self)
        data["stage"] = str(self.stage)
        data["event"]["target"]["platform"] = str(self.event.target.platform)
        data["event"]["target"]["key"] = self.event.target.key
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Candidate":
        ev = dict(data["event"])
        tgt = _known(StreamTarget, ev.pop("target"))
        tgt["platform"] = Platform(tgt["platform"])
        event = SpikeEvent(target=StreamTarget(**tgt), **_known(SpikeEvent, ev))
        words = [Word(**_known(Word, w)) for w in data.get("words", [])]
        rest = {k: v for k, v in _known(cls, data).items() if k not in ("event", "words", "stage")}
        return cls(event=event, words=words, stage=Stage(data["stage"]), **rest)


def _known(dc: type, data: dict) -> dict:
    """Drop keys a dataclass doesn't declare (derived keys, older/newer rows)."""
    names = {f.name for f in fields(dc)}
    return {k: v for k, v in data.items() if k in names}
