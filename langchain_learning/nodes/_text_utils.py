"""Shared text utilities for node scoring."""
from __future__ import annotations

import re


def tokenise(text: str) -> set[str]:
    """Return set of lowercase tokens (3+ chars) from text."""
    return {t for t in re.findall(r"[a-z]{3,}", text.lower()) if t}
