"""speed_short: the baseline with a shorter required summary (15 words; 8 words when blank). Same searches, same candidates."""

from strategies import baseline, speed_lib

retrieve = baseline.retrieve


def answer(clue: str, tools) -> dict:
    results = retrieve(clue, tools)
    return tools.ask(speed_lib.PROMPT_SHORT, speed_lib.build_prompt(clue, results), model="sonnet", effort="low") or {"url": "", "summary": ""}
