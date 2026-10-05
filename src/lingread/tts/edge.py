"""TTSEngine 适配层（M1：edge-tts + 磁盘缓存）

设计约定：
- synthesize() 返回完整 mp3 字节（M1 先用整句合成，流式边下边播在 M3 优化）；
- 缓存键 = sha1(engine|voice|rate|text)，命中即零成本零延迟；
- 指数退避重试（实测 edge-tts 存在 NoAudioReceived 瞬时失败）。
"""
from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import time
import os
import json
import uuid
from dataclasses import asdict
from pathlib import Path

import edge_tts
from ..diagnostics import event
from .timing import Speech, WordBoundary

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
CACHE_DIR = Path(os.environ.get('LINGREAD_DATA_DIR', str(Path.home() / '.lingread'))) / 'cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)
_DB = CACHE_DIR / "meta.db"


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, path TEXT, chars INT, created REAL)"
    )
    return conn


def cache_get(key: str) -> bytes | None:
    conn = _db()
    row = conn.execute("SELECT path FROM meta WHERE key=?", (key,)).fetchone()
    conn.close()
    if row:
        p = Path(row[0])
        if p.exists():
            return p.read_bytes()
    return None


def cache_put(key: str, data: bytes, chars: int) -> None:
    p = CACHE_DIR / f"{key}.mp3"
    p.write_bytes(data)
    conn = _db()
    conn.execute(
        "INSERT OR REPLACE INTO meta VALUES (?,?,?,?)", (key, str(p), chars, time.time())
    )
    conn.commit()
    conn.close()


def make_key(engine: str, voice: str, rate: str, text: str) -> str:
    return hashlib.sha1(f"{engine}|{voice}|{rate}|{text}".encode()).hexdigest()


class EdgeTTSEngine:
    name = "edge-tts"
    cost_per_1k_chars = 0.0

    def __init__(self, voice: str = DEFAULT_VOICE, rate: str = "+0%", max_retry: int = 3):
        self.voice = voice
        self.rate = rate
        self.max_retry = max_retry

    async def synthesize(self, text: str) -> bytes:
        return (await self.synthesize_timed(text)).audio

    async def synthesize_timed(self, text: str) -> Speech:
        voice, rate = self.voice, self.rate
        key = make_key(self.name + ':word-v1', voice, rate, text)
        timing_path = CACHE_DIR / f'{key}.json'
        hit = cache_get(key)
        if hit is not None and timing_path.exists():
            try:
                boundaries = tuple(WordBoundary(**item) for item in json.loads(timing_path.read_text('utf-8')))
                event('tts_cache_hit', chars=len(text), voice=voice, rate=rate)
                return Speech(hit, boundaries)
            except (ValueError, TypeError, OSError):
                pass
        last_err: Exception | None = None
        for attempt in range(self.max_retry):
            event('tts_attempt', attempt=attempt+1, chars=len(text), voice=voice, rate=rate)
            try:
                buf = bytearray()
                boundaries = []
                async for chunk in edge_tts.Communicate(
                    text, voice, rate=rate, boundary='WordBoundary'
                ).stream():
                    if chunk["type"] == "audio":
                        buf.extend(chunk["data"])
                    elif chunk['type'] == 'WordBoundary':
                        start = chunk['offset'] / 10_000_000
                        end = start + chunk['duration'] / 10_000_000
                        boundaries.append(WordBoundary(start, end, chunk['text']))
                if not buf:
                    raise RuntimeError("NoAudioReceived")
                data = bytes(buf)
                cache_put(key, data, len(text))
                tmp = timing_path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
                try:
                    tmp.write_text(json.dumps([asdict(b) for b in boundaries], ensure_ascii=False), 'utf-8')
                    tmp.replace(timing_path)
                finally:
                    tmp.unlink(missing_ok=True)
                return Speech(data, tuple(boundaries))
            except Exception as e:  # noqa: BLE001 - 统一退避重试
                event('tts_attempt_failed', attempt=attempt+1, error_type=type(e).__name__)
                last_err = e
                await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"edge-tts 合成失败({self.max_retry}次重试后): {last_err}")


async def list_zh_voices() -> list[str]:
    """音色列表动态拉取（云野下线教训：绝不硬编码）。"""
    vs = await edge_tts.list_voices()
    return [v["ShortName"] for v in vs if v["Locale"].startswith("zh-CN")]
