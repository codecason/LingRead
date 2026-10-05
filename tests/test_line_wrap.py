import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import pymupdf
from lingread.text.engine import TextEngine, _clean_page_text


class LineWrapTests(unittest.TestCase):
    def test_chinese_wrapped_word_is_continuous_and_keeps_coordinates(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'wrapped.pdf'
            with pymupdf.open() as doc:
                page = doc.new_page()
                page.insert_text((72, 72), '这个问题需要认真分\n析，然后继续阅读。',
                                 fontname='china-s', fontsize=16)
                doc.save(path)
            engine = TextEngine(str(path))
            try:
                self.assertEqual(engine.sentences[0].text,
                                 '这个问题需要认真分析，然后继续阅读。')
                pos = engine.locate_selection('分\n析，然后', 0)
                self.assertEqual(pos.offset, 8)
                self.assertTrue(engine.slice_from(0, pos.offset)[0].text.startswith('分析'))
                rects, _ = engine.rects_for_range(engine.sentences[0], 8, 10)
                self.assertEqual(len(rects), 2)
                self.assertGreater(rects[1].y0, rects[0].y0)
            finally:
                engine.close()

    def test_cleaner_preserves_english_spaces_and_real_paragraphs(self):
        self.assertEqual(_clean_page_text('认真分\n析问题。'), '认真分析问题。')
        self.assertEqual(_clean_page_text('认真分 \n 析问题。'), '认真分析问题。')
        self.assertEqual(_clean_page_text('English\nwords and examina-\ntion.'),
                         'English words and examination.')
        self.assertEqual(_clean_page_text('第一段\n\n第二段'), '第一段\n\n第二段')
        self.assertEqual(_clean_page_text('有意 空格'), '有意 空格')


if __name__ == '__main__':
    unittest.main()
