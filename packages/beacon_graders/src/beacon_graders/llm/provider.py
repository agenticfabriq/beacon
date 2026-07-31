"""LLMProvider protocol and in-process judge cache."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class JudgeRequest:
    prompt: str
    grader_version: str
    max_tokens: int = 1024
    system: str | None = None


@dataclass
class JudgeResponse:
    text: str
    tokens_input: int
    tokens_output: int
    model_version: str
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model_version: str

    def generate(self, request: JudgeRequest) -> JudgeResponse:
        """Issue a judge call for ``request`` and return the model response."""
        ...


class JudgeCache:
    """LRU cache keyed by prompt hash, model version, and grader version."""

    def __init__(self, *, provider: LLMProvider, max_size: int = 1024) -> None:
        self.provider = provider
        self._max_size = max_size
        self._lock = RLock()
        self._entries: OrderedDict[str, JudgeResponse] = OrderedDict()

    def _cache_key(self, request: JudgeRequest) -> str:
        digest = hashlib.sha256()
        digest.update(request.prompt.encode("utf-8"))
        if request.system:
            digest.update(b"\x00")
            digest.update(request.system.encode("utf-8"))
        return f"{digest.hexdigest()}::{self.provider.model_version}::{request.grader_version}"

    def get_or_call(self, request: JudgeRequest) -> JudgeResponse:
        """Return the cached judge response or call the provider and cache it."""
        key = self._cache_key(request)
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None:
                self._entries.move_to_end(key)
                return hit

        response = self.provider.generate(request)
        with self._lock:
            self._entries[key] = response
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_size:
                self._entries.popitem(last=False)
        return response
