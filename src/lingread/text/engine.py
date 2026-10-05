"""TextEngine: PDF 文本提取、清洗、句子切分（M1）

输出统一的句子序列，每句携带 (page_index, char_start) 全局位点信息，
供 ReadQueue 从任意位点开始朗读。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
import unicodedata

import pymupdf

# 句末标点（中英文）
_SENT_END = "。！？!?；;…"
_SENT_RE = re.compile(r"(?<=[%s])" % re.escape(_SENT_END))
_MAX_SENT_LEN = 100  # 单句上限，超长句按逗号/空格再切
# A layout wrap inside Chinese text is not a word boundary. Keep explicit
# spaces and blank-line paragraph boundaries; English still needs a space.
_CJK_WRAP = re.compile(r'(?<=[\u3400-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f])[ \t]*\n[ \t]*(?=[\u3400-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f])')


@dataclass
class Sentence:
    id: int
    page: int          # 0-based 页码
    char_start: int    # 文档全局字符偏移（清洗后文本坐标）
    text: str


@dataclass(frozen=True)
class ReadingPosition:
    sentence_id: int
    offset: int = 0


def normalized(text: str):
    """Comparable text plus indices into the original (ligatures may expand)."""
    out, indices = [], []
    # Foxit may copy line-end hyphens that the text engine has repaired.
    removed = {i for m in re.finditer(r'(?<=\w)-\s*\n(?=\w)', text)
               for i in range(m.start(), m.end())}
    for i, ch in enumerate(text):
        if i in removed:
            continue
        for c in unicodedata.normalize('NFKC', ch).casefold():
            if not c.isspace() and c != '\u00ad':
                out.append(c)
                indices.append(i)
    return ''.join(out), indices


def _mapped_page(page):
    chars, boxes = [], []
    for block in page.get_text('rawdict')['blocks']:
        for line in block.get('lines', []):
            for span in line['spans']:
                for c in span['chars']:
                    for letter in c['c']:
                        chars.append(letter)
                        boxes.append(pymupdf.Rect(c['bbox']))
            chars.append('\n')
            boxes.append(None)
    raw = ''.join(chars)
    deleted = {i for m in re.finditer(r'(?<=\w)-\n(?=\w)', raw)
               for i in range(m.start(), m.end())}
    deleted.update(i for m in _CJK_WRAP.finditer(raw)
                   for i in range(m.start(), m.end()))
    out, mapped = [], []
    for i, ch in enumerate(raw):
        if i in deleted:
            continue
        if ch.isspace():
            if out and out[-1] != ' ':
                out.append(' ')
                mapped.append(None)
        else:
            out.append(ch)
            mapped.append(boxes[i])
    if out and out[-1] == ' ':
        out.pop()
        mapped.pop()
    return ''.join(out), mapped


def _clean_page_text(raw: str) -> str:
    """页内清洗：断词修复、换行合并。"""
    # 英文断词：examina-\ntion -> examination
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", raw)
    text = _CJK_WRAP.sub('', text)
    # Remaining line breaks need a separator (e.g. English words).
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    # 多余空白
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _split_sentences(text: str) -> list[str]:
    parts = _SENT_RE.split(text)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) <= _MAX_SENT_LEN:
            out.append(p)
        else:  # 超长句二级切分
            subs = re.split(r"(?<=[，,、：:])", p)
            buf = ""
            for s in subs:
                if len(buf) + len(s) > _MAX_SENT_LEN and buf:
                    out.append(buf)
                    buf = s
                else:
                    buf += s
            if buf:
                out.append(buf)
    return out


class TextEngine:
    """从 PDF 构建句子索引。"""

    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.doc = pymupdf.open(pdf_path)
        self.page_count = self.doc.page_count
        self.sentences: list[Sentence] = []
        self._boxes: dict[int, list] = {}
        self._build()

    def _build(self) -> None:
        sid = 0
        offset = 0
        for pno in range(self.page_count):
            page = self.doc[pno]
            clean, boxes = _mapped_page(page)
            if not clean:
                continue
            cursor = 0
            for s in _split_sentences(clean):
                start = clean.index(s, cursor)
                cursor = start + len(s)
                self.sentences.append(Sentence(id=sid, page=pno, char_start=offset, text=s))
                self._boxes[sid] = boxes[start:cursor]
                sid += 1
                offset += len(s)

    def first_sentence_on_page(self, page: int) -> int:
        """返回某页（0-based）第一句的 sentence id；无文本则顺延到下一页。"""
        for s in self.sentences:
            if s.page >= page:
                return s.id
        return -1

    def slice_from(self, sentence_id: int, offset: int = 0) -> list[Sentence]:
        if not 0 <= sentence_id < len(self.sentences):
            return []
        result = self.sentences[sentence_id:]
        first = result[0]
        if not 0 <= offset < len(first.text):
            return []
        result[0] = replace(first, text=first.text[offset:], char_start=first.char_start + offset)
        return result

    def locate_selection(self, text: str, page: int, point=None) -> ReadingPosition | None:
        needle, _ = normalized(text)
        if not needle:
            return None
        # Adjacent pages cover reverse and cross-page selections without matching
        # an unrelated occurrence elsewhere in a large book.
        candidates = [s for s in self.sentences if abs(s.page - page) <= 1]
        haystack, positions = '', []
        for s in candidates:
            norm, indices = normalized(s.text)
            haystack += norm
            positions.extend(ReadingPosition(s.id, i) for i in indices)
        matches = []
        at = haystack.find(needle)
        while at >= 0:
            pos = positions[at]
            s = self.sentences[pos.sentence_id]
            box = self._boxes[s.id][pos.offset]
            distance = abs(s.page - page) * 100000
            if point and box:
                distance += (box.x0 - point[0]) ** 2 + (box.y0 - point[1]) ** 2
            matches.append((distance, at, pos))
            at = haystack.find(needle, at + 1)
        return min(matches, key=lambda m: m[:2])[2] if matches else None

    def position_at_point(self, page: int, x: float, y: float) -> ReadingPosition | None:
        best = None
        for s in self.sentences:
            if s.page != page:
                continue
            for i, r in enumerate(self._boxes[s.id]):
                if r is None or s.text[i].isspace():
                    continue
                dx = max(r.x0 - x, 0, x - r.x1)
                dy = max(r.y0 - y, 0, y - r.y1)
                distance = dx * dx + dy * dy
                if distance <= max(3, r.height / 2) ** 2:
                    candidate = (distance, abs((r.x0 + r.x1) / 2 - x), s.id, i)
                    if best is None or candidate < best:
                        best = candidate
        return ReadingPosition(best[2], best[3]) if best else None

    def rects_for_range(self, s: Sentence, start: int, end: int):
        base = s.char_start - self.sentences[s.id].char_start
        boxes = self._boxes[s.id][base + start:base + end]
        result = []
        for box in boxes:
            if box is None:
                continue
            r = pymupdf.Rect(box)
            if (result and abs(result[-1].y0 - r.y0) < 2
                    and abs(result[-1].y1 - r.y1) < 2
                    and -1 <= r.x0 - result[-1].x1 <= r.height):
                result[-1] |= r
            else:
                result.append(r)
        return result, self.doc[s.page].rect

    def quads_for_sentence(self, s: Sentence):
        """返回 (页面坐标矩形列表, 页面rect)。用句首探针在页内搜索定位。

        句子经清洗（换行合并/断词修复），用前 20 字符探针搜索最稳；
        返回首个命中位置的所有 quad（跨行句会有多个）。
        """
        return self.rects_for_range(s, 0, len(s.text))

    def sentence_at_point(self, page: int, x: float, y: float) -> int:
        """页面坐标 (x,y) 处点击 -> 最近的句子 id。

        策略：取该页所有句子，用句首探针 search_for 拿起始 y 坐标，
        选 y 距离点击点最近（优先点击处上方/同行的句子）的那句。
        """
        cands = [s for s in self.sentences if s.page == page]
        if not cands:
            return self.first_sentence_on_page(page)
        best_id, best_dist = cands[0].id, float("inf")
        pg = self.doc[page]
        for s in cands:
            probe = s.text[:12].strip()
            if not probe:
                continue
            try:
                quads = pg.search_for(probe, quads=True)
            except Exception:
                continue
            if not quads:
                continue
            r = quads[0].rect
            cy = (r.y0 + r.y1) / 2
            # 点击在句子下方较多时（读过头）惩罚更大，偏向点击处未读的句子
            dist = abs(cy - y) + (30 if cy < y - 20 else 0)
            if dist < best_dist:
                best_dist, best_id = dist, s.id
        return best_id

    def detect_language(self) -> str:
        """按前 50 句统计中日韩字符占比，返回 'zh' 或 'en'。"""
        import re as _re

        sample = "".join(s.text for s in self.sentences[:50])
        if not sample:
            return "zh"
        cjk = len(_re.findall(r"[一-鿿]", sample))
        return "zh" if cjk / len(sample) > 0.2 else "en"

    def close(self) -> None:
        self.doc.close()
