"""Quick fix B on the faster model: haiku, with the detail checks before the url."""

import json
from datetime import datetime, timezone

from strategies import baseline, main_quick_b


def retrieve(clue, tools):
    return baseline.retrieve(clue, tools)


def answer(clue, tools):
    results = retrieve(clue, tools)
    prompt = f"now: {datetime.now(timezone.utc):%Y-%m-%dT%H:00:00Z}\nClue: {clue}\n\nSearch results:\n{json.dumps(results, ensure_ascii=False)}\n"
    return tools.ask(main_quick_b.PROMPT, prompt, schema=main_quick_b.SCHEMA, model="haiku", effort="") or {"url": "", "summary": ""}
