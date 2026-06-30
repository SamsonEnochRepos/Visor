"""
voice_intent.py — Voice-command → Intent resolver for VISOR.

Converts raw transcription text from the Vosk speech engine into
``IntentResult`` objects.  Each voice command is mapped to an ``Intent``
plus a context dict that the action layer can execute directly.

The matching is intentionally simple (keyword / prefix): the voice
engine already constrains the vocabulary, so a full NLU parser would
be overkill.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from visor.core.types import (
    GestureResult,
    Intent,
    IntentResult,
)

logger = logging.getLogger("VISOR.intent.voice")

# Type alias for a single rule: (pattern, intent, context_factory).
# The factory receives the ``re.Match`` so it can extract capture groups.
_Rule = Tuple[re.Pattern[str], Intent, Any]


class VoiceIntentResolver:
    """Map free-text voice commands to structured ``IntentResult`` objects.

    Rules are evaluated top-to-bottom; the first match wins.  Longer /
    more specific patterns are therefore listed before short ones.

    Example::

        resolver = VoiceIntentResolver()
        result = resolver.resolve("open calculator")
        # IntentResult(intent=Intent.APP_LAUNCH, context={"app": "calculator"}, ...)
    """

    def __init__(self) -> None:
        self._rules: List[_Rule] = self._build_rules()

    # ── Public API ──────────────────────────────────────────────────────

    def resolve(self, text: str) -> Optional[IntentResult]:
        """Try to match *text* against the voice-command table.

        Args:
            text: Transcribed speech fragment (typically lower-cased by
                the speech engine).

        Returns:
            An ``IntentResult`` on match, or ``None`` if the text does
            not correspond to any known command.
        """
        if not text or not text.strip():
            return None

        normalised = text.strip().lower()
        logger.debug("Voice input: '%s'", normalised)

        for pattern, intent, ctx_factory in self._rules:
            match = pattern.search(normalised)
            if match:
                context: Dict[str, Any] = (
                    ctx_factory(match) if callable(ctx_factory) else dict(ctx_factory)
                )
                logger.info(
                    "Voice matched: '%s' → %s  context=%s",
                    normalised, intent.value, context,
                )
                return IntentResult(
                    intent=intent,
                    gesture=GestureResult.idle(),
                    context=context,
                    confidence=1.0,
                )

        logger.debug("No voice match for: '%s'", normalised)
        return None

    # ── Rule Table ──────────────────────────────────────────────────────

    @staticmethod
    def _build_rules() -> List[_Rule]:
        """Construct the ordered rule table.

        Rules are tried top-to-bottom; first match wins.  Place more
        specific patterns *before* generic ones.
        """
        rules: List[_Rule] = [
            # ── Scrolling ────────────────────────────────────────────
            (
                re.compile(r"\bscroll\s+up\b"),
                Intent.SCROLL,
                {"direction": "up", "amount": 10},
            ),
            (
                re.compile(r"\bscroll\s+down\b"),
                Intent.SCROLL,
                {"direction": "down", "amount": 10},
            ),

            # ── Search (before generic "open") ──────────────────────
            (
                re.compile(r"\bsearch\s+(.+)"),
                Intent.APP_LAUNCH,
                lambda m: {
                    "app": "browser",
                    "url": (
                        "https://www.google.com/search?q="
                        + urllib.parse.quote_plus(m.group(1).strip())
                    ),
                },
            ),

            # ── App launch ──────────────────────────────────────────
            (
                re.compile(r"\bopen\s+(.+)"),
                Intent.APP_LAUNCH,
                lambda m: {"app": m.group(1).strip()},
            ),

            # ── Close ───────────────────────────────────────────────
            (
                re.compile(r"\bclose\b"),
                Intent.CLOSE,
                {},
            ),

            # ── Volume ──────────────────────────────────────────────
            (
                re.compile(r"\bvolume\s+up\b"),
                Intent.VOICE_COMMAND,
                {"action": "press", "key": "volumeup"},
            ),
            (
                re.compile(r"\bvolume\s+down\b"),
                Intent.VOICE_COMMAND,
                {"action": "press", "key": "volumedown"},
            ),
            (
                re.compile(r"\bmute\b"),
                Intent.VOICE_COMMAND,
                {"action": "press", "key": "volumemute"},
            ),

            # ── Screenshot ──────────────────────────────────────────
            (
                re.compile(r"\bscreenshot\b"),
                Intent.VOICE_COMMAND,
                {"action": "hotkey", "keys": ["win", "prtsc"]},
            ),

            # ── Window / desktop management ─────────────────────────
            (
                re.compile(r"\bswitch\s+window\b"),
                Intent.VOICE_COMMAND,
                {"action": "hotkey", "keys": ["alt", "tab"]},
            ),
            (
                re.compile(r"\bdesktop\b"),
                Intent.VOICE_COMMAND,
                {"action": "hotkey", "keys": ["win", "d"]},
            ),
            (
                re.compile(r"\bminimize\b"),
                Intent.VOICE_COMMAND,
                {"action": "hotkey", "keys": ["win", "down"]},
            ),
            (
                re.compile(r"\bmaximize\b"),
                Intent.VOICE_COMMAND,
                {"action": "hotkey", "keys": ["win", "up"]},
            ),

            # ── Tab management ──────────────────────────────────────
            (
                re.compile(r"\bnext\s+tab\b"),
                Intent.VOICE_COMMAND,
                {"action": "hotkey", "keys": ["ctrl", "tab"]},
            ),
            (
                re.compile(r"\bnew\s+tab\b"),
                Intent.VOICE_COMMAND,
                {"action": "hotkey", "keys": ["ctrl", "t"]},
            ),

            # ── Zoom ────────────────────────────────────────────────
            (
                re.compile(r"\bzoom\s+in\b"),
                Intent.VOICE_COMMAND,
                {"action": "hotkey", "keys": ["ctrl", "="]},
            ),
            (
                re.compile(r"\bzoom\s+out\b"),
                Intent.VOICE_COMMAND,
                {"action": "hotkey", "keys": ["ctrl", "-"]},
            ),
        ]
        return rules
