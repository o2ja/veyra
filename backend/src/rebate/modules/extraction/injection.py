"""Injection indicators (AI_PROCESSING.md §3.5, §9): flags for the reviewer, computed from the source.

Indicators never change values; they are shown to the reviewer, and evidence that lies inside a flagged span
(hidden text or an instruction addressed to an AI) is rejected by the verifier.
"""

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from rebate.modules.extraction.documents import Colour, Document, Page, PdfText, Word
from rebate.modules.extraction.parsers import normalise

TINY_FONT_PT = 5.0  # body text in the golden set is 7-18pt; < 5pt is not meant to be read
WHITE_MIN = 0.95  # RGB/gray component threshold for "white on (assumed) white page"


class IndicatorKind(StrEnum):
    hidden_text = "hidden_text"  # white fill colour
    tiny_font = "tiny_font"
    off_page = "off_page"
    invisible_characters = "invisible_characters"  # zero-width / format characters
    instruction = "instruction"  # text addressed to an AI / instructions


@dataclass(frozen=True)
class Indicator:
    kind: IndicatorKind
    excerpt: str
    page: int | None = None
    sheet: str | None = None
    cell: str | None = None
    start: int = 0  # offsets into the page text / cell value
    end: int = 0


_INSTRUCTION = re.compile(
    r"\b(?:ignore|disregard|forget|override)\b[^.]{0,40}\b(?:instructions?|prompts?|rules|above|previous)\b"
    r"|\byou are (?:now )?(?:an?|in|the)\b[^.]{0,30}\b(?:mode|assistant|ai|model|admin)\b"
    r"|\b(?:system|developer) (?:prompt|message|instructions?)\b"
    r"|\bas an (?:ai|llm|language model)\b"
    r"|\b(?:mark|set|treat) (?:this|the) (?:agreement|document|claim|draft) as (?:approved|valid|verified)\b"
    r"|\bset (?:every|all|each) [a-z ]{0,30}\bto\b"
    r"|\bnew instructions\b|\bdo not (?:tell|mention|reveal)\b|\badmin mode\b"
)


def _is_white(colour: Colour) -> bool:
    if colour is None:
        return False
    if len(colour) == 4:  # CMYK: white is no ink
        return all(c <= 1 - WHITE_MIN for c in colour)
    return all(c >= WHITE_MIN for c in colour)


def word_flags(page: Page, word: Word) -> set[IndicatorKind]:
    flags: set[IndicatorKind] = set()
    if _is_white(word.colour):
        flags.add(IndicatorKind.hidden_text)
    if 0 < word.size < TINY_FONT_PT:
        flags.add(IndicatorKind.tiny_font)
    if word.x1 <= 0 or word.bottom <= 0 or word.x0 >= page.width or word.top >= page.height:
        flags.add(IndicatorKind.off_page)
    return flags


def _text_indicators(text: str) -> list[tuple[IndicatorKind, int, int]]:
    found = [
        (IndicatorKind.invisible_characters, i, i + 1)
        for i, ch in enumerate(text)
        if unicodedata.category(ch) == "Cf"
    ]
    low, index = normalise(text)
    for m in _INSTRUCTION.finditer(low):
        # widen to the sentence (bounded by ". " or a line break) so the reviewer sees the instruction
        s, e = index[m.start()], index[m.end() - 1] + 1
        dot = text.rfind(". ", 0, s)
        s = max(dot + 2 if dot >= 0 else 0, text.rfind("\n", 0, s) + 1)
        dot, nl = text.find(". ", e), text.find("\n", e)
        e = min(x for x in (dot + 1 if dot >= 0 else len(text), nl if nl >= 0 else len(text)))
        found.append((IndicatorKind.instruction, s, e))
    return found


def detect(document: Document) -> list[Indicator]:
    out: list[Indicator] = []
    if isinstance(document, PdfText):
        for page in document.pages:
            runs: dict[IndicatorKind, list[tuple[int, int]]] = {}
            for w in page.words:
                for kind in word_flags(page, w):
                    spans = runs.setdefault(kind, [])
                    if spans and w.start - spans[-1][1] <= 1:  # adjacent words form one run
                        spans[-1] = (spans[-1][0], w.end)
                    else:
                        spans.append((w.start, w.end))
            for kind, spans in runs.items():
                out += [Indicator(kind, page.text[s:e], page=page.number, start=s, end=e) for s, e in spans]
            out += [
                Indicator(kind, page.text[s:e], page=page.number, start=s, end=e)
                for kind, s, e in _text_indicators(page.text)
            ]
    else:
        for c in document.cells:
            out += [
                Indicator(kind, c.value[s:e], sheet=c.sheet, cell=c.address, start=s, end=e)
                for kind, s, e in _text_indicators(c.value)
            ]
    return out
