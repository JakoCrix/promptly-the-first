import json
import logging
import re

import google.generativeai as genai

from core.config import GOOGLE_API_KEY, SUGGEST_BATCH_SIZE

log = logging.getLogger(__name__)

genai.configure(api_key=GOOGLE_API_KEY)
_MODEL = genai.GenerativeModel("gemini-2.5-flash")


def fetch_suggestions(topic: str, description: str, existing_words: list[str]) -> list[tuple[str, str]]:
    """Call Gemini and return up to SUGGEST_BATCH_SIZE (word, sentence) tuples.

    Filters out any suggestions whose word appears in existing_words (case-insensitive).
    Returns an empty list if the API call or JSON parse fails.
    """

    existing_lower = {w.lower() for w in existing_words}
    existing_str = ", ".join(existing_words[:30])

    prompt = (
        f'You are a vocabulary tutor. Topic: "{topic}".\n'
        f"Description: {description}\n"
        f"Existing words (do NOT repeat these): {existing_str}\n\n"
        f"Suggest {SUGGEST_BATCH_SIZE} new vocabulary words for this topic.\n"
        "Return ONLY a JSON array of objects, each with \"word\" and \"sentence\" keys.\n"
        "The sentence should use the word naturally in context.\n"
        'Example: [{"word": "giraffe", "sentence": "The giraffe stretched its long neck to reach the leaves."}]'
    )

    try:
        response = _MODEL.generate_content(prompt)
        text = response.text.strip()
        # Strip markdown code fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text)
    except Exception as exc:
        log.error("fetch_suggestions failed: %s: %s", type(exc).__name__, exc)
        return []

    results = []
    for item in parsed:
        word = item.get("word", "").strip()
        sentence = item.get("sentence", "").strip()
        if word and sentence and word.lower() not in existing_lower:
            results.append((word, sentence))

    return results


def generate_sentence(word: str, topic: str, description: str) -> str | None:
    """Generate one fresh sentence for a vocab card. Returns None on any failure."""
    prompt = (
        f'Generate exactly one natural sentence that uses the word or phrase "{word}" '
        f'in the context of the topic "{topic}".\n'
        f"Topic description: {description or topic}\n"
        "Return only the sentence itself — no labels, no quotes, no extra text."
    )
    try:
        response = _MODEL.generate_content(prompt)
        return response.text.strip() or None
    except Exception as exc:
        log.error("generate_sentence failed: %s: %s", type(exc).__name__, exc)
        return None
