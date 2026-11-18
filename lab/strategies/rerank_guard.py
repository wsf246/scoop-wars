"""rerank_guard: baseline + the url guard only (the link must be one of the results). No new Claude calls."""
from strategies import rerank_lib as lib

retrieve = lib.search


def answer(clue: str, tools) -> dict:
    return lib.answer_with(clue, tools)
