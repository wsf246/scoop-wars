"""Shared pieces for the speed_* strategies: the frozen clock, prompt variants, candidate trimming."""

import json

from strategies import baseline

NOW = "2026-11-18T12:00:00Z"  # the hour of the baseline dev run: keeps every prompt identical across hours (cache hits)
LAST = "The summary is one sentence in English saying what happened, with the real names. When the url is empty, the summary says in one sentence why."
assert baseline.PROMPT.endswith(LAST)
SHORT_LAST = (
    "The summary is one short English sentence, 15 words at most, saying what happened, with the real names. "
    "When the url is empty, the summary is 8 words at most: the detail that does not match, or \"no article reports it\"."
)
PROMPT_SHORT = baseline.PROMPT[: -len(LAST)] + SHORT_LAST


def trim(results: dict, per_lane: int = 10, chars: int = 900) -> dict:
    return {lane: [{**a, "text": (a["text"] or "")[:chars]} for a in arts[:per_lane]] for lane, arts in results.items()}


def build_prompt(clue: str, results: dict) -> str:
    return f"now: {NOW}\nClue: {clue}\n\nSearch results:\n{json.dumps(results, ensure_ascii=False)}\n"
