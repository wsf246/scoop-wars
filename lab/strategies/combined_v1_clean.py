"""combined_v1 with codex_agent's snippet cleaner only."""

from strategies import combined_v1


def retrieve(clue, tools):
    return combined_v1.retrieve(clue, tools, clean=True, full=False)


def answer(clue, tools):
    return combined_v1.answer(clue, tools, clean=True, full=False)
