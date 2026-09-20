"""TikTok Content Posting API: creator info → init → chunked PUT → status poll.

Apps that have not passed TikTok's audit may only post with privacy SELF_ONLY.
"""
from __future__ import annotations

from pathlib import Path

from ..util import ERR_SNIPPET
from .base import PostText, Publisher, PublishError, PublishResult, read_bytes, stats
from .oauth import OAuthManager

API = "https://open.tiktokapis.com/v2/post/publish"
VIDEO_QUERY = "https://open.tiktokapis.com/v2/video/query/"
USER_INFO = "https://open.tiktokapis.com/v2/user/info/"
IDS_PER_CALL = 20        # API limit
CAPTION_MAX = 2200                 # API limit for post_info.title
MIN_CHUNK = 5 * 1024 * 1024        # API: chunks are 5–64 MB except a single small upload
MAX_CHUNK = 64 * 1024 * 1024


def chunk_plan(size: int, chunk_bytes: int) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) per TikTok's rules: the last chunk absorbs the
    remainder; files up to 5 MB go in one chunk."""
    if size <= MIN_CHUNK:
        return size, 1
    chunk = min(max(chunk_bytes, MIN_CHUNK), MAX_CHUNK)
    return chunk, max(1, size // chunk)


class TikTokPublisher(Publisher):
    name = "tiktok"

    def __init__(self, settings, http, tokens, oauth: OAuthManager) -> None:
        super().__init__(settings, http, tokens)
        self.oauth = oauth

    def configured(self) -> tuple[bool, str]:
        a = self.settings.accounts
        if not (a.tiktok_client_key and a.tiktok_client_secret):
            return False, "TikTok client key/secret missing (Settings → Accounts)"
        if not a.tiktok_redirect_uri:
            return False, "TikTok redirect URI missing (Settings → Accounts)"
        if not self.oauth.connected("tiktok"):
            return False, "TikTok account not connected (Settings → Accounts → Connect)"
        return True, ""

    async def _api(self, token: str, path: str, body: dict) -> dict:
        r = await self.http.post(f"{API}/{path}", json=body, timeout=self.timeout,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json; charset=UTF-8"})
        data = r.json() if r.content else {}
        err = data.get("error", {}) if isinstance(data, dict) else {}
        if r.status_code != 200 or err.get("code") not in (None, "ok"):
            raise PublishError(f"TikTok {path} HTTP {r.status_code}: {err.get('code')} "
                               f"{err.get('message') or r.text[:ERR_SNIPPET]}")
        return data.get("data", {}) or {}

    async def _publish(self, video: Path, post: PostText) -> PublishResult:
        cfg = self.settings.posting.tiktok
        token = await self.oauth.access_token("tiktok")
        creator = await self._api(token, "creator_info/query/", {})
        options = creator.get("privacy_level_options") or []
        if options and cfg.privacy not in options:
            raise PublishError(f"TikTok privacy {cfg.privacy} not allowed for this account "
                               f"(allowed: {', '.join(options)})")
        data = await read_bytes(video)
        size = len(data)
        chunk, count = chunk_plan(size, cfg.chunk_mb * 1024 * 1024)
        caption = f"{post.title}\n\n{post.caption}".strip()[:CAPTION_MAX]
        init = await self._api(token, "video/init/", {
            "post_info": {"title": caption, "privacy_level": cfg.privacy, "disable_duet": False,
                          "disable_comment": False, "disable_stitch": False},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk,
                            "total_chunk_count": count}})
        publish_id, upload_url = init.get("publish_id"), init.get("upload_url")
        if not (publish_id and upload_url):
            raise PublishError(f"TikTok init returned no upload URL: {init}")
        for i in range(count):
            start = i * chunk
            end = size if i == count - 1 else start + chunk
            r = await self.http.put(upload_url, content=data[start:end], timeout=self.timeout,
                                    headers={"Content-Type": "video/mp4",
                                             "Content-Range": f"bytes {start}-{end - 1}/{size}"})
            if r.status_code not in (200, 201, 206):
                raise PublishError(f"TikTok chunk {i + 1}/{count} HTTP {r.status_code}: "
                                   f"{r.text[:ERR_SNIPPET]}")

        async def check():
            st = await self._api(token, "status/fetch/", {"publish_id": publish_id})
            status = st.get("status", "")
            if status == "PUBLISH_COMPLETE":
                # the API really spells it "publicaly"
                ids = st.get("publicaly_available_post_id") or []
                return str(ids[0]) if ids else publish_id
            if status == "FAILED":
                raise PublishError(f"TikTok processing failed: {st.get('fail_reason', 'unknown')}")
            if status == "SEND_TO_USER_INBOX":
                return publish_id
            return None

        post_id = await self.poll(check, "TikTok")
        url = "" if post_id == publish_id else f"https://www.tiktok.com/video/{post_id}"
        return PublishResult(True, remote_id=post_id, url=url)

    async def metrics(self, remote_ids: list[str]) -> dict[str, dict]:
        token = await self.oauth.access_token("tiktok")
        out: dict[str, dict] = {}
        for i in range(0, len(remote_ids), IDS_PER_CALL):
            r = await self.http.post(
                VIDEO_QUERY, timeout=self.timeout,
                params={"fields": "id,view_count,like_count,comment_count,share_count"},
                json={"filters": {"video_ids": remote_ids[i:i + IDS_PER_CALL]}},
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json; charset=UTF-8"})
            if r.status_code != 200:
                raise PublishError(f"TikTok video stats HTTP {r.status_code}: {r.text[:ERR_SNIPPET]}")
            for v in (r.json().get("data") or {}).get("videos", []):
                out[str(v.get("id"))] = stats(v.get("view_count"), v.get("like_count"),
                                              v.get("comment_count"), v.get("share_count"))
        return out

    async def account(self) -> dict | None:
        token = await self.oauth.access_token("tiktok")
        r = await self.http.get(USER_INFO, timeout=self.timeout,
                                params={"fields": "follower_count,likes_count,video_count"},
                                headers={"Authorization": f"Bearer {token}"})
        if r.status_code != 200:
            raise PublishError(f"TikTok user stats HTTP {r.status_code}: {r.text[:ERR_SNIPPET]}")
        u = (r.json().get("data") or {}).get("user", {})
        return {"followers": int(u.get("follower_count", 0) or 0),
                "total_views": int(u.get("likes_count", 0) or 0),
                "posts": int(u.get("video_count", 0) or 0)}
