"""Structural contract for all session graph nodes."""
from __future__ import annotations

from typing import Protocol

from langchain_learning.session_state import SessionState


class NodeCallable(Protocol):
    def __call__(self, state: SessionState) -> dict:
        """Execute node logic and return partial state updates."""
        ...
