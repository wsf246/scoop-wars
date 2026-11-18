"""combined_v1: named languages on the first search (params) + the detail-pairs picker with the blank enforced in code (picker)."""

from strategies import params_best, picker_common as pc


def retrieve(clue, tools):
    bodies = params_best.bodies(clue)
    bodies[1]["body"]["subjects"] = {"enable": False}
    done = tools.search_many(bodies)
    return {d["name"]: [{**a, "text": a["text"][:900]} for a in d["articles"]] for d in done}


def answer(clue, tools):
    results = retrieve(clue, tools)
    raw = tools.ask(pc.PAIRS_PROMPT, pc.user_prompt(clue, results), schema=pc.PAIRS_SCHEMA, model="sonnet", effort="low")
    if not raw:
        return {"url": "", "summary": ""}
    urls = {a["url"] for arts in results.values() for a in arts}
    url = str(raw.get("url") or "").strip()
    if any(isinstance(c, dict) and c.get("same") is False for c in raw.get("checks") or []) or url not in urls:
        url = ""  # a detail differs (a fake), or the link is not one of the results
    return {"url": url, "summary": str(raw.get("story") or "")}
