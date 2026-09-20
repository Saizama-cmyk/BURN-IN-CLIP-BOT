"""Facebook Page Reels: video_reels start → rupload → finish (publish) → status poll."""
from __future__ import annotations

from pathlib import Path

from ..util import ERR_SNIPPET
from .base import PostText, Publisher, PublishError, PublishResult, http_error, read_bytes, stats

GRAPH_VERSION = "v26.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
RUPLOAD = f"https://rupload.facebook.com/video-upload/{GRAPH_VERSION}"


class FacebookPublisher(Publisher):
    name = "facebook"

    def configured(self) -> tuple[bool, str]:
        a = self.settings.accounts
        if not (a.facebook_page_id and a.facebook_page_token):
            return False, "Facebook Page ID/token missing (Settings → Accounts)"
        return True, ""

    async def _publish(self, video: Path, post: PostText) -> PublishResult:
        a = self.settings.accounts
        token, page = a.facebook_page_token, a.facebook_page_id
        r = await self.http.post(f"{GRAPH}/{page}/video_reels", timeout=self.timeout,
                                 data={"upload_phase": "start", "access_token": token})
        body = r.json()
        if r.status_code != 200 or "video_id" not in body:
            raise http_error(r, "Facebook reel start", ERR_SNIPPET)
        video_id = body["video_id"]
        upload_url = body.get("upload_url") or f"{RUPLOAD}/{video_id}"
        data = await read_bytes(video)
        up = await self.http.post(upload_url, content=data, timeout=self.timeout,
                                  headers={"Authorization": f"OAuth {token}", "offset": "0",
                                           "file_size": str(len(data))})
        if up.status_code != 200 or not up.json().get("success"):
            raise http_error(up, "Facebook reel upload", ERR_SNIPPET)
        description = f"{post.title}\n\n{post.caption}".strip()
        fin = await self.http.post(f"{GRAPH}/{page}/video_reels", timeout=self.timeout,
                                   data={"upload_phase": "finish", "video_id": video_id,
                                         "video_state": "PUBLISHED", "description": description,
                                         "access_token": token})
        if fin.status_code != 200 or not fin.json().get("success"):
            raise http_error(fin, "Facebook reel publish", ERR_SNIPPET)

        async def check():
            s = await self.http.get(f"{GRAPH}/{video_id}", timeout=self.timeout,
                                    params={"fields": "status", "access_token": token})
            st = s.json().get("status", {})
            if (st.get("video_status") == "error"
                    or st.get("processing_phase", {}).get("status") == "error"):
                raise PublishError(f"Facebook processing error: {st}")
            if (st.get("publishing_phase", {}).get("status") == "complete"
                    or st.get("video_status") == "ready"):
                return True
            return None

        await self.poll(check, "Facebook")
        return PublishResult(True, remote_id=video_id,
                             url=f"https://www.facebook.com/reel/{video_id}")

    async def delete(self, remote_id: str) -> PublishResult:
        r = await self.http.delete(f"{GRAPH}/{remote_id}", timeout=self.timeout,
                                   params={"access_token": self.settings.accounts.facebook_page_token})
        if r.status_code == 200:
            return PublishResult(True, remote_id=remote_id)
        return PublishResult(False, error=f"Facebook delete HTTP {r.status_code}: "
                                          f"{r.text[:ERR_SNIPPET]}")

    async def metrics(self, remote_ids: list[str]) -> dict[str, dict]:
        token = self.settings.accounts.facebook_page_token
        out: dict[str, dict] = {}
        for vid in remote_ids:
            r = await self.http.get(f"{GRAPH}/{vid}/video_insights", timeout=self.timeout,
                                    params={"metric": "blue_reels_play_count,"
                                                      "post_video_likes_by_reaction_type,"
                                                      "post_video_social_actions",
                                            "access_token": token})
            if r.status_code != 200:
                raise http_error(r, "Facebook reel insights", ERR_SNIPPET)
            vals = {m.get("name"): (m.get("values") or [{}])[0].get("value")
                    for m in r.json().get("data", [])}
            reactions = vals.get("post_video_likes_by_reaction_type") or {}
            social = vals.get("post_video_social_actions") or {}
            out[vid] = stats(vals.get("blue_reels_play_count"),
                             sum(reactions.values()) if isinstance(reactions, dict) else reactions,
                             social.get("COMMENT") if isinstance(social, dict) else 0,
                             social.get("SHARE") if isinstance(social, dict) else 0)
        return out

    async def account(self) -> dict | None:
        a = self.settings.accounts
        r = await self.http.get(f"{GRAPH}/{a.facebook_page_id}", timeout=self.timeout,
                                params={"fields": "followers_count,fan_count",
                                        "access_token": a.facebook_page_token})
        if r.status_code != 200:
            raise http_error(r, "Facebook Page stats", ERR_SNIPPET)
        b = r.json()
        return {"followers": int(b.get("followers_count") or b.get("fan_count") or 0),
                "total_views": 0, "posts": 0}
