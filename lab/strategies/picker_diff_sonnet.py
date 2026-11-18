"""One call, sonnet low: `story` and `differences` before the url; any difference blanks the card."""

from strategies import picker_common as pc


def retrieve(clue, tools):
    return pc.retrieve(clue, tools)


def answer(clue, tools):
    return pc.pick_with_differences(clue, tools, model="sonnet", effort="low")
