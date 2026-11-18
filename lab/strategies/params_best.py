"""params_best: the baseline with one request-parameter change that matters, plus one free one.

1. The first search names its languages. With no `language` filter, News Search answers an English clue
   almost only with English articles (dev: 1,453 of 1,500 results), so a story covered only in Japanese,
   Spanish, French, Vietnamese, Arabic, Hindi, Turkish or Portuguese is never seen. Listing the languages
   explicitly (English plus the 15 others that are not Chinese or Indonesian) makes the ranking
   cross-lingual: the English article still comes first when one exists, and the local one when none does.
   Chinese and Indonesian stay in their own search, exactly as in the baseline.
2. subjects.enable = false on that search: the default story clusters are never used by the picker, almost
   never contain the story, and cost a second (subject) query per call. Results are identical without them.

Everything else is the baseline: count 10, time_range day, highlight 200 tokens, the prompt in prompt.md,
one Claude call (sonnet, low effort). Knobs below were used for the experiments in lab/notes/params.md.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

PROMPT = (Path(__file__).resolve().with_name("baseline_prompt.md")).read_text(encoding="utf-8").strip()

OTHER_LANGUAGES = ["ar", "de", "es", "fr", "hi", "it", "ja", "ko", "nl", "pl", "pt", "ru", "th", "tr", "vi"]
LANE_A_LANGUAGES = ["en"] + OTHER_LANGUAGES  # every language News Search supports except zh and id
LANE_B_LANGUAGES = ["zh", "id"]

HIGHLIGHT_TOKENS = 200  # 100..400 measured: no retrieval change, no latency change
FULL_CONTENT_TOKENS = 0  # > 0 adds full_content (first 10 results free); measured: no gain on fakes
TEXT_CHARS = 900  # what the picker sees of each article, as in the baseline

# The lab's searches are frozen in the cache, so the picker's clock is pinned to the hour they were made
# (the baseline run used the same value). In the game this must be the real time: set PARAMS_NOW=live.
PINNED_NOW = os.environ.get("PARAMS_NOW", "2026-11-18T12:00:00Z")


def now() -> str:
    return f"{datetime.now(timezone.utc):%Y-%m-%dT%H:00:00Z}" if PINNED_NOW == "live" else PINNED_NOW


def bodies(clue: str, highlight_tokens: int = HIGHLIGHT_TOKENS, full_content_tokens: int = FULL_CONTENT_TOKENS) -> list[dict]:
    a = {"query": clue[:500], "count": 10, "time_range": "day", "highlight": {"enable": True, "max_tokens": highlight_tokens},
         "language": LANE_A_LANGUAGES, "subjects": {"enable": False}}
    b = {"query": clue[:500], "count": 10, "time_range": "day", "highlight": {"enable": True, "max_tokens": highlight_tokens},
         "language": LANE_B_LANGUAGES}  # the baseline's second search, unchanged (subjects off is equally safe here)
    if full_content_tokens:
        for body in (a, b):
            body["full_content"] = {"enable": True, "max_tokens": full_content_tokens}
    return [{"name": "any_language", "body": a}, {"name": "chinese_or_indonesian", "body": b}]


def retrieve(clue: str, tools, highlight_tokens: int = HIGHLIGHT_TOKENS, full_content_tokens: int = FULL_CONTENT_TOKENS, text_chars: int = TEXT_CHARS) -> dict:
    """The two searches; returns what the picker sees: {lane name: [articles]}."""
    done = tools.search_many(bodies(clue, highlight_tokens, full_content_tokens))
    out = {}
    for d in done:
        raw = d["data"].get("results") or []
        arts = []
        for a, r in zip(d["articles"], raw):
            text = a["text"]
            if full_content_tokens and r.get("full_content"):
                text = r["full_content"]  # the article from its start, instead of query-relevant snippets
            arts.append({**a, "text": text[:text_chars]})
        out[d["name"]] = arts
    return out


def answer(clue: str, tools) -> dict:
    results = retrieve(clue, tools)
    prompt = f"now: {now()}\nClue: {clue}\n\nSearch results:\n{json.dumps(results, ensure_ascii=False)}\n"
    return tools.ask(PROMPT, prompt, model="sonnet", effort="low") or {"url": "", "summary": ""}
