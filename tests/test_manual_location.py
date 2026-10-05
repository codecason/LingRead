"""Playback must not take over Foxit's page or scroll position."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import pymupdf
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from lingread.ui import reader


class ManualLocationTests(unittest.TestCase):
    def test_playback_leaves_browsing_alone_until_locate_clicked(self):
        self.check_navigation(None)

    def test_visible_spoken_line_does_not_auto_scroll(self):
        self.check_navigation((0, 0, .5, 100))

    def check_navigation(self, registration):
        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        navigations, scrolls, bars, controllers, results = [], [], [], [], []
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'pages.pdf')
            with pymupdf.open() as doc:
                for _ in range(3):
                    doc.new_page().insert_text((100, 780), 'Read this sentence.')
                doc.save(path)
            win = SimpleNamespace(hwnd=123, title='pages.pdf')
            native = SimpleNamespace(GetForegroundWindow=lambda:123,
                GetAsyncKeyState=lambda key:0, IsIconic=lambda hwnd:False,
                SetForegroundWindow=lambda hwnd:None)
            bridge = SimpleNamespace(find_windows=lambda:[win], find_active_window=lambda:win,
                resolve_document_path=lambda w:path, selection_text=lambda w:'Read this sentence.',
                get_current_page=lambda w:2, get_viewport_rect=lambda w:(0, 0, 800, 800),
                get_zoom=lambda w:1, dpi=lambda w:72,
                follow_page=lambda w,p:navigations.append(p),
                scroll_document=lambda w,d:scrolls.append(d) or True)
            class Controller:
                def __init__(self, engine, tts, **callbacks):
                    self.engine, self.callbacks = engine, callbacks
                    self._session = object()
                    self.state = reader.State.READING
                    controllers.append(self)
                def set_speed(self, rate): pass
                def play_from_position(self, *args): pass
                def stop(self): self.state = reader.State.IDLE
            original = reader.FloatingBar
            def bar_factory(*args):
                bar = original(*args)
                bars.append(bar)
                return bar
            def emit_page():
                ctrl = controllers[0]
                sentence = ctrl.engine.sentences[0]
                ctrl.callbacks['on_sentence'](sentence)
                ctrl.callbacks['on_progress'](sentence, 0, 4)
            def manual():
                results.append((list(navigations), list(scrolls)))
                bars[0].btn_locate.click()
            with patch.object(reader, 'FoxitBridge', return_value=bridge), \
                 patch.object(reader.ctypes.windll, 'user32', native), \
                 patch.object(reader, 'get_mouse_point', return_value=(0,0)), \
                 patch.object(reader, 'FloatingBar', side_effect=bar_factory), \
                 patch.object(reader, 'PlaybackController', Controller), \
                 patch.object(reader.HighlightOverlay, 'show_physical_rects'), \
                 patch.object(reader, 'capture_viewport', return_value=None), \
                 patch.object(reader, 'register_page', return_value=registration), \
                 patch.object(reader, 'make_tray', return_value=SimpleNamespace(hide=lambda:None)):
                QTimer.singleShot(20, lambda:bars[0].btn_read.click())
                QTimer.singleShot(100, emit_page)
                QTimer.singleShot(1800, manual)
                QTimer.singleShot(2200, app.quit)
                reader.run_gui(None, 0, 'test')
                app.aboutToQuit.disconnect()
                bars[0].close()
            self.assertEqual(results, [([], [])], 'Audio must not navigate during browsing')
            self.assertEqual(navigations[0], 0, 'Manual locate must return to spoken page')


if __name__ == '__main__':
    unittest.main()
