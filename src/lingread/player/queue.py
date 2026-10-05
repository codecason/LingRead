"""Cancellable sentence queue with character start and audio-clock highlighting."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from enum import Enum
import threading
import time
import uuid
import unicodedata
from ..diagnostics import event
import miniaudio
from ..text.engine import TextEngine
from ..tts.edge import EdgeTTSEngine
from ..tts.timing import build_cues

SYNTH_TIMEOUT = 15.0


class State(Enum):
    IDLE = 0
    READING = 1
    PAUSED = 2


@dataclass
class _Session:
    diagnostic_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    stopped: threading.Event = field(default_factory=threading.Event)
    paused: threading.Event = field(default_factory=threading.Event)
    loop: object = None
    task: object = None
    thread: object = None


class PlaybackController:
    def __init__(self, engine: TextEngine, tts: EdgeTTSEngine,
                 on_sentence=None, on_state=None, headless=False,
                 on_progress=None, on_error=None):
        self.engine, self.tts = engine, tts
        self.on_sentence, self.on_state = on_sentence, on_state
        self.on_progress, self.on_error = on_progress, on_error
        self.headless = headless
        self.state = State.IDLE
        self.current_id = self.start_id = -1
        self.current_offset = self.start_offset = 0
        self._session = None
        self._lock = threading.RLock()

    def _notify_state(self):
        if self.on_state:
            self.on_state(self.state)

    def play_from_sentence(self, sentence_id):
        self.play_from_position(sentence_id, 0)

    def play_from_position(self, sentence_id, offset=0, paused=False):
        self.stop()
        sentences = self.engine.slice_from(sentence_id, offset)
        if not sentences:
            return
        with self._lock:
            self.start_id = self.current_id = sentence_id
            self.start_offset = self.current_offset = offset
            session = _Session()
            event('playback_start', session=session.diagnostic_id,
                  request=getattr(self, 'diagnostic_request', None), sentence=sentence_id, offset=offset)
            if paused:
                session.paused.set()
            self._session = session
            self.state = State.PAUSED if paused else State.READING
            self._notify_state()
            session.thread = threading.Thread(target=self._run, args=(session, sentences), daemon=True)
            session.thread.start()

    def stop(self):
        with self._lock:
            session = self._session
            self._session = None
            if session:
                event('playback_stop', session=session.diagnostic_id)
                session.stopped.set()
                if session.loop and session.task:
                    try:
                        session.loop.call_soon_threadsafe(session.task.cancel)
                    except RuntimeError:
                        pass
            self.state = State.IDLE
            self._notify_state()
        if session and session.thread and session.thread is not threading.current_thread():
            session.thread.join(timeout=.1)

    def pause(self):
        with self._lock:
            if self.state == State.READING and self._session:
                self._session.paused.set()
                self.state = State.PAUSED
                self._notify_state()

    def resume(self):
        with self._lock:
            if self.state == State.PAUSED and self._session:
                self._session.paused.clear()
                self.state = State.READING
                self._notify_state()

    def replay(self):
        sid, offset = self.current_id, self.current_offset
        self.play_from_position(sid, offset)

    def restart_all(self):
        self.play_from_position(max(0, self.start_id), self.start_offset)

    def set_speed(self, rate):
        self.tts.rate = rate

    def set_voice(self, voice):
        if self.tts.voice == voice:
            return
        with self._lock:
            state, sid, offset = self.state, self.current_id, self.current_offset
            start_id, start_offset = self.start_id, self.start_offset
        self.stop()  # Discard prefetched audio from the previous voice.
        self.tts.voice = voice
        if state != State.IDLE and sid >= 0:
            self.play_from_position(sid, offset, paused=state == State.PAUSED)
            self.start_id, self.start_offset = start_id, start_offset

    def _active(self, session):
        return self._session is session and not session.stopped.is_set()

    def _run(self, session, sentences):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        session.loop = loop
        session.task = loop.create_task(self._run_async(session, sentences))
        try:
            if not self._active(session):
                session.task.cancel()
            loop.run_until_complete(session.task)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            event('playback_failed', session=session.diagnostic_id, sentence=self.current_id,
                  error_type=type(exc).__name__)
            with self._lock:
                if self._active(session) and self.on_error:
                    self.on_error(str(exc))
        finally:
            for task in asyncio.all_tasks(loop):
                task.cancel()
            pending = asyncio.all_tasks(loop)
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            with self._lock:
                if self._session is session:
                    self.state = State.IDLE
                    self._notify_state()

    async def _run_async(self, session, sentences):
        queue = asyncio.Queue(maxsize=2)
        async def producer():
            try:
                for s in sentences:
                    if not self._active(session):
                        return
                    if not any(unicodedata.category(ch)[0] not in 'PZC' for ch in s.text):
                        event('synthesis_skipped', session=session.diagnostic_id, sentence=s.id,
                              page=s.page, reason='punctuation_or_whitespace_only')
                        continue
                    started = time.monotonic()
                    event('synthesis_start', session=session.diagnostic_id, sentence=s.id,
                          page=s.page, chars=len(s.text))
                    try:
                        speech = await asyncio.wait_for(self.tts.synthesize_timed(s.text), SYNTH_TIMEOUT)
                    except asyncio.CancelledError:
                        event('synthesis_cancelled', session=session.diagnostic_id, sentence=s.id)
                        raise
                    except Exception as exc:
                        event('synthesis_failed', session=session.diagnostic_id, sentence=s.id,
                              error_type=type(exc).__name__, ms=round((time.monotonic()-started)*1000))
                        raise
                    event('synthesis_ready', session=session.diagnostic_id, sentence=s.id,
                          bytes=len(speech.audio), ms=round((time.monotonic()-started)*1000))
                    await queue.put((s, speech))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await queue.put(exc)
            else:
                await queue.put(None)
        prod = asyncio.create_task(producer())
        try:
            while self._active(session):
                wait_started = time.monotonic()
                item = await queue.get()
                if item is None:
                    return
                if isinstance(item, Exception):
                    raise item
                s, speech = item
                event('audio_ready', session=session.diagnostic_id, sentence=s.id,
                      ms=round((time.monotonic()-wait_started)*1000))
                pcm = miniaudio.decode(speech.audio)
                while session.paused.is_set() and self._active(session):
                    await asyncio.sleep(.02)
                with self._lock:
                    if not self._active(session):
                        return
                    self.current_id = s.id
                    self.current_offset = s.char_start - self.engine.sentences[s.id].char_start
                    if self.on_sentence:
                        self.on_sentence(s)
                cues = build_cues(s.text, speech.boundaries, len(pcm.samples) / pcm.nchannels / pcm.sample_rate)
                await self._play_pcm(pcm, session, s, cues)
                event('audio_finished', session=session.diagnostic_id, sentence=s.id)
        finally:
            prod.cancel()
            await asyncio.gather(prod, return_exceptions=True)

    async def _play_pcm(self, pcm, session, sentence, cues):
        if self.headless:
            return
        samples, nch = pcm.samples, pcm.nchannels
        pos, heard, finished = 0, 0, False
        last_cue = -1
        def stream_audio():
            nonlocal pos, heard, finished
            wanted = yield b''
            while self._active(session):
                if session.paused.is_set():
                    wanted = yield bytes(wanted * nch * 2)
                    continue
                heard = pos
                if pos >= len(samples):
                    finished = True
                    wanted = yield bytes(wanted * nch * 2)
                    continue
                end = min(pos + wanted * nch, len(samples))
                data = samples[pos:end].tobytes()
                pos = end
                wanted = yield data + bytes(wanted * nch * 2 - len(data))
        device = miniaudio.PlaybackDevice(output_format=miniaudio.SampleFormat.SIGNED16,
                                         nchannels=nch, sample_rate=pcm.sample_rate,
                                         buffersize_msec=40)
        try:
            stream = stream_audio()
            next(stream)
            device.start(stream)
            while self._active(session) and not finished:
                if not session.paused.is_set():
                    seconds = heard / nch / pcm.sample_rate
                    index = last_cue
                    while index + 1 < len(cues) and cues[index + 1].at <= seconds:
                        index += 1
                    with self._lock:
                        if index != last_cue and self._active(session):
                            last_cue = index
                            if self.on_progress:
                                cue = cues[index]
                                self.on_progress(sentence, cue.start, cue.end)
                await asyncio.sleep(.01)
        finally:
            device.close()
