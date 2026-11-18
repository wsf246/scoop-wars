"""Lanes experiment: a haiku call guesses where the clue's story happens, and that picks the second search.

any-language search always; second search is Chinese-or-Indonesian unless haiku says the story is local to
another non-English country, in which case it is the wide non-English lane. Retrieval experiment only.
"""

from strategies import lanes_common as lc

ROUTER_SYSTEM = (
    "A clue describes one news story from today, written without names. Decide where the story takes place and which language the local press there writes in. "
    "Answer local_language with: 'zh' for mainland China, Hong Kong, Macau, Taiwan, Singapore or Malaysia; 'id' for Indonesia; the ISO 639-1 code "
    "(ja, ko, th, vi, hi, ar, es, pt, fr, de, it, tr, ru, nl, pl) for another country whose press is not in English; 'en' for an English-speaking country "
    "or a global story (markets, international sport, science, technology); 'unknown' when the clue gives no real hint of the country. Do not guess from weak hints."
)
ROUTER_SCHEMA = {
    "type": "object",
    "properties": {"place": {"type": "string"}, "local_language": {"type": "string", "enum": ["zh", "id", "ja", "ko", "th", "vi", "hi", "ar", "es", "pt", "fr", "de", "it", "tr", "ru", "nl", "pl", "en", "unknown"]}},
    "required": ["place", "local_language"], "additionalProperties": False,
}


def second_lane(clue: str, tools) -> tuple[str, dict]:
    guess = tools.ask(ROUTER_SYSTEM, f"Clue: {clue}", schema=ROUTER_SCHEMA, model="haiku", effort="low", timeout=20) or {}
    return ("zh+id" if guess.get("local_language", "unknown") in ("zh", "id", "en", "unknown") else "wide"), guess


def retrieve(clue: str, tools) -> dict:
    lane, _ = second_lane(clue, tools)
    done = tools.search_many([{"name": "any", "body": lc.body(clue, "any")}, {"name": lane, "body": lc.body(clue, lane)}])
    return {d["name"]: d["articles"] for d in done}
