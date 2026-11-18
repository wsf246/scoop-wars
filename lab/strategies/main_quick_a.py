"""Quick fix A: the baseline plus a stricter fake check in the prompt (prompt-only change)."""

import json
from datetime import datetime, timezone

from strategies import baseline

EXTRA = """

Fake check, before you answer. Roughly one clue in five is a fake. Take the most specific details of the clue (each number, the country or city, which person or group did it, the direction or outcome, the day) and find each one in the chosen article's title or text. If the article states something different for any one of them, the clue is a fake: answer with an empty url. A detail the article does not mention is not a contradiction; a detail it states differently is."""
PROMPT = baseline.PROMPT + EXTRA


def retrieve(clue, tools):
    return baseline.retrieve(clue, tools)


def answer(clue, tools):
    results = retrieve(clue, tools)
    prompt = f"now: {datetime.now(timezone.utc):%Y-%m-%dT%H:00:00Z}\nClue: {clue}\n\nSearch results:\n{json.dumps(results, ensure_ascii=False)}\n"
    return tools.ask(PROMPT, prompt, model="sonnet", effort="low") or {"url": "", "summary": ""}
