"""YouTube Shorts: resumable upload via the Data API v3."""
from __future__ import annotations

import json
from pathlib import Path

from ..util import ERR_SNIPPET
from .base import PostText, Publisher, PublishError, PublishResult, http_error, read_bytes, stats
from .oauth import OAuthManager

AGE_RESTRICTED = "ytAgeRestricted"    # YouTube's 18+ rating
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
IDS_PER_CALL = 50        # API limit
TITLE_MAX = 100          # API limit
DESCRIPTION_MAX = 5000   # API limit


class YouTubePublisher(Publisher):
    name = "youtube"

    def __init__(self, settings, http, tokens, oauth: OAuthManager) -> None:
        super().__init__(settings, http, tokens)
        self.oauth = oauth

    def configured(self) -> tuple[bool, str]:
        a = self.settings.accounts
        if not (a.youtube_client_id and a.youtube_client_secret):
            return False, "YouTube OAuth client ID/secret missing (Settings → Accounts)"
        if not self.oauth.connected("youtube"):
            return False, "YouTube account not connected (Settings → Accounts → Connect)"
        return True, ""

    async def _publish(self, video: Path, post: PostText) -> PublishResult:
        cfg = self.settings.posting.youtube
        token = await self.oauth.access_token("youtube")
        suffix = cfg.shorts_suffix
        title = (post.title[:TITLE_MAX - len(suffix)] + suffix).strip()
        meta = {"snippet": {"title": title, "description": post.caption[:DESCRIPTION_MAX],
                            "tags": post.hashtags, "categoryId": cfg.category_id},
                "status": {"privacyStatus": cfg.privacy, "selfDeclaredMadeForKids": False}}
        parts = "snippet,status"
        if cfg.age_restrict:                      # 18+: keeps clips out of kids' feeds
            meta["contentDetails"] = {"contentRating": {"ytRating": AGE_RESTRICTED}}
            parts += ",contentDetails"
        data = await read_bytes(video)
        r = await self.http.post(
            UPLOAD_URL, params={"uploadType": "resumable", "part": parts},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json; charset=UTF-8",
                     "X-Upload-Content-Type": "video/mp4",
                     "X-Upload-Content-Length": str(len(data))},
            content=json.dumps(meta), timeout=self.timeout)
        if r.status_code != 200 or "location" not in r.headers:
            raise http_error(r, "YouTube upload init", ERR_SNIPPET)
        up = await self.http.put(r.headers["location"], content=data, timeout=self.timeout,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "video/mp4"})
        if up.status_code not in (200, 201):
            raise http_error(up, "YouTube upload", ERR_SNIPPET)
        vid = up.json().get("id")
        if not vid:
            raise PublishError(f"YouTube upload returned no id: {up.text[:ERR_SNIPPET]}")
        return PublishResult(True, remote_id=vid, url=f"https://youtube.com/shorts/{vid}")

    async def delete(self, remote_id: str) -> PublishResult:
        try:
            token = await self.oauth.access_token("youtube")
            r = await self.http.delete(VIDEOS_URL, params={"id": remote_id},
                                       headers={"Authorization": f"Bearer {token}"},
                                       timeout=self.timeout)
        except (PublishError, OSError) as exc:
            return PublishResult(False, error=str(exc))
        if r.status_code in (204, 404):
            return PublishResult(True, remote_id=remote_id)
        return PublishResult(False, error=f"YouTube delete HTTP {r.status_code}: "
                                          f"{r.text[:ERR_SNIPPET]}")

    async def metrics(self, remote_ids: list[str]) -> dict[str, dict]:
        token = await self.oauth.access_token("youtube")
        out: dict[str, dict] = {}
        for i in range(0, len(remote_ids), IDS_PER_CALL):
            r = await self.http.get(VIDEOS_URL, timeout=self.timeout,
                                    params={"part": "statistics",
                                            "id": ",".join(remote_ids[i:i + IDS_PER_CALL])},
                                    headers={"Authorization": f"Bearer {token}"})
            if r.status_code != 200:
                raise http_error(r, "YouTube stats", ERR_SNIPPET)
            for item in r.json().get("items", []):
                st = item.get("statistics", {})
                out[item["id"]] = stats(st.get("viewCount"), st.get("likeCount"),
                                        st.get("commentCount"))
        return out

    async def account(self) -> dict | None:
        token = await self.oauth.access_token("youtube")
        r = await self.http.get(CHANNELS_URL, timeout=self.timeout,
                                params={"part": "statistics", "mine": "true"},
                                headers={"Authorization": f"Bearer {token}"})
        if r.status_code != 200:
            raise http_error(r, "YouTube channel stats", ERR_SNIPPET)
        items = r.json().get("items", [])
        if not items:
            return None
        st = items[0].get("statistics", {})
        return {"followers": int(st.get("subscriberCount", 0) or 0),
                "total_views": int(st.get("viewCount", 0) or 0),
                "posts": int(st.get("videoCount", 0) or 0)}
