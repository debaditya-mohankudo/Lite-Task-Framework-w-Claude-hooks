"""Shared stopword set — loaded once at import, used by score.py and session.py."""
import json
from pathlib import Path

_STOPWORDS_FILE = Path(__file__).parent / "stopwords.json"

_PATH_RE_STR = r"^[./]|/"   # token contains a slash or starts with . or /


def _load() -> set[str]:
    try:
        data = json.loads(_STOPWORDS_FILE.read_text())
        return (
            set(data.get("grammar", []))
            | set(data.get("noise", []))
            | set(data.get("path_artifacts", []))
        )
    except Exception:
        return set()


STOPWORDS: set[str] = _load()


def is_noise(token: str) -> bool:
    """True if token should be excluded from persisted keyword sets."""
    import re
    if token in STOPWORDS:
        return True
    if len(token) < 3:
        return True
    # path fragments: contains slash or starts with dot
    if re.search(r"[/\\]", token):
        return True
    if token.startswith("."):
        return True
    # pure numeric or hex ids (e.g. "da79abd7", "8765", "501")
    if re.fullmatch(r"[0-9a-f]{6,}", token):
        return True
    # tool-call ids: "toolu_01..." pattern
    if re.match(r"toolu_[0-9a-z]+", token):
        return True
    # task/job short ids: 8–12 lowercase alphanum with no vowels (e.g. "bmwkjjphj")
    if re.fullmatch(r"[bcdfghjklmnpqrstvwxyz0-9]{6,12}", token):
        return True
    return False


def filter_keywords(keywords: set[str]) -> set[str]:
    """Return only meaningful keywords — strips stopwords, paths, ids."""
    return {t for t in keywords if not is_noise(t)}
