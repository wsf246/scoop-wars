"""speed_base: the baseline, with `now` frozen to the hour of the lead's baseline dev run.

Identical prompts to baseline.py as it ran at 12:xx UTC, so every Claude call is a cache hit whatever the clock
says now (baseline.py itself puts the current hour in the prompt and stops hitting the cache when the hour changes).
"""

import json

from strategies import baseline

NOW = "2026-11-18T12:00:00Z"
retrieve = baseline.retrieve


def build_prompt(clue: str, results: dict) -> str:
    return f"now: {NOW}\nClue: {clue}\n\nSearch results:\n{json.dumps(results, ensure_ascii=False)}\n"


def answer(clue: str, tools) -> dict:
    results = retrieve(clue, tools)
    return tools.ask(baseline.PROMPT, build_prompt(clue, results), model="sonnet", effort="low") or {"url": "", "summary": ""}
