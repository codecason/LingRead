"""Real Qt button/polling pipeline; only Foxit/Win32 and cloud are simulated."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import pymupdf
from PySide6.QtCore import QTimer, QPoint
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication
from lingread.ui import reader


class GuiReadingStartTests(unittest.TestCase):
    def test_clicking_read_bar_preserves_document_start(self):
        self.check_read_start(False)

    def test_clipboard_focus_change_does_not_discard_captured_click(self):
        self.check_read_start(True)

    def check_read_start(self, clipboard_changes_focus):
        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'start.pdf')
            with pymupdf.open() as doc:
                p = doc.new_page()
                p.insert_text((100, 100), 'Before selected words.', fontsize=16)
                doc.save(path)
            engine = reader.TextEngine(path)
            box = engine._boxes[0][7]
            point = QPoint(round((box.x0+box.x1)/2), round((box.y0+box.y1)/2)+200)
            engine.close()
            native = SimpleNamespace(GetForegroundWindow=lambda:123,
                                     GetAsyncKeyState=lambda key:0)
            win = SimpleNamespace(hwnd=123, title='start.pdf', pid=1)
            bridge = SimpleNamespace(find_windows=lambda:[win], find_active_window=lambda:win,
                get_viewport_rect=lambda w:(0,200,800,800), resolve_document_path=lambda w:path,
                get_current_page=lambda w:0, selection_text=lambda w:'',
                get_zoom=lambda w:1, dpi=lambda w:72)
            if clipboard_changes_focus:
                def selection(w):
                    native.GetForegroundWindow = lambda:999
                    return ''
                bridge.selection_text = selection
            requested = []
            class TTS:
                def __init__(self, **kwargs): self.rate = '+0%'
                async def synthesize_timed(self, text):
                    requested.append(text)
                    await asyncio.sleep(30)
            bars=[]
            original_bar=reader.FloatingBar
            def make_bar(*args):
                b=original_bar(*args)
                bars.append(b)
                return b
            def release(): native.GetAsyncKeyState=lambda key:0
            def press_bar():
                nonlocal point
                point=bars[0].frameGeometry().center()
                native.GetAsyncKeyState=lambda key:0x8000
            def click_read():
                release()
                bars[0].btn_read.click()
            native.GetAsyncKeyState=lambda key:0x8000
            with patch.object(reader, 'FoxitBridge', return_value=bridge), \
                 patch.object(reader.ctypes.windll, 'user32', native), \
                 patch.object(reader, 'get_mouse_point', side_effect=lambda:(point.x(),point.y())), \
                 patch.object(QCursor, 'pos', side_effect=lambda:point), \
                 patch.object(reader, 'FloatingBar', side_effect=make_bar), \
                 patch.object(reader, 'EdgeTTSEngine', TTS), \
                 patch.object(reader, 'capture_viewport', return_value=None), \
                 patch.object(reader, 'register_page', return_value=(0,0,.5,100)), \
                 patch.object(reader, 'make_tray', return_value=SimpleNamespace(hide=lambda:None)):
                QTimer.singleShot(100, release)
                QTimer.singleShot(200, press_bar)
                QTimer.singleShot(300, click_read)
                QTimer.singleShot(900, app.quit)
                reader.run_gui(None, 0, 'test')
                app.aboutToQuit.disconnect()
                status=bars[0].status.text()
                bars[0].close()
                self.assertEqual(requested[:1], ['selected words.'], status)

if __name__=='__main__':
    unittest.main()
