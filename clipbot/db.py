"""SQLite persistence for candidates and post attempts.

One connection guarded by a lock; callers on the event loop wrap calls in
``asyncio.to_thread`` so disk I/O never blocks the loop.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .models import ACTIVE_STAGES, Candidate, Stage

SQLITE_BUSY_TIMEOUT_S = 30   # wait for a concurrent writer instead of failing

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY, created_at REAL NOT NULL, platform TEXT NOT NULL, login TEXT NOT NULL,
    stage TEXT NOT NULL, score REAL NOT NULL DEFAULT 0, json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_cand_created ON candidates(created_at);
CREATE INDEX IF NOT EXISTS ix_cand_stage ON candidates(stage);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT NOT NULL, platform TEXT NOT NULL,
    remote_id TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', posted_at REAL NOT NULL,
    status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', deleted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_posts_platform ON posts(platform, posted_at);
CREATE INDEX IF NOT EXISTS ix_posts_candidate ON posts(candidate_id);
CREATE TABLE IF NOT EXISTS post_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER NOT NULL, captured_at REAL NOT NULL,
    views INTEGER NOT NULL DEFAULT 0, likes INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0, shares INTEGER NOT NULL DEFAULT 0,
    saves INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_metrics_post ON post_metrics(post_id, captured_at);
CREATE TABLE IF NOT EXISTS account_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, captured_at REAL NOT NULL,
    followers INTEGER NOT NULL DEFAULT 0, total_views INTEGER NOT NULL DEFAULT 0,
    posts INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_account_platform ON account_stats(platform, captured_at);
CREATE TABLE IF NOT EXISTS imported_clips (
    clip_id TEXT PRIMARY KEY, imported_at REAL NOT NULL, candidate_id TEXT NOT NULL DEFAULT '');
"""


