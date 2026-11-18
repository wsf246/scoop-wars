"""rerank_v_sum_sonnet: as rerank_v_sum with sonnet (low effort) as the checker."""
from strategies import rerank_lib as lib

retrieve = lib.search


def answer(clue: str, tools) -> dict:
    return lib.answer_with(clue, tools, verify_source="summary", verify_model="sonnet")
