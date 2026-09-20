"""Discord webhook: the clip as an attachment, or text only when it is too big."""
from __future__ import annotations

import json
from pathlib import Path

from ..util import ERR_SNIPPET
from .base import PostText, Publisher, PublishError, PublishResult, read_bytes

CONTENT_MAX = 2000   # Discord message limit


class DiscordPublisher(Publisher):
    name = "discord"

    def configured(self) -> tuple[bool, str]:
        if not self.settings.accounts.discord_webhook:
            return False, "Discord webhook URL missing (Settings → Accounts)"
        return True, ""

    async def _publish(self, video: Path, post: PostText) -> PublishResult:
        cfg = self.settings.posting.discord
        hook = self.settings.accounts.discord_webhook.rstrip("/")
        content = f"**{post.title}**\n{post.caption}".strip()[:CONTENT_MAX]
        payload = {"content": content, "username": cfg.username}
        size = Path(video).stat().st_size
        if size <= cfg.max_file_mb * 1024 * 1024:
            data = await read_bytes(video)
            r = await self.http.post(hook, params={"wait": "true"}, timeout=self.timeout,
                                     data={"payload_json": json.dumps(payload)},
                                     files={"files[0]": (Path(video).name, data, "video/mp4")})
        else:
            payload["content"] = (f"{content}\n(clip is {size / 1024 / 1024:.0f} MB, over the "
                                  f"attachment limit)")[:CONTENT_MAX]
            r = await self.http.post(hook, params={"wait": "true"}, json=payload,
                                     timeout=self.timeout)
        if r.status_code == 429:
            raise PublishError(f"Discord rate limited, retry after {r.json().get('retry_after')}s")
        if r.status_code not in (200, 204):
            raise PublishError(f"Discord HTTP {r.status_code}: {r.text[:ERR_SNIPPET]}")
        msg = r.json() if r.content else {}
        mid = str(msg.get("id", ""))
        url = (f"https://discord.com/channels/{msg.get('guild_id', '@me')}/"
               f"{msg.get('channel_id', '')}/{mid}") if mid else ""
        return PublishResult(True, remote_id=mid, url=url)

    async def delete(self, remote_id: str) -> PublishResult:
        if not remote_id:
            return PublishResult(False, error="no message id recorded")
        hook = self.settings.accounts.discord_webhook.rstrip("/")
        r = await self.http.delete(f"{hook}/messages/{remote_id}", timeout=self.timeout)
        if r.status_code in (204, 404):
            return PublishResult(True, remote_id=remote_id)
        return PublishResult(False, error=f"Discord delete HTTP {r.status_code}: "
                                          f"{r.text[:ERR_SNIPPET]}")
