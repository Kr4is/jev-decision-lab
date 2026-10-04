from __future__ import annotations

from typing import Protocol

from ..schema import Decision


class Backend(Protocol):
    name: str

    def decide(self, text: str) -> Decision: ...
