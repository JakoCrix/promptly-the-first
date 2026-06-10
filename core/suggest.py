import logging
import re

import google.generativeai as genai

from core.config import GOOGLE_API_KEY, MIN_RETIRED_FOR_WEAVING

log = logging.getLogger(__name__)

genai.configure(api_key=GOOGLE_API_KEY)
_MODEL = genai.GenerativeModel("gemini-2.5-flash")


def generate_sentence(
    word: str,
    topic: str,
    description: str,
    hint: str | None = None,
    retired_words: list[str] | None = None,
) -> str | None:
    """Generate one fresh sentence for a vocab card. Returns None on any failure."""
    hint_line = f'Pronunciation and meaning: "{hint}"\n' if hint else ""
    retired_line = ""
    if retired_words and len(retired_words) >= MIN_RETIRED_FOR_WEAVING:
        retired_line = (
            f"You may also naturally weave in some of these words the learner has already mastered, "
            f"if they fit: {', '.join(retired_words)}. "
            "Do not force them — only include words that belong naturally. "
            "Prioritise flow over how many you include.\n"
        )
    prompt = (
        f'Generate exactly one natural example sentence that uses the word or phrase "{word}" '
        f'in the context of the topic "{topic}".\n'
        f"{hint_line}"
        f"Topic description: {description or topic}\n"
        f"{retired_line}"
        "Return only the sentence itself — no labels, no quotes, no extra text."
    )
    try:
        response = _MODEL.generate_content(prompt)
        return response.text.strip() or None
    except Exception as exc:
        log.error("generate_sentence failed: %s: %s", type(exc).__name__, exc)
        return None


def format_sentence_words(sentence: str, main_word: str, retired_words: list[str]) -> str:
    """Underline+bold the main word and underline retired words in a single regex pass."""
    def _pattern(word: str) -> str:
        escaped = re.escape(word)
        return (r'\b' + escaped + r'\b') if word.isascii() else escaped

    # Exclude main word from retired list to prevent double-wrapping.
    filtered_retired = [w for w in retired_words if w.lower() != main_word.lower()]
    sorted_retired = sorted(filtered_retired, key=len, reverse=True)

    parts = [f"(?P<main>{_pattern(main_word)})"]
    if sorted_retired:
        parts.append("(?P<retired>" + "|".join(_pattern(w) for w in sorted_retired) + ")")

    combined = re.compile("|".join(parts), re.IGNORECASE)

    def _replace(m: re.Match) -> str:
        return f"<u><b>{m.group()}</b></u>" if m.group("main") else f"<u>{m.group()}</u>"

    result = combined.sub(_replace, sentence)
    if result == sentence:
        log.debug("format_sentence_words: no match for '%s' in: %.80s", main_word, sentence)
    return result
