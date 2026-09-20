"""Learn which clips perform and steer toward them, without flooding the pages.

Refreshes (pulling views/likes/comments/shares for posts and follower counts per account):
  * periodically, every ``analytics.interval_h``;
  * "next day": as soon as any post passes ``next_day_h`` without a measurement after that age;
  * "end of day": once per day after ``end_of_day_hour`` (local time).

Scoring: a post's performance is its views at the next-day check relative to the median of
that platform over ``lookback_days`` — log2(views / median), so 2x the median = +1, half = -1.
Groups (streamer, category, moment kind) average their posts' scores, shrunk toward zero by
n / (n + shrinkage) so one lucky clip doesn't dominate. Nothing is steered until
``min_posts`` posts have been measured.

Steering, all bounded by ``steer_strength``:
  * scheduler priority: clips from strong groups go first — except an ``explore_share`` of
    picks that ignore analytics so new streamers/categories keep getting a chance;
  * anti-flood caps: at most ``max_share_per_streamer`` / ``max_share_per_category`` of a
    platform's posts per day;
  * discovery: the ``boost_top_streamers`` best streamers are watched first when live;
  * copywriter: top titles and themes are passed as examples.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime

from .config import AppPaths, Settings, atomic_write
from .db import Store
from .models import Candidate
from .publishers import Publisher
from .publishers.base import PublishError

logger = logging.getLogger("clipbot.analytics")

DAY_S = 86400
GROUPS = ("streamer", "category", "kind")
MEASURABLE = ("youtube", "tiktok", "instagram", "facebook")   # platforms with a stats API


@dataclass
class Insights:
    measured: int = 0
    groups: dict = field(default_factory=dict)          # group -> {value: {score, n}}
    top_posts: list = field(default_factory=list)
    medians: dict = field(default_factory=dict)          # platform -> median views
    accounts: dict = field(default_factory=dict)         # platform -> latest follower stats
    updated: float = 0.0

    def to_dict(self) -> dict:
        return {"measured": self.measured, "groups": self.groups, "top_posts": self.top_posts,
                "medians": self.medians, "accounts": self.accounts, "updated": self.updated}


def post_views_at(snaps: list[dict], posted_at: float, age_s: float) -> dict | None:
    """The first measurement taken at or after ``age_s`` (the next-day number)."""
    for s in snaps:
        if s["captured_at"] - posted_at >= age_s:
            return s
    return None


def compute_insights(posts: list[dict], metrics: dict[int, list[dict]], settings: Settings,
                     accounts: dict | None = None) -> Insights:
    a = settings.analytics
    age = a.next_day_h * 3600
    scored = []
    for p in posts:
        snap = post_views_at(metrics.get(p["post_id"], []), p["posted_at"], age)
        if snap:
            scored.append((p, snap))
    by_platform: dict[str, list[int]] = {}
    for p, snap in scored:
        by_platform.setdefault(p["platform"], []).append(snap["views"])
    medians = {k: statistics.median(v) for k, v in by_platform.items() if v}
    rows = []
    for p, snap in scored:
        ratio = (snap["views"] + 1) / (medians.get(p["platform"], 0) + 1)
        rows.append({**p, "views": snap["views"], "likes": snap["likes"],
                     "comments": snap["comments"], "shares": snap["shares"],
                     "perf": math.log2(ratio)})
    groups: dict[str, dict] = {}
    for g in GROUPS:
        acc: dict[str, list[float]] = {}
        for r in rows:
            key = (f"{r['source']}:{r['streamer']}" if g == "streamer"
                   else str(r.get(g) or "").strip())
            if key:
                acc.setdefault(key, []).append(r["perf"])
        groups[g] = {k: {"score": round(sum(v) / len(v) * len(v) / (len(v) + a.shrinkage), 3),
                         "n": len(v)} for k, v in acc.items()}
    top = sorted(rows, key=lambda r: -r["perf"])
    return Insights(measured=len(rows), groups=groups, medians=medians,
                    top_posts=[{k: r[k] for k in ("title", "platform", "display_name", "category",
                                                  "kind", "views", "likes", "perf", "url")}
                               for r in top[:a.examples * 2]],
                    accounts=accounts or {}, updated=time.time())


class Analytics:
    def __init__(self, settings: Settings, store: Store, publishers: dict[str, Publisher],
                 paths: AppPaths) -> None:
        self.settings = settings
        self.store = store
        self.publishers = publishers
        self.paths = paths
        self.insights = Insights()
        self.last_run = 0.0
        self.last_eod_date = ""
        self.status: dict[str, str] = {}
        self._picks: dict[str, int] = {}
        self._task: asyncio.Task | None = None
        self._file = paths.data / "analytics.json"
        self._load()

    def apply(self, settings: Settings) -> None:
        self.settings = settings

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        if not self._file.exists():
            return
        try:
            d = json.loads(self._file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("analytics cache unreadable, starting fresh: %s", exc)
            return
        ins = d.get("insights", {})
        self.insights = Insights(**{k: ins[k] for k in Insights().to_dict() if k in ins})
        self.last_run = float(d.get("last_run", 0))
        self.last_eod_date = d.get("last_eod_date", "")

    def _save(self) -> None:
        atomic_write(self._file, json.dumps({"insights": self.insights.to_dict(),
                                             "last_run": self.last_run,
                                             "last_eod_date": self.last_eod_date}))

    # ------------------------------------------------------------------ scheduling
    def due(self, now: float, posts: list[dict], metrics: dict[int, list[dict]]) -> str:
        """Why a refresh is due now ('' if not)."""
        a = self.settings.analytics
        if not a.enabled:
            return ""
        if now - self.last_run >= a.interval_h * 3600:
            return "periodic"
        if now - self.last_run < a.min_gap_min * 60:
            return ""
        age = a.next_day_h * 3600
        for p in posts:
            if p["platform"] not in MEASURABLE:
                continue
            if now - p["posted_at"] >= age and not post_views_at(
                    metrics.get(p["post_id"], []), p["posted_at"], age):
                return "next-day"
        today = datetime.fromtimestamp(now)
        if today.hour >= a.end_of_day_hour and self.last_eod_date != today.date().isoformat():
            return "end-of-day"
        return ""

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="analytics")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # keep learning alive; the traceback is logged
                logger.exception("analytics tick failed")
            await asyncio.sleep(self.settings.analytics.tick_s)

    async def tick(self, force: bool = False) -> bool:
        now = time.time()
        since = now - self.settings.analytics.lookback_days * DAY_S
        posts = await asyncio.to_thread(self.store.measurable_posts, since)
        metrics = await asyncio.to_thread(self.store.metrics_for, [p["post_id"] for p in posts])
        reason = "manual" if force else self.due(now, posts, metrics)
        if not reason:
            return False
        await self.refresh(posts, reason)
        return True

    async def refresh(self, posts: list[dict], reason: str) -> None:
        now = time.time()
        logger.info("analytics refresh (%s): %d post(s)", reason, len(posts))
        accounts: dict[str, dict] = {}
        for name in MEASURABLE:
            pub = self.publishers.get(name)
            if pub is None or not pub.configured()[0]:
                continue
            mine = [p for p in posts if p["platform"] == name]
            try:
                got = await pub.metrics([p["remote_id"] for p in mine]) if mine else {}
                for p in mine:
                    m = got.get(p["remote_id"])
                    if m:
                        await asyncio.to_thread(self.store.record_metrics, p["post_id"], m["views"],
                                                m["likes"], m["comments"], m["shares"], m["saves"])
                acct = await pub.account()
                if acct:
                    accounts[name] = acct
                    await asyncio.to_thread(self.store.record_account, name, acct["followers"],
                                            acct["total_views"], acct["posts"])
                self.status[name] = f"ok · {len(got)} post(s) measured"
            except (PublishError, OSError, ValueError, KeyError) as exc:
                self.status[name] = f"error: {exc}"
                logger.warning("analytics %s: %s", name, exc)
        metrics = await asyncio.to_thread(self.store.metrics_for, [p["post_id"] for p in posts])
        self.insights = compute_insights(posts, metrics, self.settings,
                                         accounts or self.insights.accounts)
        self.last_run = now
        if reason == "end-of-day" or datetime.fromtimestamp(now).hour >= \
                self.settings.analytics.end_of_day_hour:
            self.last_eod_date = datetime.fromtimestamp(now).date().isoformat()
        await asyncio.to_thread(self._save)

    # ------------------------------------------------------------------ steering
    @property
    def active(self) -> bool:
        a = self.settings.analytics
        return a.enabled and a.steer_strength > 0 and self.insights.measured >= a.min_posts

    def group_score(self, group: str, value: str) -> float:
        return float(self.insights.groups.get(group, {}).get(value, {}).get("score", 0.0))

    def bias(self, c: Candidate) -> float:
        """Priority boost for a clip, in 'judge score points' (0 when not steering)."""
        if not self.active:
            return 0.0
        t = c.event.target
        s = (self.group_score("streamer", t.key) + self.group_score("category", t.category)
             + self.group_score("kind", c.event.kind)) / len(GROUPS)
        return self.settings.analytics.steer_strength * s * len(GROUPS)

    def explore_now(self, platform: str) -> bool:
        """Deterministically spread ``explore_share`` of picks as unbiased ones."""
        n = self._picks.get(platform, 0) + 1
        self._picks[platform] = n
        e = self.settings.analytics.explore_share
        return math.floor(n * e) != math.floor((n - 1) * e)

    def within_caps(self, c: Candidate, today: list[dict]) -> bool:
        """Would posting ``c`` keep one streamer/category under its daily share?"""
        a = self.settings.analytics
        total = len(today) + 1
        login, cat = c.event.target.login.lower(), c.event.target.category
        same_s = sum(1 for p in today if p["streamer"] == login) + 1
        same_c = sum(1 for p in today if cat and p["category"] == cat) + (1 if cat else 0)
        return (same_s <= max(1, math.ceil(a.max_share_per_streamer * total))
                and same_c <= max(1, math.ceil(a.max_share_per_category * total)))

    def boosted_streamers(self, source_platform: str) -> list[str]:
        """Logins of the best-performing streamers (positive score) on one stream platform."""
        if not self.active or not self.settings.analytics.boost_top_streamers:
            return []
        prefix = f"{source_platform}:"
        ranked = sorted(((v["score"], k) for k, v in self.insights.groups.get("streamer", {}).items()
                         if v["score"] > 0 and k.startswith(prefix)), reverse=True)
        return [k[len(prefix):] for _, k in ranked][:self.settings.analytics.boost_top_streamers]

    def notes(self) -> str:
        """Short plain-text summary for the copywriter."""
        a = self.settings.analytics
        if not (a.feed_copywriter and self.active):
            return ""
        lines = []
        for g, label in (("streamer", "Streamers"), ("category", "Categories"),
                         ("kind", "Moment types")):
            vals = sorted(self.insights.groups.get(g, {}).items(), key=lambda kv: -kv[1]["score"])
            best = [f"{k} ({v['score']:+.1f})" for k, v in vals[:a.examples] if v["score"] > 0]
            worst = [f"{k} ({v['score']:+.1f})" for k, v in vals[-2:] if v["score"] < 0]
            if best:
                lines.append(f"{label} doing best: {', '.join(best)}.")
            if worst:
                lines.append(f"{label} doing worst: {', '.join(worst)}.")
        if self.insights.top_posts:
            lines.append("Best recent titles:")
            lines += [f'- "{p["title"]}" ({p["views"]} views on {p["platform"]})'
                      for p in self.insights.top_posts[:a.examples] if p.get("title")]
        return "\n".join(lines)[:a.insights_max_chars]

    def snapshot(self) -> dict:
        return {"enabled": self.settings.analytics.enabled, "steering": self.active,
                "last_run": self.last_run, "status": self.status, **self.insights.to_dict()}
