import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import uiautomation as auto
from lingread.bridge.foxit import FoxitBridge

class PageEdit(auto.EditControl):
    @property
    def Name(self): return '输入页码跳转到指定页'
    def GetChildren(self): return []
    def GetValuePattern(self): return SimpleNamespace(Value=self.page_value)

class FoxitPageNumberTests(unittest.TestCase):
    def read(self, value):
        edit=PageEdit()
        edit.page_value=value
        with patch.object(auto, 'ControlFromHandle', return_value=edit):
            return FoxitBridge().get_current_page(SimpleNamespace(hwnd=123))

    def test_plain_current_and_total_from_new_document(self):
        self.assertEqual(self.read('8 / 320'), 7)
        self.assertEqual(self.read('320/320'), 319)

    def test_labelled_and_unlabelled_page_formats(self):
        for value, expected in [('531 (551 / 758)',550), ('iv (4 / 320)',3),
                                ('8',7), (' 8\u00a0/\u00a0320 ',7)]:
            with self.subTest(value=value):
                self.assertEqual(self.read(value),expected)

    def test_invalid_page_counters_are_not_valid_indices(self):
        for value in ['0 / 320','321 / 320','8 / 0','0','','150%','loading']:
            with self.subTest(value=value):
                self.assertIsNone(self.read(value))

if __name__=='__main__': unittest.main()
