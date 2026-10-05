"""Offline regressions: generated PDFs, real text index, no cloud/account needed."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest
import threading
import time
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
os.environ.setdefault('LINGREAD_DATA_DIR', str(Path(tempfile.gettempdir()) / 'lingread-tests'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import pymupdf
from lingread.text.engine import TextEngine


class TextPositionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'sample.pdf'
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), '前面文字。这里开始朗读，继续后面的内容。', fontname='china-s', fontsize=16)
        page.insert_text((72, 120), '重复内容。', fontname='china-s', fontsize=16)
        page.insert_text((72, 160), '重复内容。', fontname='china-s', fontsize=16)
        page = doc.new_page()
        page.insert_text((72, 72), 'Second page: examina-\ntion works. Continue here.', fontsize=14)
        doc.save(self.path)
        doc.close()
        self.engine = TextEngine(str(self.path))

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def test_selected_mid_sentence_starts_at_exact_character(self):
        self.assertTrue(hasattr(self.engine, 'locate_selection'), 'Selection mapping is missing')
        pos = self.engine.locate_selection('开始朗读，继续', 0)
        self.assertIsNotNone(pos)
        first = self.engine.slice_from(pos.sentence_id, pos.offset)[0]
        self.assertEqual(first.text, '开始朗读，继续后面的内容。')
        self.assertEqual(first.char_start, self.engine.sentences[pos.sentence_id].char_start + 2)

    def test_selection_whitespace_and_pdf_line_hyphen(self):
        self.assertTrue(hasattr(self.engine, 'locate_selection'))
        pos = self.engine.locate_selection('examination\r\n works.', 1)
        self.assertEqual(self.engine.slice_from(pos.sentence_id, pos.offset)[0].text,
                         'examination works. Continue here.')

    def test_duplicate_text_uses_anchor_not_first_occurrence(self):
        self.assertTrue(hasattr(self.engine, 'locate_selection'))
        pos = self.engine.locate_selection('重复内容', 0, point=(73, 155))
        self.assertEqual(pos.sentence_id, 3)

    def test_range_rects_preserve_occurrence_and_partial_start(self):
        self.assertTrue(hasattr(self.engine, 'rects_for_range'))
        s = self.engine.slice_from(3)[0]
        rects, _ = self.engine.rects_for_range(s, 0, 2)
        self.assertEqual(len(rects), 1)
        self.assertGreater(rects[0].y0, 140)
        self.assertAlmostEqual(rects[0].width, 32, delta=1)

    def test_point_uses_x_and_does_not_pick_header_for_same_line(self):
        self.assertTrue(hasattr(self.engine, 'position_at_point'))
        pos = self.engine.position_at_point(0, 187, 65)
        self.assertEqual((pos.sentence_id, pos.offset), (1, 2))
        self.assertIsNone(self.engine.position_at_point(0, 560, 700))

    def test_invalid_selection_never_matches_page_start(self):
        self.assertTrue(hasattr(self.engine, 'locate_selection'))
        self.assertIsNone(self.engine.locate_selection('not in this PDF', 0))
        self.assertIsNone(self.engine.locate_selection('   ', 0))


class TimingTests(unittest.TestCase):
    def timing(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('lingread.tts.timing'), 'Word timing is missing')
        from lingread.tts.timing import build_cues, WordBoundary
        return build_cues, WordBoundary

    def test_two_characters_cross_word_boundaries_and_keep_pause(self):
        build, B = self.timing()
        cues = build('我喜欢学习。', [B(.1, .2, '我'), B(.3, .7, '喜欢'), B(1.0, 1.4, '学习')], 1.6)
        self.assertEqual([(c.start, c.end) for c in cues], [(0, 2), (2, 4), (4, 5)])
        self.assertAlmostEqual(cues[0].at, .1)
        self.assertAlmostEqual(cues[1].at, .5)
        self.assertAlmostEqual(cues[2].at, 1.2)

    def test_punctuation_and_english_words_are_not_mangled(self):
        build, B = self.timing()
        text = '你好，世界！Hello world.'
        cues = build(text, [], 4)
        self.assertEqual([text[c.start:c.end] for c in cues], ['你好', '世界', 'Hello', 'world'])
        self.assertTrue(all(a.at < b.at for a, b in zip(cues, cues[1:])))


class PipelineTests(unittest.TestCase):
    def test_cloud_boundaries_survive_cache_and_new_engine(self):
        from lingread.tts import edge
        self.assertTrue(hasattr(edge.EdgeTTSEngine, 'synthesize_timed'), 'Audio cache drops word boundaries')
        class Stream:
            def __init__(self, *args, **kwargs):
                if kwargs.get('boundary') != 'WordBoundary':
                    raise AssertionError('Word boundary was not requested')
            async def stream(self):
                yield {'type': 'audio', 'data': b'mp3-fixture'}
                yield {'type': 'WordBoundary', 'offset': 1000000, 'duration': 2000000, 'text': '你好'}
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(edge, 'CACHE_DIR', Path(folder)), patch.object(edge, '_DB', Path(folder) / 'meta.db'):
                with patch.object(edge.edge_tts, 'Communicate', Stream):
                    cold = asyncio.run(edge.EdgeTTSEngine().synthesize_timed('你好'))
                with patch.object(edge.edge_tts, 'Communicate', side_effect=AssertionError('cache missed')):
                    hot = asyncio.run(edge.EdgeTTSEngine().synthesize_timed('你好'))
                self.assertEqual(cold, hot)
                self.assertEqual(hot.audio, b'mp3-fixture')
                self.assertAlmostEqual(hot.boundaries[0].start, .1)
                self.assertAlmostEqual(hot.boundaries[0].end, .3)

    def test_replay_keeps_mid_sentence_start_and_stop_cancels_synthesis(self):
        from lingread.player.queue import PlaybackController, State
        from lingread.text.engine import Sentence
        self.assertTrue(hasattr(PlaybackController, 'play_from_position'), 'Playback drops character offset')
        received, entered = [], threading.Event()
        class Engine:
            sentences = [Sentence(0, 0, 0, '前面开始朗读。')]
            slice_from = TextEngine.slice_from
        class TTS:
            async def synthesize_timed(self, text):
                received.append(text)
                entered.set()
                await asyncio.sleep(30)
        ctrl = PlaybackController(Engine(), TTS(), headless=True)
        try:
            ctrl.play_from_position(0, 2)
            self.assertTrue(entered.wait(2))
            t0 = time.perf_counter()
            ctrl.stop()
            self.assertLess(time.perf_counter() - t0, .2)
            self.assertEqual(ctrl.state, State.IDLE)
            entered.clear()
            ctrl.replay()
            self.assertTrue(entered.wait(2))
            self.assertEqual(received[:2], ['开始朗读。', '开始朗读。'])
        finally:
            ctrl.stop()

    def test_audio_clock_freezes_on_pause_and_no_progress_after_stop(self):
        import array
        from lingread.player import queue as player
        from lingread.tts.timing import build_cues
        from lingread.text.engine import Sentence
        recorded = []
        closed = threading.Event()
        class Device:
            def __init__(self, **kwargs):
                self.thread = None
            def start(self, stream):
                def run():
                    while not closed.wait(.02):
                        try:
                            stream.send(20)
                        except StopIteration:
                            return
                self.thread = threading.Thread(target=run)
                self.thread.start()
            def close(self):
                closed.set()
                self.thread.join(1)
        async def scenario():
            ctrl = player.PlaybackController(None, None, on_progress=lambda s,a,b: recorded.append(s.text[a:b]))
            session = player._Session()
            ctrl._session = session
            ctrl.state = player.State.READING
            s = Sentence(0, 0, 0, '一二三四五六七八九十')
            pcm = SimpleNamespace(samples=array.array('h', [0]*1200), nchannels=1, sample_rate=1000)
            task = asyncio.create_task(ctrl._play_pcm(pcm, session, s, build_cues(s.text, [], 1.2)))
            await asyncio.sleep(.15)
            ctrl.pause()
            snapshot = list(recorded)
            await asyncio.sleep(.3)
            self.assertEqual(recorded, snapshot)
            ctrl.resume()
            await asyncio.sleep(.3)
            self.assertGreater(len(recorded), len(snapshot))
            ctrl.stop()
            stopped = list(recorded)
            await task
            self.assertEqual(recorded, stopped)
            self.assertTrue(closed.is_set())
            self.assertEqual(recorded[:2], ['一二', '三四'])
        with patch.object(player.miniaudio, 'PlaybackDevice', Device):
            asyncio.run(scenario())


class SelectionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_copy_restores_text_and_binary_formats(self):
        from PySide6.QtCore import QMimeData
        from lingread.bridge.foxit import FoxitBridge
        import ctypes
        old = QMimeData()
        old.setText('original clipboard')
        old.setData('application/test-binary', b'\x00\xff\x01')
        self.app.clipboard().setMimeData(old)
        class Native:
            sequence = 40
            GetForegroundWindow = SimpleNamespace(restype=None)
            def __init__(self):
                self.GetForegroundWindow = lambda: 123
            def GetClipboardSequenceNumber(self): return self.sequence
            def GetAsyncKeyState(self, key): return 0
            def SendInput(self, count, events, size):
                self.sequence += 1
                SelectionIntegrationTests.app.clipboard().setText('开始朗读')
                return count
        with patch.object(ctypes.windll, 'user32', Native()):
            text = FoxitBridge._copy_selection(SimpleNamespace(hwnd=123))
        self.assertEqual(text, '开始朗读')
        mime = self.app.clipboard().mimeData()
        self.assertEqual(mime.text(), 'original clipboard')
        self.assertEqual(bytes(mime.data('application/test-binary')), b'\x00\xff\x01')

    def test_unchanged_clipboard_is_not_a_selection(self):
        from lingread.bridge.foxit import FoxitBridge
        import ctypes
        self.app.clipboard().setText('stale clipboard from another document')
        native = SimpleNamespace(GetForegroundWindow=lambda: 123,
            GetClipboardSequenceNumber=lambda: 40, GetAsyncKeyState=lambda key: 0,
            SendInput=lambda *args: 4)
        with patch.object(ctypes.windll, 'user32', native):
            self.assertEqual(FoxitBridge._copy_selection(SimpleNamespace(hwnd=123)), '')
        self.assertEqual(self.app.clipboard().text(), 'stale clipboard from another document')

    def test_no_selection_and_failed_matching_do_not_fall_back_to_page_start(self):
        from lingread.ui.reader import resolve_start
        engine = SimpleNamespace(locate_selection=lambda *args: None)
        with self.assertRaises(ValueError):
            resolve_start(engine, 'not found', 0, None)
        with self.assertRaises(ValueError):
            resolve_start(engine, '', 0, None)

    def test_follow_direction_keeps_current_audio_text_in_view(self):
        from lingread.ui import reader
        self.assertTrue(hasattr(reader, 'follow_direction'))
        viewport = (100, 100, 800, 700)
        self.assertEqual(reader.follow_direction((200, 720, 240, 740), viewport), 1)
        self.assertEqual(reader.follow_direction((200, 50, 240, 80), viewport), -1)
        self.assertEqual(reader.follow_direction((200, 320, 240, 340), viewport), 0)
        # Start moving before the baseline disappears below the viewport.
        self.assertEqual(reader.follow_direction((200, 680, 240, 698), viewport), 1)


class GeometryTests(unittest.TestCase):
    def test_repeated_glyphs_on_scrolled_foxit_page_still_register(self):
        import cv2
        from lingread.bridge.geometry import register_page
        fixtures = Path(__file__).parent / 'fixtures'
        page = cv2.imread(str(fixtures / 'registration-page.png'), 0)
        screen = cv2.imread(str(fixtures / 'registration-screen.png'), 0)
        result = register_page(page, screen, 1.5)
        self.assertIsNotNone(result, 'Visible text must not lose highlighting because offscreen glyphs repeat')
        self.assertAlmostEqual(result[0], 161, delta=3)
        self.assertAlmostEqual(result[1], -352, delta=3)
        self.assertAlmostEqual(result[2], 1.5, delta=.02)

    def test_scrolled_page_is_not_anchored_to_viewport_top(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('lingread.bridge.geometry'), 'Page registration is missing')
        import numpy as np
        import cv2
        from lingread.bridge.geometry import register_page
        rng = np.random.default_rng(2026)
        page = np.full((900, 600), 255, np.uint8)
        for n in range(80):
            x, y = rng.integers(20, 520), rng.integers(20, 860)
            cv2.putText(page, f'{n}:text', (int(x), int(y)), cv2.FONT_HERSHEY_SIMPLEX, .5, 0, 1)
        matrix = np.float32([[1.4, 0, 80], [0, 1.4, -330]])
        screen = cv2.warpAffine(page, matrix, (1000, 650), borderValue=180)
        result = register_page(page, screen, expected_scale=1.4)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result[0], 80, delta=3)
        self.assertAlmostEqual(result[1], -330, delta=3)
        self.assertAlmostEqual(result[2], 1.4, delta=.02)
        self.assertIsNone(register_page(page, np.full_like(screen, 255), expected_scale=1.4))


if __name__ == '__main__':
    unittest.main()
