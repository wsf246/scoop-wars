"""combined_v2: as v1, but the model only reads and checks; code chooses the article.

The model sees articles by short id (no urls) and answers with the story, the detail checks, the ids of every
article reporting the story, and where the story happens. Code then blanks on any failed check, keeps the
articles in the right language for that place, and takes the earliest published one (the first report).
"""

import json
import re

from strategies import params_best, picker_common as pc

PROMPT = """You are playing Scoop Wars. A clue describes one news story published in the last few hours, without the names or words in its headline. Under the clue are the results of two news searches made with it. Every article has an `id`: `A` articles are in English or another language, `B` articles are in Chinese or Indonesian. Say which articles report the clue's story, or that none does.

About one clue in five is a FAKE: a real story from today with one fact changed (another number, country, city, date or time of day, the opposite direction or reaction, a swapped cause, person or group). A fake still matches a real article on everything else, so a close match is not enough: one changed fact makes it a fake. A link for a fake loses 2 points and a blank wins 2. For a real clue a wrong link loses 2 and a blank loses nothing, so never guess.

Fill the answer in this order.

`story`: one sentence in English (at most 30 words) saying what the closest story in the results reports, with the real names and with ITS OWN numbers, places, dates, times and outcomes, taken from the articles and not from the clue.

`checks`: one entry for each concrete detail of the clue: every number, amount, count, age, score, date, weekday and time of day; every place and nationality; who did what to whom; the cause or reason; every direction (ahead or behind, rose or fell, from which into which); every reaction (praised or questioned). In each entry, `clue` is the detail as the clue states it, in a few words; `articles` is what the articles about that story state for it, in a few words copied from them, or "not stated" when none mentions it; `same` is false only when the articles state it differently. These are NOT differences: a paraphrase or general description that fits ("a G7 economy" for the UK, "a search giant" for Google), a rounded number ("about 800 billion" for 808 billion, "over a quarter-million" for 252,000), a detail that no article mentions. When two stories in the results look alike, the closest story is the one that agrees with the clue's details. When articles about the story disagree with each other on a detail, the clue agrees if one of them does.

`matches`: the ids of every article, from both lists, that reports this same story: the same event, not an older, later or merely similar one. Empty when any check has `same` false (the clue is a fake, or the story is another event of the same kind), and empty when no article clearly reports the story.

`place`: where the story happens: "chinese" for mainland China, Hong Kong, Macau, Taiwan, Singapore or Malaysia; "indonesia" for Indonesia; "elsewhere" for anywhere else."""

SCHEMA = {
    "type": "object",
    "properties": {
        "story": pc.PAIRS_SCHEMA["properties"]["story"],
        "checks": pc.PAIRS_SCHEMA["properties"]["checks"],
        "matches": {"type": "array", "items": {"type": "string"}, "description": "ids of the articles reporting the clue's story; empty when a check failed or none reports it"},
        "place": {"type": "string", "enum": ["chinese", "indonesia", "elsewhere"]},
    },
    "required": ["story", "checks", "matches", "place"],
    "additionalProperties": False,
}


def searches(clue, tools):
    bodies = params_best.bodies(clue)
    bodies[1]["body"]["subjects"] = {"enable": False}
    return tools.search_many(bodies)


def candidates(done):
    """{id: article} with the language each article is in: zh, id (Indonesian) or other."""
    out = {}
    for letter, d in zip("AB", done):
        for n, a in enumerate(d["articles"], start=1):
            if not a.get("url"):
                continue
            chinese = len(re.findall(r"[一-鿿]", a.get("title") or "")) >= 2
            out[f"{letter}{n}"] = {**a, "lang": "other" if letter == "A" else ("zh" if chinese else "id")}
    return out


def retrieve(clue, tools):
    return candidates(searches(clue, tools))


def choose(raw, cands):
    """The url code picks from the model's answer, or ""."""
    if any(isinstance(c, dict) and c.get("same") is False for c in raw.get("checks") or []):
        return ""  # a detail differs: a fake, or another event of the same kind
    matches = [cands[i] for i in dict.fromkeys(str(m).strip().upper() for m in raw.get("matches") or []) if i in cands]
    if not matches:
        return ""
    want = {"chinese": "zh", "indonesia": "id"}.get(raw.get("place"), "other")
    preferred = [a for a in matches if a["lang"] == want] or matches  # no article in that language: any that reports it
    return min(preferred, key=lambda a: a.get("published") or "9999")["url"]  # the earliest report


def answer(clue, tools):
    cands = retrieve(clue, tools)
    shown = [{"id": i, "title": a["title"], "published": a["published"], "text": a["text"][:700]} for i, a in cands.items()]
    prompt = f"now: {pc.now()}\nClue: {clue}\n\nSearch results:\n{json.dumps(shown, ensure_ascii=False)}\n"
    raw = tools.ask(PROMPT, prompt, schema=SCHEMA, model="sonnet", effort="low")
    if not raw:
        return {"url": "", "summary": ""}
    return {"url": choose(raw, cands), "summary": str(raw.get("story") or "")}
