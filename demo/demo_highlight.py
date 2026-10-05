"""M2 真机验证：翻页跟随 + 高亮叠层 + 变速合成，截图留证。"""
import sys
import time

sys.path.insert(0, r"D:\Labs\2605-agent\PDF-TTS\src")

from PySide6.QtWidgets import QApplication

from lingread.bridge.foxit import FoxitBridge
from lingread.text.engine import TextEngine
from lingread.ui.overlay import HighlightOverlay

app = QApplication([])
bridge = FoxitBridge()
win = bridge.find_active_window()
assert win, "未检测到 Foxit 窗口"
doc = bridge.resolve_document_path(win)
page0 = bridge.get_current_page(win)
print(f"文档: {doc}\n当前页: 第 {page0 + 1} 页")

engine = TextEngine(str(doc))

# 1. 翻页跟随：跳到当前页 +3
target = min(page0 + 3, engine.page_count - 1)
ok = bridge.follow_page(win, target)
time.sleep(1.5)  # 等 Foxit 完成跳页渲染
page_now = bridge.get_current_page(win)
print(f"follow_page -> {ok}, 目标第{target + 1}页, 实测第{None if page_now is None else page_now + 1}页")

# 2. 高亮：取该页第一句，计算屏幕矩形并显示叠层
zoom = bridge.get_zoom(win)
vp = bridge.get_viewport_rect(win)
print(f"zoom={zoom}, viewport={vp}")

sid = engine.first_sentence_on_page(target)
s = engine.sentences[sid]
rects, page_rect = engine.quads_for_sentence(s)
print(f"句子: {s.text[:50]!r} 命中矩形数={len(rects)}")

from PySide6.QtGui import QGuiApplication

dpr = QGuiApplication.primaryScreen().devicePixelRatio()
print(f"dpr={dpr}")
scale = (zoom or 1.0) * 96.0 / 72.0 * dpr
screen_rects = []
if rects and vp:
    vp_l, vp_t, vp_r, _ = vp
    x_off = vp_l + max(0, ((vp_r - vp_l) - page_rect.width * scale) / 2)
    for r in rects:
        screen_rects.append((
            int(x_off + r.x0 * scale), int(vp_t + r.y0 * scale),
            int((r.x1 - r.x0) * scale), int((r.y1 - r.y0) * scale),
        ))
print("屏幕矩形:", screen_rects)

overlay = HighlightOverlay()
overlay.show_rects(screen_rects)
app.processEvents()
time.sleep(1.0)
app.processEvents()

from PIL import ImageGrab

img = ImageGrab.grab(all_screens=True)
img.save(r"D:\Labs\2605-agent\PDF-TTS\demo\screenshot_highlight.png")
print("截图已保存")

# 3. 变速合成验证
import asyncio

from lingread.tts.edge import EdgeTTSEngine

async def speed_test():
    tts = EdgeTTSEngine()
    t0 = time.perf_counter(); a = await tts.synthesize("变速功能验证，正常速度。"); t1 = time.perf_counter() - t0
    tts.set = None
    tts.rate = "+50%"
    t0 = time.perf_counter(); b = await tts.synthesize("变速功能验证，正常速度。"); t2 = time.perf_counter() - t0
    print(f"1.0x: {len(a)}B ({t1:.1f}s) | 1.5x: {len(b)}B ({t2:.1f}s) -> "
          f"{'PASS' if len(b) < len(a) else 'CHECK(两者大小应不同)'}")

asyncio.run(speed_test())
time.sleep(4)  # 高亮留屏
overlay.clear()
engine.close()
print("done")
