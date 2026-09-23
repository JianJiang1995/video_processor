"""Select chronological evidence excerpts when a model's context is full."""

import re


EXCERPT_NOTICE = (
    "[Context excerpts: some records were omitted to fit the model. "
    "These excerpts cannot establish whole-video counts, a definitive first occurrence, "
    "or the absence of an event. "
    "Disclose insufficient evidence when relevant.]\n"
)


def select_context_excerpts(context: str, query: str, max_chars: int) -> str:
    if len(context) <= max_chars:
        return context
    budget = max_chars - len(EXCERPT_NOTICE)
    if budget <= 0:
        return ""

    # Keep complete records, including their timestamps, rather than cutting sentences.
    records = [part.strip() for part in re.split(r"\n(?=#{1,6}\s)", context) if part.strip()]
    words = re.findall(r"[a-z0-9_-]+|[\u4e00-\u9fff]+", query.lower())
    terms = set()
    for word in words:
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            terms.update(word[i:i + 2] for i in range(len(word) - 1))
        elif len(word) > 1:
            terms.add(word)

    # Questions and stored records may use different languages. These are search
    # synonyms, not detections or rules that manufacture clinical observations.
    for aliases in (
        ("instrument", "instruments", "tool", "tools", "器械", "工具"),
        ("bleeding", "hemorrhage", "出血"),
        ("fog", "fogging", "起雾", "雾"),
        ("scissors", "剪刀"),
        ("clip", "clips", "clipper", "夹子", "施夹", "夹闭"),
    ):
        if terms.intersection(aliases):
            terms.update(aliases)
    earliest = bool(re.search(r"\b(first|firstly|earliest)\b|首次|最早|第一次", query, re.I))

    def rank(index: int):
        text = records[index].lower()
        priority = 0
        if text.startswith("### selected review focus"):
            priority = 2
        elif text.startswith("### session evidence coverage"):
            priority = 1
        elif text.startswith(("### recent dialogue", "### recent user questions")):
            priority = -1
        return (priority, sum(term in text for term in terms), -index if earliest else index)

    chosen = []
    for index in sorted(range(len(records)), key=rank, reverse=True):
        size = len(records[index]) + 2
        if size <= budget:
            chosen.append(index)
            budget -= size
    if not chosen:
        return EXCERPT_NOTICE.rstrip()
    return EXCERPT_NOTICE + "\n\n".join(records[index] for index in sorted(chosen))
