"""Shared pieces for the picker study: the baseline searches (cached) and a picker with a check-before-url schema.

The searches are exactly the baseline's (same request bodies, so they come from the cache). Only the Claude
call changes. `NOW` is fixed in the lab so that prompts stay identical from one hour to the next (the cache key
includes the prompt); in the game it is the current UTC hour, as in play.py.
"""

import json
import os
from datetime import datetime, timezone

from strategies import baseline

# The hour the baseline searches were made. Set LAB_NOW=live to use the clock (as the game does).
NOW = os.environ.get("LAB_NOW", "2026-11-18T12:00:00Z")
RAW: dict[str, dict] = {}  # clue text -> the model's raw answers, for inspection after a run


def now() -> str:
    return f"{datetime.now(timezone.utc):%Y-%m-%dT%H:00:00Z}" if NOW == "live" else NOW


def retrieve(clue: str, tools) -> dict:
    return baseline.retrieve(clue, tools)


def user_prompt(clue: str, results: dict) -> str:
    """The baseline's user message, byte for byte (so an unchanged system prompt hits the baseline's cache)."""
    return f"now: {now()}\nClue: {clue}\n\nSearch results:\n{json.dumps(results, ensure_ascii=False)}\n"


INTRO = """You are playing Scoop Wars. A clue describes one news story published in the last few hours, without the names or words in its headline. Under the clue are the results of two news searches made with it: `any_language`, and `chinese_or_indonesian` (only articles in those languages). Answer with the URL of one article from those results that reports the clue's story, or with an empty url.

About one clue in five is a FAKE: a real story from today with one fact changed (another number, country, city, date or time of day, the opposite direction or reaction, a swapped cause, person or group). A fake still matches a real article on everything else, so a close match is not enough: one changed fact makes it a fake. A link for a fake loses 2 points and a blank wins 2. For a real clue a wrong link loses 2 and a blank loses nothing, so never guess."""

CHOOSING = """- Language. If the story happens in mainland China, Hong Kong, Macau, Taiwan, Singapore or Malaysia, answer with a Chinese-language article. If it happens in Indonesia, answer with an Indonesian-language article. These must come from `chinese_or_indonesian`; use another language only when no article there reports the story. For stories anywhere else, answer with an English article when one reports it.
- Which article. Among the articles that report the story in the right language, choose the one with the earliest `published` time. Never choose an article published more than 24 hours before `now`. Copy the url exactly."""

NOT_DIFFERENCES = """These are NOT differences: a paraphrase or general description that fits ("a G7 economy" for the UK, "a search giant" for Google), a rounded number ("about 800 billion" for 808 billion, "over a quarter-million" for 252,000), a detail that no article mentions. When two stories in the results look alike, the closest story is the one that agrees with the clue's details. When articles about the story disagree with each other on a detail, the clue agrees if one of them does: choose that article."""

# --- one call: the story and the differences come before the url --------------------------------------
DIFF_PROMPT = f"""{INTRO}

Fill the answer in this order.

`story`: one sentence in English saying what the closest story in the results reports, with the real names and with ITS OWN numbers, places, dates, times and outcomes, taken from the articles and not from the clue.

`differences`: compare every concrete detail of the clue (each number, amount, count, age, score, date, weekday and time of day; each place and nationality; who did what to whom; the cause or reason; the direction, such as ahead or behind, rose or fell, more or fewer; the reaction, such as praised or questioned) with what the articles about that story state. List each detail the articles state differently, as "clue: X / articles: Y". {NOT_DIFFERENCES} Leave the list empty when nothing is stated differently.

`url`: empty when `differences` is not empty (the clue is a fake, or the story is another event of the same kind), and empty when no article clearly reports the story. Otherwise the url of one article that reports it:
{CHOOSING}
"""

DIFF_SCHEMA = {
    "type": "object",
    "properties": {
        "story": {"type": "string", "description": "One English sentence: what the closest story in the results reports, with real names and its own numbers, places, dates and outcomes. If nothing is close, say so."},
        "differences": {"type": "array", "items": {"type": "string"}, "description": "Each concrete detail of the clue that the articles state differently, as 'clue: X / articles: Y'. Empty when there is none."},
        "url": {"type": "string", "description": "URL of one article that reports the clue's story; empty when differences is not empty or no article reports it"},
    },
    "required": ["story", "differences", "url"],
    "additionalProperties": False,
}


