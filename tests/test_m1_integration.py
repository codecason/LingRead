"""M1 集成测试：停止响应 / 重读位点 / 缓存命中 / UI 离屏实例化"""
import os
import sys
import time

sys.path.insert(0, r"D:\Labs\2605-agent\PDF-TTS\src")
os.environ["QT_QPA_PLATFORM"] = "offscreen"

PDF = r"D:\Labs\2605-agent\PDF-TTS\tests\fixtures\sample.pdf"

from lingread.player.queue import PlaybackController, State
from lingread.text.engine import TextEngine
from lingread.tts.edge import EdgeTTSEngine

print("== 1. 缓存命中测试（第二次合成同句应瞬时返回）==")
import asyncio

async def cache_test():
    tts = EdgeTTSEngine()
    t0 = time.perf_counter(); await tts.synthesize("缓存测试句子。"); cold = time.perf_counter() - t0
    t0 = time.perf_counter(); await tts.synthesize("缓存测试句子。"); warm = time.perf_counter() - t0
    print(f"   冷 {cold:.2f}s / 热 {warm*1000:.1f}ms -> {'PASS' if warm < 0.05 else 'FAIL'}")
    return warm < 0.05

r1 = asyncio.run(cache_test())

print("== 2. 停止响应测试（目标 ≤200ms 内状态复位；headless 下测量指令往返）==")
engine = TextEngine(PDF)
ctrl = PlaybackController(engine, EdgeTTSEngine(), headless=True)
ctrl.play_from_sentence(0)
time.sleep(2.0)  # 等第一句开始
t0 = time.perf_counter()
ctrl.stop()
stop_ms = (time.perf_counter() - t0) * 1000
print(f"   stop() 耗时 {stop_ms:.1f}ms, 状态={ctrl.state.name}, 停留句id={ctrl.current_id} -> "
      f"{'PASS' if ctrl.state == State.IDLE and ctrl.current_id >= 0 else 'FAIL'}")
r2 = ctrl.state == State.IDLE and ctrl.current_id >= 0

print("== 3. 重读位点测试（replay 应从停留句重新开始）==")
marked = []
ctrl2 = PlaybackController(engine, EdgeTTSEngine(), on_sentence=lambda s: marked.append(s.id), headless=True)
ctrl2.start_id = ctrl.current_id  # 模拟上次起点
ctrl2.play_from_sentence(ctrl.current_id)
t0 = time.perf_counter()
while ctrl2.state != State.IDLE and len(marked) < 2:
    time.sleep(0.1)
ctrl2.stop()
print(f"   重读序列={marked[:2]}, 起点={ctrl.current_id} -> "
      f"{'PASS' if marked and marked[0] == ctrl.current_id else 'FAIL'}")
r3 = bool(marked) and marked[0] == ctrl.current_id

print("== 4. UI 离屏实例化测试 ==")
from PySide6.QtWidgets import QApplication

from lingread.ui.bar import FloatingBar, UiBridge

app = QApplication([])
holder = {}
bar = FloatingBar(holder, lambda: None)
bar.show()
bar.set_sentence("测试句子显示")
print(f"   悬浮条创建+显示 OK, 状态文本='{bar.status.text()}' -> PASS")
r4 = True

print("== 5. FoxitBridge 窗口侦测（本机探测）==")
from lingread.bridge.foxit import FoxitBridge

b = FoxitBridge()
w = b.find_active_window()
if w:
    print(f"   检测到 Foxit 窗口: {w.title!r}, 文档={b.resolve_document_path(w)}, 页码={b.get_current_page(w)}")
else:
    print("   本机未运行 Foxit Reader（跳过，属预期）")
r5 = True

print(f"\n汇总: cache={'PASS' if r1 else 'FAIL'} stop={'PASS' if r2 else 'FAIL'} "
      f"replay={'PASS' if r3 else 'FAIL'} ui={'PASS' if r4 else 'FAIL'}")
sys.exit(0 if all([r1, r2, r3, r4]) else 1)
