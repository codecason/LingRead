import io
import sys
import wave
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from lingread.player.queue import PlaybackController
from lingread.text.engine import Sentence, TextEngine
from lingread.tts.timing import Speech

class PunctuationQueueTests(unittest.TestCase):
    def test_punctuation_fragment_does_not_interrupt_following_text(self):
        class Engine:
            sentences = [Sentence(0, 0, 0, '前一句。'), Sentence(1, 0, 4, '）。'),
                         Sentence(2, 0, 6, '后一句。')]
            slice_from = TextEngine.slice_from
        data = io.BytesIO()
        with wave.open(data, 'wb') as wav:
            wav.setparams((1,2,24000,0,'NONE','not compressed'))
            wav.writeframes(bytes(4800))
        requested, played, errors = [], [], []
        class TTS:
            async def synthesize_timed(self, text):
                requested.append(text)
                if text == '）。': raise RuntimeError('No audio')
                return Speech(data.getvalue(), ())
        ctrl = PlaybackController(Engine(), TTS(), headless=True,
            on_sentence=lambda s:played.append(s.id), on_error=errors.append)
        ctrl.play_from_sentence(0)
        ctrl._session.thread.join(3)
        ctrl.stop()
        self.assertEqual(requested, ['前一句。','后一句。'])
        self.assertEqual(played, [0,2])
        self.assertEqual(errors, [])