def pick_with_differences(clue: str, tools, model: str = "sonnet", effort: str = "low", enforce: bool = True, system: str = DIFF_PROMPT, schema: dict = DIFF_SCHEMA) -> dict:
    """One call. With `enforce`, a non-empty `differences` blanks the card whatever url the model wrote."""
    results = retrieve(clue, tools)
    raw = tools.ask(system, user_prompt(clue, results), schema=schema, model=model, effort=effort)
    RAW[clue] = {"pick": raw}
    if not raw:
        return {"url": "", "summary": ""}
    url = str(raw.get("url") or "").strip()
    differences = [d for d in raw.get("differences") or [] if str(d).strip()]
    if differences and enforce:
        url = ""
    summary = str(raw.get("story") or "")
    if not url and differences:
        summary = f"No link: the clue differs from the closest story ({differences[0]})."
    return {"url": url, "summary": summary}


# --- one call: every clue detail is paired with what the articles state, before the url ----------------
PAIRS_PROMPT = f"""{INTRO}

Fill the answer in this order.

`story`: one sentence in English (at most 35 words) saying what the closest story in the results reports, with the real names and with ITS OWN numbers, places, dates, times and outcomes, taken from the articles and not from the clue.

`checks`: one entry for each concrete detail of the clue: every number, amount, count, age, score, date, weekday and time of day; every place and nationality; who did what to whom; the cause or reason; every direction (ahead or behind, rose or fell, from which into which); every reaction (praised or questioned). In each entry, `clue` is the detail as the clue states it, in a few words; `articles` is what the articles about that story state for it, in a few words copied from them, or "not stated" when none mentions it; `same` is false only when the articles state it differently. {NOT_DIFFERENCES}

`url`: empty when any check has `same` false (the clue is a fake, or the story is another event of the same kind), and empty when no article clearly reports the story. Otherwise the url of one article that reports it:
{CHOOSING}
"""

PAIRS_SCHEMA = {
    "type": "object",
    "properties": {
        "story": {"type": "string", "description": "One English sentence, at most 35 words: what the closest story in the results reports, with real names and its own numbers, places, dates and outcomes. If nothing is close, say so."},
        "checks": {
            "type": "array",
            "description": "One entry per concrete detail of the clue",
            "items": {
                "type": "object",
                "properties": {
                    "clue": {"type": "string", "description": "the detail as the clue states it, a few words"},
                    "articles": {"type": "string", "description": "what the articles state for it, a few words, or 'not stated'"},
                    "same": {"type": "boolean", "description": "false only when the articles state it differently"},
                },
                "required": ["clue", "articles", "same"],
                "additionalProperties": False,
            },
        },
        "url": {"type": "string", "description": "URL of one article that reports the clue's story; empty when any check has same=false or no article reports it"},
    },
    "required": ["story", "checks", "url"],
    "additionalProperties": False,
}


def pick_with_pairs(clue: str, tools, model: str = "sonnet", effort: str = "low", enforce: bool = True, system: str = PAIRS_PROMPT, schema: dict = PAIRS_SCHEMA) -> dict:
    """One call. With `enforce`, any check with same=false blanks the card whatever url the model wrote."""
    results = retrieve(clue, tools)
    raw = tools.ask(system, user_prompt(clue, results), schema=schema, model=model, effort=effort)
    RAW[clue] = {"pick": raw}
    if not raw:
        return {"url": "", "summary": ""}
    url = str(raw.get("url") or "").strip()
    failed = [c for c in raw.get("checks") or [] if isinstance(c, dict) and c.get("same") is False]
    if failed and enforce:
        url = ""
    summary = str(raw.get("story") or "")
    if not url and failed:
        summary = f"No link: the clue says '{failed[0].get('clue')}' but the closest story says '{failed[0].get('articles')}'."
    return {"url": url, "summary": summary}
