"""高亮叠层可视化验证：把 Foxit 提到前台，高亮当前页第一句，截图留证。"""
import sys
import time
import ctypes

sys.path.insert(0, r"D:\Labs\2605-agent\PDF-TTS\src")

from PySide6.QtWidgets import QApplication
from PIL import ImageGrab

from lingread.bridge.foxit import FoxitBridge
from lingread.text.engine import TextEngine
from lingread.ui.overlay import HighlightOverlay

app = QApplication([])
bridge = FoxitBridge()
win = bridge.find_active_window()
doc = bridge.resolve_document_path(win)
page = bridge.get_current_page(win)
print(f"窗口: {win.title[:40]!r} 页: {page + 1}")

# 提到前台（keybd_event 绕过 Windows 前台锁）
ctypes.windll.user32.keybd_event(0, 0, 0, 0)
ctypes.windll.user32.ShowWindow(win.hwnd, 9)
ctypes.windll.user32.SetForegroundWindow(win.hwnd)
time.sleep(1.2)

engine = TextEngine(str(doc))
sid = engine.first_sentence_on_page(page)
s = engine.sentences[sid]
rects, page_rect = engine.quads_for_sentence(s)
zoom = bridge.get_zoom(win) or 1.0
vp = bridge.get_viewport_rect(win)
print(f"句子: {s.text[:50]!r} zoom={zoom} vp={vp} rects={len(rects)}")

from PySide6.QtGui import QGuiApplication

dpr = QGuiApplication.primaryScreen().devicePixelRatio()
scale = zoom * 96.0 / 72.0 * dpr
vp_l, vp_t, vp_r, _ = vp
x_off = vp_l + max(0, ((vp_r - vp_l) - page_rect.width * scale) / 2)
screen_rects = [
    (int(x_off + r.x0 * scale), int(vp_t + r.y0 * scale),
     int((r.x1 - r.x0) * scale), int((r.y1 - r.y0) * scale))
    for r in rects
]
print("屏幕矩形:", screen_rects)

overlay = HighlightOverlay()
overlay.show_rects(screen_rects)
app.processEvents()
time.sleep(0.8)
app.processEvents()

img = ImageGrab.grab(all_screens=True)
img.save(r"D:\Labs\2605-agent\PDF-TTS\demo\screenshot_highlight.png")
print("截图已保存", img.size)

time.sleep(3)
overlay.clear()
engine.close()
