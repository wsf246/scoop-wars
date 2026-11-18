"""rerank_v_sum: baseline, then a haiku check of the clue against the picker's own summary only; blank on a conflict."""
from strategies import rerank_lib as lib

retrieve = lib.search


def answer(clue: str, tools) -> dict:
    return lib.answer_with(clue, tools, verify_source="summary")
