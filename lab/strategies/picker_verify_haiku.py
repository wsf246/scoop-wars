"""Two steps: the baseline pick (sonnet low, prompt.md, cached), then a second call that only checks the pick against the clue."""

import json

from strategies import baseline
from strategies import picker_common as pc

VERIFY_PROMPT = f"""You check one answer in the game Scoop Wars. A clue describes one news story published in the last few hours, without the names or words in its headline. An agent searched the news and chose one article as the story of the clue. About one clue in five is a FAKE: a real story from today with one fact changed (another number, country, city, date or time of day, the opposite direction or reaction, a swapped cause, person or group). A fake matches a real article on everything else, so a close match is not enough. Linking an article for a fake loses points; rejecting a real clue also loses points.

You get the clue, the chosen article with the agent's summary, and all the search results (other articles on the same story can state details the chosen one leaves out).

`checks`: one entry for each concrete detail of the clue: every number, amount, count, age, score, date, weekday and time of day; every place and nationality; who did what to whom; the cause or reason; every direction (ahead or behind, rose or fell, from which into which); every reaction (praised or questioned). In each entry, `clue` is the detail as the clue states it, in a few words; `articles` is what the chosen article and the other articles about the same story state for it, in a few words copied from them, or "not stated" when none mentions it; `same` is false only when the articles state it differently. {pc.NOT_DIFFERENCES}

`verdict`: "reject" when any check has `same` false or the chosen article is about another event; otherwise "keep"."""

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "checks": pc.PAIRS_SCHEMA["properties"]["checks"],
        "verdict": {"type": "string", "enum": ["keep", "reject"]},
    },
    "required": ["checks", "verdict"],
    "additionalProperties": False,
}
MODEL, EFFORT = "haiku", "low"


def retrieve(clue, tools):
    return pc.retrieve(clue, tools)


def verify(clue: str, card: dict, results: dict, tools, model: str, effort: str) -> dict:
    chosen = next((a for lane in results.values() for a in lane if a.get("url") == card["url"]), None)
    prompt = (
        f"Clue: {clue}\n\nChosen article:\n{json.dumps(chosen or {'url': card['url']}, ensure_ascii=False)}\n"
        f"Agent's summary: {card.get('summary', '')}\n\nAll search results:\n{json.dumps(results, ensure_ascii=False)}\n"
    )
    return tools.ask(VERIFY_PROMPT, prompt, schema=VERIFY_SCHEMA, model=model, effort=effort) or {}


def answer(clue, tools, model: str = MODEL, effort: str = EFFORT):
    results = retrieve(clue, tools)
    card = tools.ask(baseline.PROMPT, pc.user_prompt(clue, results), model="sonnet", effort="low") or {"url": "", "summary": ""}
    pc.RAW[clue] = {"pick": card}
    if not card.get("url"):
        return card
    check = verify(clue, card, results, tools, model, effort)
    pc.RAW[clue]["verify"] = check
    failed = [c for c in check.get("checks") or [] if isinstance(c, dict) and c.get("same") is False]
    if failed or check.get("verdict") == "reject":
        why = f"the clue says '{failed[0].get('clue')}' but the story says '{failed[0].get('articles')}'" if failed else "the article is about another event"
        return {"url": "", "summary": f"No link: {why}."}
    return card
