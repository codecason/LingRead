"""真机演示：侦测 Foxit 当前窗口/页码，真实出声朗读 4 句后自动停止。"""
import sys
import time

sys.path.insert(0, r"D:\Labs\2605-agent\PDF-TTS\src")

from lingread.bridge.foxit import FoxitBridge
from lingread.player.queue import PlaybackController, State
from lingread.text.engine import TextEngine
from lingread.tts.edge import EdgeTTSEngine

bridge = FoxitBridge()
win = bridge.find_active_window()
if not win:
    print("未检测到 Foxit Reader 窗口，请先打开一个 PDF")
    sys.exit(1)

doc = bridge.resolve_document_path(win)
page = bridge.get_current_page(win)
print(f"Foxit 窗口: {win.title!r}")
print(f"文档: {doc}")
print(f"当前页: 第 {page + 1} 页")

engine = TextEngine(str(doc))
sid = engine.first_sentence_on_page(page)
print(f"起始句 id={sid}，即将朗读 4 句...\n")

heard = []

def on_sentence(s):
    heard.append(s.id)
    print(f"♪ [第{s.page + 1}页·句{s.id}] {s.text[:60]}")

ctrl = PlaybackController(engine, EdgeTTSEngine(), on_sentence=on_sentence)  # headless=False 真实播放
ctrl.play_from_sentence(sid)

t0 = time.perf_counter()
while ctrl.state != State.IDLE and len(heard) < 4 and time.perf_counter() - t0 < 60:
    time.sleep(0.1)
time.sleep(0.5)  # 让最后一句放完尾部
ctrl.stop()
engine.close()
print(f"\n演示结束：共朗读 {len(heard)} 句，已自动停止。")
