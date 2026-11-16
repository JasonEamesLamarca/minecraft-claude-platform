"""Cleaning of text crossing a trust boundary (player -> AI, AI -> player)."""
from __future__ import annotations

import re
import unicodedata

_FORMAT_CODE = re.compile("§.?", re.S)        # Minecraft formatting codes
_MD_FENCE = re.compile(r"```[a-zA-Z0-9_-]*")
_MD_EMPH = re.compile(r"(\*\*|__|`)")
_SPACES = re.compile(r"[ \t]{2,}")


def _drop_controls(text: str, keep_newlines: bool) -> str:
    out = []
    for ch in text:
        if ch == "\n" and keep_newlines:
            out.append(ch)
        elif ch in "\n\r\t":
            out.append(" ")
        elif unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs"):   # controls, bidi/format, private use
            continue
        else:
            out.append(ch)
    return "".join(out)


def clean_player_text(text: str, max_chars: int) -> str:
    """Normalize a player's request. Raises ValueError when empty or too long (rejected, not truncated)."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    text = unicodedata.normalize("NFKC", text)
    text = _FORMAT_CODE.sub("", _drop_controls(text, keep_newlines=False))
    text = _SPACES.sub(" ", text).strip()
    if not text:
        raise ValueError("Your request is empty.")
    if len(text) > max_chars:
        raise ValueError(f"Your request is too long ({len(text)} > {max_chars} characters).")
    return text


def clean_reply(text: str, max_chars: int) -> str:
    """Make model output safe and readable as plain chat text."""
    if not isinstance(text, str):
        text = str(text)
    text = unicodedata.normalize("NFKC", text)
    text = _FORMAT_CODE.sub("", _drop_controls(text, keep_newlines=True))
    text = _MD_FENCE.sub("", text)
    text = _MD_EMPH.sub("", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)          # markdown headings
    text = re.sub(r"\n{2,}", "\n", text)
    text = " ".join(line.strip() for line in text.split("\n") if line.strip())
    text = _SPACES.sub(" ", text).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text or "(no answer)"


def clean_name(name: str) -> str:
    """Player names are 3-16 of [A-Za-z0-9_]; anything else is replaced (names reach the prompt)."""
    return name if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_]{1,16}", name) else "Player"
