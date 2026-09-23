"""Content safety: keep slurs (and optionally profanity) out of everything Ashvane posts.

Only slurs and derogatory terms are blocked by default (casual slang like the "-a" form is left
alone on purpose; add it under Settings → Safety → Also block if you want it gone).
Used on post text (titles, descriptions, captions, hashtags, first comments), on the captions
burned into the video, and to skip clips where someone says a slur. Matching is per word after
normalising case, leetspeak (n1gg4) and stretched letters (fuuuck); a few unambiguous stems also
match their longer forms. The built-in word lists are stored base64-encoded so the source
doesn't carry them in plain text; your own extra/allowed words come from Settings → Safety.
"""
from __future__ import annotations

import base64
import re
from functools import lru_cache

from .config import Settings

_LISTS = {
    "slurs": "bmlnZ2VyIG5pZ2dlcnMgbmlnbGV0IGNvb24gamlnYWJvbyBwb3JjaG1vbmtleSBjaGluayBjaGlua3kgZ29vayB6aXBwZXJoZWFkIHNwaWMgc3BpY2sgd2V0YmFjayBiZWFuZXIga2lrZSBoZWViIHJhZ2hlYWQgdG93ZWxoZWFkIHNhbmRuaWdnZXIgcGFraSByZWRza2luIHNxdWF3IGZhZ2dvdCBmYWcgZmFnZyBkeWtlIHRyYW5ueSBzaGVtYWxlIHJldGFyZCByZXRhcmRlZCBtb25nb2xvaWQ=",
    "strong": "ZnVjayBmdWNrZXIgZnVja2luZyBmdWNrZWQgbW90aGVyZnVja2VyIGN1bnQgc2hpdCBiaXRjaCB3aG9yZSBzbHV0IGNvY2sgZGljayBwdXNzeSBiYXN0YXJkIGFzc2hvbGUgdHdhdCB3YW5rZXI=",
    "mild": "ZGFtbiBoZWxsIGFzcyBjcmFwIHBpc3MgYm9sbG9ja3MgYmxvb2R5",
}
_STEMS = "bmlnZ2VyIGZhZ2cgZnVjayBtb3RoZXJmdWNrIHNoaXQgYml0Y2g="
LEVELS = {"slurs": ("slurs",), "strong": ("slurs", "strong"), "all": ("slurs", "strong", "mild")}
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t",
                       "@": "a", "$": "s", "!": "i"})
_WORD = re.compile(r"[\w@$!*]+", re.UNICODE)
_NON_LETTER = re.compile(r"[^a-z]")
_STRETCH = re.compile(r"(.)\1{2,}")
_DOUBLE = re.compile(r"(.)\1+")


def _decode(b: str) -> frozenset[str]:
    return frozenset(base64.b64decode(b).decode().split())


@lru_cache(maxsize=None)
def _words(tier: str) -> frozenset[str]:
    return _decode(_LISTS[tier])


@lru_cache(maxsize=None)
def _stems() -> frozenset[str]:
    return _decode(_STEMS)


def normalise(token: str) -> str:
    t = _NON_LETTER.sub("", token.lower().translate(_LEET))
    return _STRETCH.sub(r"\1\1", t)                  # fuuuuck -> fuuck


def _variants(token: str) -> set[str]:
    t = normalise(token)
    return {t, _DOUBLE.sub(r"\1", t)}                # also fully collapsed: fuuck -> fuck


class Filter:
    def __init__(self, settings: Settings) -> None:
        sf = settings.safety
        extra = {normalise(w) for w in sf.extra_words if normalise(w)}
        self.enabled = sf.enabled
        self.banned = set().union(*(_words(t) for t in LEVELS[sf.level])) | extra
        self.slurs = set(_words("slurs")) | extra
        self.allowed = {normalise(w) for w in sf.allowed_words if normalise(w)}

    def _hit(self, token: str, words: set[str]) -> bool:
        stems = {s for s in _stems() if any(w.startswith(s) for w in words)}
        for v in _variants(token):
            if not v or v in self.allowed:
                continue
            if v in words or any(v.startswith(s) for s in stems):
                return True
        return False

    def bad(self, token: str) -> bool:
        return self.enabled and self._hit(token, self.banned)

    def slurs_in(self, text: str) -> list[str]:
        """Slurs said in ``text`` (independent of the masking level)."""
        return [m.group(0) for m in _WORD.finditer(text or "") if self._hit(m.group(0), self.slurs)]

    def mask(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        return _WORD.sub(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1)
                         if self.bad(m.group(0)) else m.group(0), text)

    def clean(self, value):
        """Mask every string inside dicts/lists (post copy); drop banned one-word tags."""
        if isinstance(value, str):
            return self.mask(value)
        if isinstance(value, list):
            return [self.clean(v) for v in value
                    if not (isinstance(v, str) and len(v.split()) == 1 and self.bad(v.lstrip("#")))]
        if isinstance(value, dict):
            return {k: self.clean(v) for k, v in value.items()}
        return value
