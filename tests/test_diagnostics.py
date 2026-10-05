import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

class DiagnosticTests(unittest.TestCase):
    def test_failed_synthesis_identifies_sentence_without_recording_text(self):
        from lingread import diagnostics as d
        from lingread.player.queue import PlaybackController
        from lingread.text.engine import TextEngine, Sentence
        class Engine:
            sentences = [Sentence(0, 3, 0, 'PRIVATE BODY')]
            slice_from = TextEngine.slice_from
        class TTS:
            async def synthesize_timed(self, text):
                raise TimeoutError('PRIVATE ERROR')
        with tempfile.TemporaryDirectory() as folder:
            path = d.configure(Path(folder))
            ctrl = PlaybackController(Engine(), TTS(), headless=True)
            ctrl.diagnostic_request = 7
            ctrl.play_from_sentence(0)
            ctrl._session.thread.join(3)
            self.assertFalse(ctrl._session.thread.is_alive())
            ctrl.stop()
            d.close()
            content = path.read_text('utf-8')
            rows = [json.loads(line) for line in content.splitlines()]
            start = next(r for r in rows if r['event']=='playback_start')
            failure = next(r for r in rows if r['event']=='synthesis_failed')
            self.assertEqual(start['request'], 7)
            self.assertEqual(failure['session'], start['session'])
            self.assertEqual((failure['sentence'],failure['error_type']), (0,'TimeoutError'))
            self.assertNotIn('PRIVATE', content)

    def test_records_context_and_rejects_sensitive_fields(self):
        from lingread import diagnostics as d
        with tempfile.TemporaryDirectory() as folder:
            path = d.configure(Path(folder))
            d.event('probe', request=12, page=7, text='PRIVATE', path='PRIVATE',
                    error=ValueError('PRIVATE'), chars=5)
            d.close()
            rows = [json.loads(line) for line in path.read_text('utf-8').splitlines()]
            row = rows[-1]
            self.assertEqual((row['event'], row['request'], row['page']), ('probe', 12, 7))
            self.assertGreaterEqual(row['uptime_s'], 0)
            self.assertIn('version', row)
            self.assertIn('run', row)
            self.assertNotIn('PRIVATE', path.read_text('utf-8'))
    def test_repeated_geometry_is_throttled_but_failure_change_is_visible(self):
        from lingread import diagnostics as d
        with tempfile.TemporaryDirectory() as folder:
            path = d.configure(Path(folder))
            for _ in range(20): d.event('geometry', throttle=60, reason='no_match')
            d.event('geometry', throttle=60, reason='matched')
            d.close()
            rows = [json.loads(line) for line in path.read_text('utf-8').splitlines()]
            self.assertEqual([r['reason'] for r in rows if r['event']=='geometry'], ['no_match','matched'])
