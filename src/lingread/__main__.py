"""聆阅 LingRead 应用入口（M1）

用法：
  python -m lingread                      # Foxit 模式：侦测 Foxit 窗口，从当前页朗读
  python -m lingread --pdf a.pdf --page 3 # 直读模式：指定文件和页码（调试用）
  python -m lingread --pdf a.pdf --headless --max-sentences 3  # 无UI无音频端到端测试
"""
from __future__ import annotations

import argparse
import sys

from .bridge.foxit import FoxitBridge
from .player.queue import PlaybackController, State
from .text.engine import TextEngine
from .tts.edge import DEFAULT_VOICE, EdgeTTSEngine


def log(msg: str) -> None:
    """运行日志，写 ~/.lingread/lingread.log（exe 版无控制台，排障全靠它）。"""
    import pathlib
    import time

    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line)
    try:
        p = pathlib.Path.home() / ".lingread" / "lingread.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _open_doc(pdf_path: str | None, page: int):
    if not pdf_path:
        print("[err] 未找到文档路径")
        return None, -1
    try:
        engine = TextEngine(pdf_path)
    except Exception as e:
        print(f"[err] 打开 PDF 失败: {e}")
        return None, -1
    sid = engine.first_sentence_on_page(max(page, 0))
    if sid < 0:
        print("[warn] 该页之后没有可朗读文本")
        engine.close()
        return None, -1
    return engine, sid


def run_headless(pdf_path: str, page: int, max_sentences: int, voice: str) -> int:
    """无 UI 无音频设备的端到端冒烟：提取->合成->解码->播放流程校验。"""
    import time

    engine, sid = _open_doc(pdf_path, page)
    if engine is None:
        return 1

    t_first = None
    done = []

    def on_sentence(s):
        nonlocal t_first
        if t_first is None:
            t_first = time.perf_counter() - t0
        done.append(s.id)
        print(f"[句{s.id}] (p{s.page + 1}) {s.text[:40]}")

    t0 = time.perf_counter()
    ctrl = PlaybackController(
        engine, EdgeTTSEngine(voice=voice),
        on_sentence=on_sentence, headless=True,
    )
    ctrl.play_from_sentence(sid)
    # headless 模式播得飞快，按句数/时限收敛
    while ctrl.state != State.IDLE and len(done) < max_sentences:
        time.sleep(0.2)
    ctrl.stop()
    total = time.perf_counter() - t0
    print(f"\n[结果] 合成并处理 {len(done)} 句 | 首音延迟(合成) {(t_first or 0):.2f}s | 总耗时 {total:.2f}s")
    engine.close()
    return 0 if done else 2


def run_gui(pdf_path: str | None, page: int, voice: str) -> int:
    from .ui.reader import run_gui as gui
    return gui(pdf_path, page, voice)


def main() -> int:
    ap = argparse.ArgumentParser(prog="lingread", description="聆阅 - Foxit PDF 朗读助手")
    ap.add_argument("--pdf", help="直接指定 PDF 文件路径")
    ap.add_argument("--page", type=int, default=0, help="起始页码（0-based）")
    from . import __version__
    ap.add_argument('--version', action='version', version='LingRead '+__version__)
    ap.add_argument("--voice", default=None, help='指定启动音色（Foxit 模式也生效）')
    ap.add_argument("--headless", action="store_true", help="无UI无音频冒烟测试")
    ap.add_argument("--max-sentences", type=int, default=3)
    args = ap.parse_args()

    if args.headless:
        if not args.pdf:
            ap.error("--headless 需要 --pdf")
        return run_headless(args.pdf, args.page, args.max_sentences, args.voice or DEFAULT_VOICE)
    return run_gui(args.pdf, args.page, args.voice)


if __name__ == "__main__":
    sys.exit(main())
