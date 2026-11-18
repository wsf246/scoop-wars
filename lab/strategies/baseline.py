"""Baseline: the agent as it played tonight (play.py on main).

Two searches with the clue itself, at once: any language, and Chinese or Indonesian only. Then one Claude
call (sonnet, low effort) picks the article with the rules in prompt.md.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

PROMPT = (Path(__file__).resolve().with_name("baseline_prompt.md")).read_text(encoding="utf-8").strip()
LANES = [("any_language", None), ("chinese_or_indonesian", ["zh", "id"])]


def retrieve(clue: str, tools) -> dict:
    """The searches for this clue; returns what the picker sees: {lane name: [articles]}."""
    requests = []
    for name, language in LANES:
        body = {"query": clue[:500], "count": 10, "time_range": "day", "highlight": {"enable": True, "max_tokens": 200}}
        if language:
            body["language"] = language
        requests.append({"name": name, "body": body})
    done = tools.search_many(requests)
    return {d["name"]: [{**a, "text": a["text"][:900]} for a in d["articles"]] for d in done}


def answer(clue: str, tools) -> dict:
    results = retrieve(clue, tools)
    prompt = f"now: {datetime.now(timezone.utc):%Y-%m-%dT%H:00:00Z}\nClue: {clue}\n\nSearch results:\n{json.dumps(results, ensure_ascii=False)}\n"
    return tools.ask(PROMPT, prompt, model="sonnet", effort="low") or {"url": "", "summary": ""}
