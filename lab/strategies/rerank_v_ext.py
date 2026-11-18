"""rerank_v_ext: baseline, then Octen Extract of the picked article and a haiku check of the clue against it; blank on a conflict."""
from strategies import rerank_lib as lib

retrieve = lib.search


def answer(clue: str, tools) -> dict:
    return lib.answer_with(clue, tools, verify_source="extract")
