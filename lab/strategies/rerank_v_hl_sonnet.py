"""rerank_v_hl_sonnet: baseline, then a sonnet (low effort) check of the clue against the picked article's search highlight; blank on a conflict."""
from strategies import rerank_lib as lib

retrieve = lib.search


def answer(clue: str, tools) -> dict:
    return lib.answer_with(clue, tools, verify_source="highlight", verify_model="sonnet")
