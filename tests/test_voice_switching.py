import asyncio
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
os.environ['QT_QPA_PLATFORM']='offscreen'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lingread.player.queue import PlaybackController, State
from lingread.text.engine import Sentence

class VoiceSwitchTests(unittest.TestCase):
    def test_switch_cancels_old_voice_and_keeps_sentence_offset_and_pause(self):
        calls=[]
        ready=threading.Event()
        class TTS:
            voice='old'
            async def synthesize_timed(self,text):
                calls.append((self.voice,text))
                ready.set()
                await asyncio.sleep(30)
        engine=SimpleNamespace(slice_from=lambda sid,offset:[Sentence(sid,0,offset,'前言开始朗读'[offset:])])
        ctrl=PlaybackController(engine,TTS(),headless=True)
        try:
            ctrl.play_from_position(0,2)
            self.assertTrue(ready.wait(2))
            ctrl.pause()
            self.assertTrue(hasattr(ctrl,'set_voice'),'Playback must support changing voice')
            ready.clear()
            ctrl.set_voice('new')
            self.assertTrue(ready.wait(2))
            self.assertEqual(calls[:2],[('old','开始朗读'),('new','开始朗读')])
            self.assertEqual(ctrl.state,State.PAUSED)
            self.assertEqual(ctrl.current_offset,2)
        finally: ctrl.stop()

    def test_saved_voice_survives_restart_and_auto_respects_language(self):
        from lingread import tts
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('lingread.tts.voices'))
        from lingread.tts.voices import save_voice,load_voice,resolve_voice
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'preferences.json'
            self.assertEqual(load_voice(path),'auto')
            save_voice('zh-CN-YunxiNeural',path)
            self.assertEqual(load_voice(path),'zh-CN-YunxiNeural')
            self.assertEqual(resolve_voice(load_voice(path),'en'),'zh-CN-YunxiNeural')
            self.assertEqual(resolve_voice('auto','en'),'en-US-AndrewNeural')
            self.assertEqual(resolve_voice('auto','zh'),'zh-CN-XiaoxiaoNeural')
            path.write_text('broken','utf-8')
            self.assertEqual(load_voice(path),'auto')
            path.write_text('{"voice": []}','utf-8')
            self.assertEqual(load_voice(path),'auto')

if __name__=='__main__': unittest.main()
