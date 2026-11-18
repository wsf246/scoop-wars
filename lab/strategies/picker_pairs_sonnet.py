"""One call, sonnet low: `story`, then each clue detail paired with what the articles state, then the url."""

from strategies import picker_common as pc


def retrieve(clue, tools):
    return pc.retrieve(clue, tools)


def answer(clue, tools):
    return pc.pick_with_pairs(clue, tools, model="sonnet", effort="low")