class Store:
    """Thread-safe wrapper around one SQLite connection."""

    def __init__(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False,
                                     timeout=SQLITE_BUSY_TIMEOUT_S)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(posts)")}
            if "deleted" not in cols:  # databases created by the first prototype
                self._conn.execute("ALTER TABLE posts ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # candidates ---------------------------------------------------------------
    def upsert_candidate(self, c: Candidate) -> None:
        score = float(c.verdict.get("score", 0) or 0)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO candidates(id, created_at, platform, login, stage, score, json) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET stage=excluded.stage, "
                "score=excluded.score, json=excluded.json",
                (c.id, c.created_at, str(c.event.target.platform), c.event.target.login,
                 str(c.stage), score, json.dumps(c.to_dict())))

    def get_candidate(self, candidate_id: str) -> Candidate | None:
        with self._lock:
            row = self._conn.execute("SELECT json FROM candidates WHERE id=?",
                                     (candidate_id,)).fetchone()
        return Candidate.from_dict(json.loads(row["json"])) if row else None

    def candidates_in_stage(self, stage: Stage) -> list[Candidate]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT json FROM candidates WHERE stage=? ORDER BY created_at",
                (str(stage),)).fetchall()
        return [Candidate.from_dict(json.loads(r["json"])) for r in rows]

    def active_candidates(self) -> list[Candidate]:
        marks = ",".join("?" for _ in ACTIVE_STAGES)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT json FROM candidates WHERE stage IN ({marks}) ORDER BY created_at",
                tuple(str(s) for s in ACTIVE_STAGES)).fetchall()
        return [Candidate.from_dict(json.loads(r["json"])) for r in rows]

    def recent_candidates(self, limit: int) -> list[dict]:
        """Newest first, without word timings (keeps dashboard payloads small)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT json FROM candidates ORDER BY created_at DESC LIMIT ?",
                (limit,)).fetchall()
        out = []
        for r in rows:
            d = json.loads(r["json"])
            d.pop("words", None)
            d["event"].pop("chat_sample", None)
            out.append(d)
        return out

    def candidate_histogram(self, now: float, buckets: int, bucket_s: float,
                            passed_stages: tuple[str, ...]) -> dict[str, list[int]]:
        """Count the full time window, independently of the dashboard's sample limit.

        Buckets are rolling intervals ending at ``now``, oldest first. Aggregate indexed
        columns in SQLite rather than reading every candidate's transcript and JSON.
        """
        if buckets <= 0 or bucket_s <= 0:
            raise ValueError("histogram needs positive buckets and bucket duration")
        marks = ",".join("?" for _ in passed_stages)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT CAST((? - created_at) / ? AS INTEGER) age_bucket, COUNT(*) total, "
                f"SUM(CASE WHEN stage IN ({marks}) THEN 1 ELSE 0 END) passed "
                "FROM candidates WHERE created_at > ? AND created_at <= ? "
                "GROUP BY age_bucket",
                (now, bucket_s, *passed_stages, now - buckets * bucket_s, now)).fetchall()
        counts, passed = [0] * buckets, [0] * buckets
        for row in rows:
            slot = buckets - 1 - int(row["age_bucket"])
            if 0 <= slot < buckets:
                counts[slot], passed[slot] = int(row["total"]), int(row["passed"])
        return {"hours": counts, "passed_hours": passed}

    def stage_counts(self, since_ts: float) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT stage, COUNT(*) n FROM candidates WHERE created_at>=? GROUP BY stage",
                (since_ts,)).fetchall()
        return {r["stage"]: r["n"] for r in rows}

    def expired_candidates(self, older_than_ts: float) -> list[Candidate]:
        with self._lock:
            rows = self._conn.execute("SELECT json FROM candidates WHERE created_at<?",
                                      (older_than_ts,)).fetchall()
        return [Candidate.from_dict(json.loads(r["json"])) for r in rows]

    def delete_candidate(self, candidate_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM candidates WHERE id=?", (candidate_id,))
            self._conn.execute("DELETE FROM posts WHERE candidate_id=?", (candidate_id,))

    # posts --------------------------------------------------------------------
    def record_post(self, candidate_id: str, platform: str, remote_id: str, url: str,
                    status: str, error: str = "", when: float | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO posts(candidate_id, platform, remote_id, url, posted_at, status, "
                "error) VALUES(?,?,?,?,?,?,?)",
                (candidate_id, platform, remote_id, url, time.time() if when is None else when,
                 status, error))

    def posts_since(self, platform: str, since_ts: float) -> int:
        """Successful posts on ``platform`` since ``since_ts`` (daily-cap accounting)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) n FROM posts WHERE platform=? AND status='ok' AND posted_at>=?",
                (platform, since_ts)).fetchone()
        return int(row["n"])

    def last_post_time(self, platform: str) -> float | None:
        """Most recent attempt (ok or failed) — the minimum gap applies to both."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(posted_at) t FROM posts WHERE platform=?", (platform,)).fetchone()
        return row["t"]

    def posts_for_candidate(self, candidate_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM posts WHERE candidate_id=? ORDER BY posted_at",
                                      (candidate_id,)).fetchall()
        return [dict(r) for r in rows]

    def mark_post_deleted(self, post_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE posts SET deleted=1 WHERE id=?", (post_id,))

    def recent_posts(self, limit: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT p.*, c.login, c.platform AS source_platform FROM posts p "
                "LEFT JOIN candidates c ON c.id=p.candidate_id "
                "ORDER BY posted_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # analytics ----------------------------------------------------------------
    def measurable_posts(self, since_ts: float) -> list[dict]:
        """Successful, not-deleted posts with a remote id, joined with their clip's details."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT p.id, p.candidate_id, p.platform, p.remote_id, p.url, p.posted_at, "
                "c.json FROM posts p JOIN candidates c ON c.id=p.candidate_id "
                "WHERE p.status='ok' AND p.deleted=0 AND p.remote_id!='' AND p.posted_at>=? "
                "ORDER BY p.posted_at", (since_ts,)).fetchall()
        out = []
        for r in rows:
            cand = json.loads(r["json"])
            ev, tgt = cand["event"], cand["event"]["target"]
            out.append({"post_id": r["id"], "candidate_id": r["candidate_id"],
                        "platform": r["platform"], "remote_id": r["remote_id"], "url": r["url"],
                        "posted_at": r["posted_at"], "streamer": tgt["login"].lower(),
                        "display_name": tgt.get("display_name", tgt["login"]),
                        "source": tgt["platform"], "category": tgt.get("category", ""),
                        "kind": ev.get("kind", ""), "score": cand.get("verdict", {}).get("score", 0),
                        "title": cand.get("verdict", {}).get("title", "")})
        return out

    def record_metrics(self, post_id: int, views: int, likes: int, comments: int, shares: int,
                       saves: int, when: float | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO post_metrics(post_id, captured_at, views, likes, comments, shares, "
                "saves) VALUES(?,?,?,?,?,?,?)",
                (post_id, time.time() if when is None else when, views, likes, comments, shares, saves))

    def metrics_for(self, post_ids: list[int]) -> dict[int, list[dict]]:
        if not post_ids:
            return {}
        marks = ",".join("?" for _ in post_ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM post_metrics WHERE post_id IN ({marks}) ORDER BY captured_at",
                tuple(post_ids)).fetchall()
        out: dict[int, list[dict]] = {}
        for r in rows:
            out.setdefault(r["post_id"], []).append(dict(r))
        return out

    def record_account(self, platform: str, followers: int, total_views: int, posts: int,
                       when: float | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO account_stats(platform, captured_at, followers, total_views, posts) "
                "VALUES(?,?,?,?,?)",
                (platform, time.time() if when is None else when, followers, total_views, posts))

    def account_history(self, since_ts: float) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM account_stats WHERE captured_at>=? ORDER BY captured_at",
                (since_ts,)).fetchall()
        return [dict(r) for r in rows]

    def posts_today(self, platform: str, since_ts: float) -> list[dict]:
        """Successful posts since ``since_ts`` with the clip's streamer and category (flood caps)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.json FROM posts p JOIN candidates c ON c.id=p.candidate_id "
                "WHERE p.platform=? AND p.status='ok' AND p.posted_at>=?",
                (platform, since_ts)).fetchall()
        out = []
        for r in rows:
            tgt = json.loads(r["json"])["event"]["target"]
            out.append({"streamer": tgt["login"].lower(), "category": tgt.get("category", "")})
        return out

    # imported clips -------------------------------------------------------------
    def clip_imported(self, clip_id: str) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM imported_clips WHERE clip_id=?",
                                      (clip_id,)).fetchone() is not None

    def mark_clip_imported(self, clip_id: str, candidate_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT OR IGNORE INTO imported_clips(clip_id, imported_at, "
                               "candidate_id) VALUES(?,?,?)", (clip_id, time.time(), candidate_id))
