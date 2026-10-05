import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from lingread.bridge.document import DocumentBinding

class DocumentBindingTests(unittest.TestCase):
    def test_manual_choice_survives_retry_without_automatic_path(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'book.pdf'
            path.write_bytes(b'%PDF-1.7')
            binding = DocumentBinding()
            win = SimpleNamespace(hwnd=1, pid=2, title='book')
            self.assertEqual(binding.resolve(win, None, lambda: str(path)), (path, True))
            self.assertEqual(binding.resolve(win, None, lambda: self.fail('repeated picker')), (path, False))

    def test_switching_document_invalidates_manual_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'book.pdf'
            path.touch()
            binding = DocumentBinding()
            win = SimpleNamespace(hwnd=1, pid=2, title='book')
            binding.resolve(win, None, lambda: str(path))
            win.title = 'other'
            self.assertEqual(binding.resolve(win, None, lambda: ''), (None, False))
            win.title = 'book'
            self.assertEqual(binding.resolve(win, None, lambda: ''), (None, False))

    def test_deleted_file_and_cancel_do_not_reuse_stale_path(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'book.pdf'
            path.touch()
            binding = DocumentBinding()
            win = SimpleNamespace(hwnd=1, pid=2, title='book')
            binding.resolve(win, None, lambda: str(path))
            path.unlink()
            self.assertEqual(binding.resolve(win, None, lambda: ''), (None, False))

    def test_automatic_path_needs_no_picker(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'book.pdf'
            path.touch()
            win = SimpleNamespace(hwnd=1, pid=2, title='book')
            self.assertEqual(DocumentBinding().resolve(win, path, lambda: self.fail('picker')), (path, False))

class AmbiguousPathTests(unittest.TestCase):
    def test_same_filename_in_two_directories_requires_choice(self):
        from unittest.mock import patch
        from lingread.bridge.foxit import FoxitBridge
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder)/'one'/'book.pdf', Path(folder)/'two'/'book.pdf']
            for path in paths:
                path.parent.mkdir()
                path.touch()
            with patch.object(FoxitBridge, '_mru_paths', return_value=[str(p) for p in paths]):
                self.assertIsNone(FoxitBridge().resolve_document_path(SimpleNamespace(title='book.pdf - Foxit Reader')))

if __name__ == '__main__': unittest.main()
