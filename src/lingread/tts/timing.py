"""Speech timestamps in seconds and spans in Python character offsets."""
from dataclasses import dataclass
import re
from ..text.engine import normalized


@dataclass(frozen=True)
class WordBoundary:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Speech:
    audio: bytes
    boundaries: tuple[WordBoundary, ...] = ()


@dataclass(frozen=True)
class Cue:
    at: float
    until: float
    start: int
    end: int


def build_cues(text: str, boundaries, duration: float) -> list[Cue]:
    # Two CJK characters; Latin words stay intact. Punctuation never consumes
    # a highlight slot, and a group does not bridge punctuation.
    groups = list(re.finditer(r'[\u3400-\u9fff\U00020000-\U0002ffff]{1,2}|[^\W_]+(?:[’\x27-][^\W_]+)*', text))
    if not groups:
        return []
    norm, indices = normalized(text)
    times = {}
    cursor = 0
    for b in boundaries:
        token, _ = normalized(b.text)
        at = norm.find(token, cursor) if token else -1
        if at < 0:
            continue
        length = len(token)
        for j in range(length):
            source = indices[at + j]
            start = b.start + (b.end - b.start) * j / length
            end = b.start + (b.end - b.start) * (j + 1) / length
            if source in times:
                times[source] = (min(times[source][0], start), max(times[source][1], end))
            else:
                times[source] = (start, end)
        cursor = at + length
    # Interpolate unaligned characters only between surrounding anchors.
    spoken = [i for g in groups for i in range(g.start(), g.end())]
    for k, i in enumerate(spoken):
        if i in times:
            continue
        left = next((j for j in range(k - 1, -1, -1) if spoken[j] in times), -1)
        right = next((j for j in range(k + 1, len(spoken)) if spoken[j] in times), len(spoken))
        begin = times[spoken[left]][1] if left >= 0 else 0.0
        finish = times[spoken[right]][0] if right < len(spoken) else duration
        step = max(0, finish - begin) / (right - left - 1)
        times[i] = (begin + step * (k - left - 1), begin + step * (k - left))
    return [Cue(times[g.start()][0], times[g.end() - 1][1], g.start(), g.end()) for g in groups]
