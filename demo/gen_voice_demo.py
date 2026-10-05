import asyncio
import edge_tts
from pathlib import Path

OUT = Path(r"D:\Labs\2605-agent\PDF-TTS\demo\voices")
OUT.mkdir(parents=True, exist_ok=True)

# 模拟真实 PDF 朗读场景：中英混排 + 数字 + 多音字
TEXT = (
    "根据2025年第三季度财报，公司实现营业收入128.6亿元，同比增长23.4%。"
    "报告指出，Transformer架构的引入使模型推理速度提升了近3倍。"
    "值得注意的是，银行行长表示，行业长期向好的趋势没有改变。"
)

VOICES = [
    ("zh-CN-XiaoxiaoNeural", "晓晓_女声_温暖"),
    ("zh-CN-YunxiNeural", "云希_男声_年轻"),
    ("zh-CN-YunyeNeural", "云野_男声_成熟"),
    ("zh-CN-XiaoyiNeural", "晓伊_女声_活泼"),
]

async def gen(voice: str, label: str):
    path = OUT / f"{label}.mp3"
    await edge_tts.Communicate(TEXT, voice).save(str(path))
    print("OK", path.name, path.stat().st_size, "bytes")

async def main():
    for v, label in VOICES:
        await gen(v, label)

asyncio.run(main())
