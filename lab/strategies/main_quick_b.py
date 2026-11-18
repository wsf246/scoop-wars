"""Quick fix B: as A, and the answer lists each clue detail checked against the article before the url."""

import json
from datetime import datetime, timezone

from strategies import baseline, main_quick_a

SCHEMA = {
    "type": "object",
    "properties": {
        "checks": {
            "type": "array",
            "description": "Each concrete detail of the clue, checked against the chosen article",
            "items": {
                "type": "object",
                "properties": {"detail": {"type": "string"}, "article_says": {"type": "string"}, "verdict": {"type": "string", "enum": ["same", "different", "not stated"]}},
                "required": ["detail", "article_says", "verdict"],
                "additionalProperties": False,
            },
        },
        "url": {"type": "string", "description": "URL of the article, or empty when any check is 'different' or no article reports the story"},
        "summary": {"type": "string", "description": "One-sentence English summary"},
    },
    "required": ["checks", "url", "summary"],
    "additionalProperties": False,
}
PROMPT = main_quick_a.PROMPT + "\n\nFill `checks` first, one entry per concrete detail of the clue, then the url. If any check is 'different', the url is empty."


def retrieve(clue, tools):
    return baseline.retrieve(clue, tools)


def answer(clue, tools):
    results = retrieve(clue, tools)
    prompt = f"now: {datetime.now(timezone.utc):%Y-%m-%dT%H:00:00Z}\nClue: {clue}\n\nSearch results:\n{json.dumps(results, ensure_ascii=False)}\n"
    return tools.ask(PROMPT, prompt, schema=SCHEMA, model="sonnet", effort="low") or {"url": "", "summary": ""}
