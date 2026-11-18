"""combined_v1: what is on main now. Named languages on the first search + the detail-pairs picker, blank enforced in code.

Knobs for the codex_agent review: CLEAN strips Markdown image/link urls from snippets; FULL adds each article's
full text (up to 2,400 characters) as extra evidence, as the codex_agent branch does.
"""

import re

from strategies import params_best, picker_common as pc

CLEAN = False
FULL = False


def clean_article_text(text) -> str:
    """codex_agent's cleaner: keep captions and link labels, drop Markdown urls."""
    if not isinstance(text, str):
        return ""
    text = re.sub(r"!?\[([^\]\r\n]*)\]\((?:[^()\r\n]|\([^()\r\n]*\))*\)", r"\1", text)
    return " ".join(text.split())


def retrieve(clue, tools, clean=None, full=None):
    clean = CLEAN if clean is None else clean
    full = FULL if full is None else full
    bodies = params_best.bodies(clue)
    bodies[1]["body"]["subjects"] = {"enable": False}
    if full:
        for b in bodies:
            b["body"]["full_content"] = {"enable": True, "max_tokens": 1000}
    done = tools.search_many(bodies)
    out = {}
    for d in done:
        arts = []
        for a, raw in zip(d["articles"], d["data"].get("results") or []):
            highlight = raw.get("highlight") or ""
            art = {"title": a["title"], "url": a["url"], "published": a["published"], "text": (clean_article_text(highlight) if clean else highlight)[:900]}
            if full:
                body_text = clean_article_text(raw.get("full_content"))[:2400]
                if body_text and body_text != art["text"]:
                    art["article_text"] = body_text
            arts.append(art)
        out[d["name"]] = arts
    return out


def answer(clue, tools, clean=None, full=None):
    results = retrieve(clue, tools, clean, full)
    raw = tools.ask(pc.PAIRS_PROMPT, pc.user_prompt(clue, results), schema=pc.PAIRS_SCHEMA, model="sonnet", effort="low")
    if not raw:
        return {"url": "", "summary": ""}
    urls = {a["url"] for arts in results.values() for a in arts}
    url = str(raw.get("url") or "").strip()
    if any(isinstance(c, dict) and c.get("same") is False for c in raw.get("checks") or []) or url not in urls:
        url = ""  # a detail differs (a fake), or the link is not one of the results
    return {"url": url, "summary": str(raw.get("story") or "")}
