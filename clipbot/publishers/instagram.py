"""Instagram Reels via the Instagram API with Instagram Login (graph.instagram.com).

Container (REELS, resumable) → bytes to rupload.facebook.com → poll status → media_publish.

Only the access token is required: the professional account's user id is looked up from the
token when ``accounts.instagram_user_id`` is blank. Long-lived tokens last 60 days, so the
token is refreshed automatically (``/refresh_access_token``) once it is older than
``posting.instagram.token_refresh_days``; the refreshed token lives in tokens.json. Pasting a
new token in Settings always takes over from the stored one.

The Instagram API has no endpoint to delete published media, so ``delete`` reports that.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

from ..util import ERR_SNIPPET
from .base import PostText, Publisher, PublishError, PublishResult, http_error, read_bytes, stats

GRAPH_VERSION = "v26.0"
GRAPH = f"https://graph.instagram.com/{GRAPH_VERSION}"
REFRESH_URL = "https://graph.instagram.com/refresh_access_token"
RUPLOAD = f"https://rupload.facebook.com/ig-api-upload/{GRAPH_VERSION}"
CAPTION_MAX = 2200   # API limit
DAY_S = 86400


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class InstagramPublisher(Publisher):
    name = "instagram"

    def configured(self) -> tuple[bool, str]:
        if not self.settings.accounts.instagram_token:
            return False, "Instagram access token missing (Settings → Accounts)"
        return True, ""

    # ------------------------------------------------------------------ token + id
    async def _token(self) -> str:
        """The freshest valid token: the one in Settings, or a refreshed copy of it."""
        typed = self.settings.accounts.instagram_token
        stored = self.tokens.get("instagram")
        if stored.get("source") != _fingerprint(typed):
            stored = {"access_token": typed, "source": _fingerprint(typed),
                      "refreshed_at": time.time()}
            self.tokens.set("instagram", stored)
        age_days = (time.time() - float(stored.get("refreshed_at", 0))) / DAY_S
        if age_days >= self.settings.posting.instagram.token_refresh_days:
            r = await self.http.get(REFRESH_URL, timeout=self.timeout,
                                    params={"grant_type": "ig_refresh_token",
                                            "access_token": stored["access_token"]})
            body = r.json() if r.content else {}
            if r.status_code == 200 and body.get("access_token"):
                stored = dict(stored, access_token=body["access_token"], refreshed_at=time.time())
                self.tokens.set("instagram", stored)
            else:
                raise PublishError("Instagram token refresh failed — generate a new token in the "
                                   f"Meta app dashboard: {r.text[:ERR_SNIPPET]}")
        return stored["access_token"]

    async def _user_id(self, token: str) -> str:
        configured = self.settings.accounts.instagram_user_id.strip()
        if configured:
            return configured
        r = await self.http.get(f"{GRAPH}/me", timeout=self.timeout,
                                params={"fields": "user_id,username", "access_token": token})
        uid = str((r.json() if r.content else {}).get("user_id", ""))
        if r.status_code != 200 or not uid:
            raise http_error(r, "Instagram account lookup", ERR_SNIPPET)
        return uid

    # ------------------------------------------------------------------ publish
    async def _publish(self, video: Path, post: PostText) -> PublishResult:
        cfg = self.settings.posting.instagram
        token = await self._token()
        uid = await self._user_id(token)
        caption = f"{post.title}\n\n{post.caption}".strip()[:CAPTION_MAX]
        r = await self.http.post(f"{GRAPH}/{uid}/media", timeout=self.timeout,
                                 data={"media_type": "REELS", "upload_type": "resumable",
                                       "caption": caption,
                                       "share_to_feed": str(cfg.share_to_feed).lower(),
                                       "access_token": token})
        if r.status_code != 200 or "id" not in r.json():
            raise http_error(r, "Instagram container", ERR_SNIPPET)
        container = r.json()["id"]
        data = await read_bytes(video)
        up = await self.http.post(f"{RUPLOAD}/{container}", content=data, timeout=self.timeout,
                                  headers={"Authorization": f"OAuth {token}", "offset": "0",
                                           "file_size": str(len(data))})
        if up.status_code != 200:
            raise http_error(up, "Instagram upload", ERR_SNIPPET)

        async def check():
            s = await self.http.get(f"{GRAPH}/{container}", timeout=self.timeout,
                                    params={"fields": "status_code,status", "access_token": token})
            body = s.json()
            code = body.get("status_code")
            if code == "FINISHED":
                return True
            if code in ("ERROR", "EXPIRED"):
                raise PublishError(f"Instagram processing {code}: {body.get('status', '')}")
            return None

        await self.poll(check, "Instagram")
        pub = await self.http.post(f"{GRAPH}/{uid}/media_publish", timeout=self.timeout,
                                   data={"creation_id": container, "access_token": token})
        if pub.status_code != 200 or "id" not in pub.json():
            raise http_error(pub, "Instagram publish", ERR_SNIPPET)
        media_id = pub.json()["id"]
        link = await self.http.get(f"{GRAPH}/{media_id}", timeout=self.timeout,
                                   params={"fields": "permalink", "access_token": token})
        url = link.json().get("permalink", "") if link.status_code == 200 else ""
        return PublishResult(True, remote_id=media_id, url=url)

    async def metrics(self, remote_ids: list[str]) -> dict[str, dict]:
        token = await self._token()
        out: dict[str, dict] = {}
        for mid in remote_ids:
            r = await self.http.get(f"{GRAPH}/{mid}/insights", timeout=self.timeout,
                                    params={"metric": "views,likes,comments,shares,saved",
                                            "access_token": token})
            if r.status_code == 200:
                vals = {}
                for m in r.json().get("data", []):
                    v = (m.get("total_value") or {}).get("value")
                    if v is None and m.get("values"):
                        v = m["values"][0].get("value")
                    vals[m.get("name")] = v
                out[mid] = stats(vals.get("views"), vals.get("likes"), vals.get("comments"),
                                 vals.get("shares"), vals.get("saved"))
                continue
            # insights need instagram_business_manage_insights; fall back to public counts
            f = await self.http.get(f"{GRAPH}/{mid}", timeout=self.timeout,
                                    params={"fields": "like_count,comments_count",
                                            "access_token": token})
            if f.status_code != 200:
                raise http_error(r, "Instagram insights", ERR_SNIPPET)
            body = f.json()
            out[mid] = stats(0, body.get("like_count"), body.get("comments_count"))
        return out

    async def account(self) -> dict | None:
        token = await self._token()
        uid = await self._user_id(token)
        r = await self.http.get(f"{GRAPH}/{uid}", timeout=self.timeout,
                                params={"fields": "followers_count,media_count",
                                        "access_token": token})
        if r.status_code != 200:
            raise http_error(r, "Instagram account stats", ERR_SNIPPET)
        b = r.json()
        return {"followers": int(b.get("followers_count", 0) or 0), "total_views": 0,
                "posts": int(b.get("media_count", 0) or 0)}
