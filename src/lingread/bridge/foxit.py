"""FoxitBridge: Foxit Reader 窗口侦测 + 文档路径解析 + 当前页定位（M1）

已在真实 Foxit PDF Reader (Continuous 版本键) 上验证：
- 文档路径：HKCU\\Software\\Foxit Software\\Foxit PDF Reader\\Continuous\\MRU\\File MRU
  值格式 "[F...]*[T...]*<完整路径>"，与窗口标题做规范化模糊匹配
  （标题中 "Volume I_ Process" 显示为 "Process and Architecture" 这类差异需归一化）；
- 当前页：UIA EditControl Name='输入页码跳转到指定页'，读取 ValuePattern 值。
"""
from __future__ import annotations

import ctypes
import re
import winreg
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

import psutil
from ..diagnostics import event

FOXIT_PROCS = {"foxitreader.exe", "foxitpdfreader.exe"}
_TITLE_SUFFIX = re.compile(r"\s*-\s*Foxit.*$", re.IGNORECASE)
_MRU_VALUE = re.compile(r"\]\*(.*)$")


def get_mouse_point() -> tuple[int, int]:
    """当前鼠标光标的屏幕物理像素坐标。"""
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


@dataclass
class FoxitWindow:
    hwnd: int
    title: str
    pid: int


def _enum_windows() -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    EnumWindows = ctypes.windll.user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    GetWindowTextW = ctypes.windll.user32.GetWindowTextW
    GetWindowTextLengthW = ctypes.windll.user32.GetWindowTextLengthW
    IsWindowVisible = ctypes.windll.user32.IsWindowVisible
    GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId

    def cb(hwnd, lparam):
        if not IsWindowVisible(hwnd):
            return True
        n = GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append((hwnd, pid.value, buf.value))
        return True

    EnumWindows(EnumWindowsProc(cb), 0)
    return out


def _pid_is_foxit(pid: int) -> bool:
    try:
        return psutil.Process(pid).name().lower() in FOXIT_PROCS
    except psutil.Error:
        return False


def _normalize(s: str) -> str:
    """标题/文件名归一化：仅保留字母数字与 CJK，忽略大小写。"""
    return re.sub(r"[^0-9a-z一-鿿]", "", s.lower())


class FoxitBridge:
    reader_name = "foxit"

    def selection_text(self, win: FoxitWindow) -> str:
        """Prefer native text ranges; Foxit versions without them use guarded copy."""
        import uiautomation as auto
        root = auto.ControlFromHandle(win.hwnd)
        viewport = self.get_viewport_rect(win)
        def walk(ctrl, depth=0):
            if depth > 12:
                return ''
            try:
                # Exclude ribbon/edit/search/sidebar selections.
                r = ctrl.BoundingRectangle
                in_page = viewport and (viewport[0] <= r.left < viewport[2]
                                        and viewport[1] <= r.top < viewport[3])
                if in_page and ctrl.ControlType != auto.ControlType.EditControl:
                    pattern = ctrl.GetPattern(auto.PatternId.TextPattern)
                    if pattern:
                        text = ''.join(rng.GetText(-1) for rng in pattern.GetSelection())
                        if text.strip():
                            return text
                for child in ctrl.GetChildren():
                    text = walk(child, depth + 1)
                    if text:
                        return text
            except Exception:
                pass
            return ''
        text = walk(root)
        event('selection_uia', chars=len(text), hwnd=win.hwnd)
        if text:
            return text
        text = self._copy_selection(win)
        event('selection_copy', chars=len(text), hwnd=win.hwnd)
        return text

    @staticmethod
    def _copy_selection(win):
        """Copy only in the verified Foxit foreground; restore clipboard formats.

        A sequence change is required. Existing clipboard text is never a selection.
        If another app takes focus or changes the clipboard, do not overwrite it.
        """
        import time
        from PySide6.QtCore import QMimeData, QEventLoop
        from PySide6.QtWidgets import QApplication
        u32 = ctypes.windll.user32
        u32.GetForegroundWindow.restype = wintypes.HWND
        previous = u32.GetForegroundWindow()
        if previous != win.hwnd:
            u32.SetForegroundWindow(win.hwnd)
        if u32.GetForegroundWindow() != win.hwnd:
            event('copy_failed', reason='foreground_not_foxit')
            return ''
        clipboard = QApplication.clipboard()
        old = clipboard.mimeData()
        saved = QMimeData()
        if old:
            for fmt in old.formats():
                saved.setData(fmt, old.data(fmt))
        before = u32.GetClipboardSequenceNumber()
        # INPUT's union must include MOUSEINPUT for correct 64-bit struct size.
        ULONG_PTR = ctypes.c_size_t
        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD),
                        ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ULONG_PTR)]
        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD),
                        ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ULONG_PTR)]
        class UNION(ctypes.Union):
            _fields_ = [('ki', KEYBDINPUT), ('mi', MOUSEINPUT)]
        class INPUT(ctypes.Structure):
            _anonymous_ = ('u',)
            _fields_ = [('type', wintypes.DWORD), ('u', UNION)]
        events = (INPUT * 4)()
        for e, key, flags in zip(events, [0x11, 0x43, 0x43, 0x11], [0, 0, 2, 2]):
            e.type, e.ki = 1, KEYBDINPUT(key, 0, flags, 0, 0)
        # Do not interfere with modifiers the user is still holding.
        if any(u32.GetAsyncKeyState(k) & 0x8000 for k in (0x10, 0x11, 0x12)):
            event('copy_failed', reason='modifier_held')
            return ''
        if u32.SendInput(4, events, ctypes.sizeof(INPUT)) != 4:
            event('copy_failed', reason='send_input_failed')
            return ''
        deadline = time.monotonic() + .5
        copied = before
        while time.monotonic() < deadline and copied == before:
            QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
            time.sleep(.01)
            copied = u32.GetClipboardSequenceNumber()
        text = ''
        if copied != before and u32.GetForegroundWindow() == win.hwnd:
            text = clipboard.text()
            if u32.GetClipboardSequenceNumber() == copied:
                clipboard.setMimeData(saved)
        event('copy_result', reason='copied' if text.strip() else ('clipboard_timeout' if copied == before else 'empty_or_focus_lost'), chars=len(text.strip()))
        return text.strip()

    @staticmethod
    def dpi(win):
        try:
            return ctypes.windll.user32.GetDpiForWindow(win.hwnd) or 96
        except AttributeError:
            return 96

    def scroll_document(self, win, direction):
        """One wheel step directed at the known document child, never globally."""
        hwnd = getattr(self, '_viewport_handles', {}).get(win.hwnd)
        if not hwnd or ctypes.windll.user32.GetForegroundWindow() != win.hwnd:
            return False
        delta = -120 if direction > 0 else 120
        ctypes.windll.user32.PostMessageW(hwnd, 0x020A, (delta & 0xffff) << 16, 0)
        return True

    def find_windows(self) -> list[FoxitWindow]:
        """所有 Foxit 文档窗口（过滤弹窗）。"""
        candidates = [
            FoxitWindow(hwnd=h, title=t, pid=p)
            for h, p, t in _enum_windows()
            if _pid_is_foxit(p)
        ]
        docs = [w for w in candidates if _TITLE_SUFFIX.search(w.title)]
        return docs or candidates

    def find_active_window(self) -> FoxitWindow | None:
        pool = self.find_windows()
        if not pool:
            return None
        fg = ctypes.windll.user32.GetForegroundWindow()
        for w in pool:
            if w.hwnd == fg:
                return w
        return pool[0]

    def resolve_document_path(self, win: FoxitWindow) -> Path | None:
        import difflib

        name = _TITLE_SUFFIX.sub("", win.title).strip()
        if not name:
            return None
        nt = _normalize(name)
        if not nt:
            return None
        best: tuple[float, Path | None] = (0.0, None)
        for full in self._mru_paths():
            p = Path(full)
            if not p.exists():
                continue
            stem = _normalize(p.stem)
            if nt and stem and (nt in stem or stem in nt):
                return p
            # 标题与文件名可能不一致（如 "Volume I_ ..." 标题省略卷号），用相似度兜底
            ratio = difflib.SequenceMatcher(None, nt, stem).ratio()
            if ratio > best[0]:
                best = (ratio, p)
        return best[1] if best[0] >= 0.55 else None

    @staticmethod
    def _mru_paths() -> list[str]:
        paths: list[str] = []
        keys = [
            r"Software\Foxit Software\Foxit PDF Reader\Continuous\MRU\File MRU",
            r"Software\Foxit Software\Foxit Reader\Continuous\MRU\File MRU",
        ]
        for key in keys:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                    i = 0
                    while True:
                        try:
                            _, val, _ = winreg.EnumValue(k, i)
                            i += 1
                        except OSError:
                            break
                        if not isinstance(val, str):
                            continue
                        # 值格式 "[F...]*[T...]*<路径>"，取最后一个 ]* 之后
                        path = val.rsplit("]*", 1)[-1].strip()
                        if path.lower().endswith(".pdf"):
                            paths.append(path)
            except OSError:
                continue
        return paths

    def get_current_page(self, win: FoxitWindow) -> int | None:
        """读取页码输入框（'输入页码跳转到指定页'）的值，返回 0-based 页码。"""
        try:
            import uiautomation as auto
        except ImportError:
            return None
        root = auto.ControlFromHandle(win.hwnd)
        if not root:
            return None

        page_pat = re.compile(r"\((\d+)\s*/\s*(\d+)\)")  # "19 (50 / 1023)" → 逻辑页50

        def walk(ctrl, depth):
            if depth > 12:
                return None
            try:
                if isinstance(ctrl, auto.EditControl) and "页码" in (ctrl.Name or ""):
                    vp = ctrl.GetValuePattern()
                    if vp:
                        val = vp.Value.strip()
                        # PDFs without custom page labels show "8 / 320";
                        # labelled PDFs show "531 (551 / 758)". The bracketed
                        # logical counter takes precedence over the page label.
                        m = page_pat.search(val) or re.fullmatch(r'(\d+)\s*/\s*(\d+)', val)
                        if m:
                            current, total = int(m.group(1)), int(m.group(2))
                            return current - 1 if 1 <= current <= total else None
                        if val.isdigit() and int(val) >= 1:
                            return int(val) - 1
                children = ctrl.GetChildren()
            except Exception:
                return None
            for sub in children:
                r = walk(sub, depth + 1)
                if r is not None:
                    return r
            return None

        return walk(root, 0)

    # ---------- M2：翻页跟随 / 缩放 / 视口 ----------

    def _find_page_edit(self, root):
        import uiautomation as auto

        found = []

        def walk(ctrl, depth):
            if depth > 12 or found:
                return
            try:
                if isinstance(ctrl, auto.EditControl) and "页码" in (ctrl.Name or ""):
                    found.append(ctrl)
                    return
                for sub in ctrl.GetChildren():
                    walk(sub, depth + 1)
            except Exception:
                return

        walk(root, 0)
        return found[0] if found else None

    def follow_page(self, win: FoxitWindow, page: int) -> bool:
        """让 Foxit 跳转到指定页（0-based）。

        不抢前台焦点、不用全局 SendKeys（避免误输入到其他窗口）：
        优先 LegacyIAccessible.SetValue 写页码框，再向该控件句柄
        PostMessage 回车键。
        """
        try:
            import uiautomation as auto
        except ImportError:
            return False
        try:
            root = auto.ControlFromHandle(win.hwnd)
            edit = self._find_page_edit(root)
            if not edit:
                return False
            hwnd = getattr(edit, "NativeWindowHandle", 0)
            if not hwnd:
                return False
            # 1) 页码标签偏移换算：框值形如 "19 (50 / 1023)"（标签19=逻辑页50，
            #    前置页无编号导致偏移31），输入被按"页码标签"解释，必须换算
            offset = 0
            try:
                m = re.search(r"(\d+)\s*\((\d+)\s*/\s*(\d+)\)",
                              edit.GetValuePattern().Value or "")
                if m:
                    offset = int(m.group(2)) - int(m.group(1))  # 逻辑 - 标签
            except Exception:
                pass
            label = max(1, (page + 1) - offset)
            # 2) 写值（LegacyIAccessible，Foxit 页码框支持）
            try:
                edit.GetLegacyIAccessiblePattern().SetValue(str(label))
            except Exception:
                # 退路：直接发 WM_SETTEXT
                ctypes.windll.user32.SendMessageW(hwnd, 0x000C, 0, str(label))
            # 2) 定向投递回车
            WM_KEYDOWN, WM_KEYUP, WM_CHAR, VK_RETURN = 0x0100, 0x0101, 0x0102, 0x0D
            u32 = ctypes.windll.user32
            u32.PostMessageW(hwnd, WM_KEYDOWN, VK_RETURN, 0x001C0001)
            u32.PostMessageW(hwnd, WM_CHAR, 0x0D, 0x001C0001)
            u32.PostMessageW(hwnd, WM_KEYUP, VK_RETURN, 0xC01C0001)
            return True
        except Exception as e:
            print(f"[warn] follow_page 失败: {e}")
            return False

    def get_zoom(self, win: FoxitWindow) -> float | None:
        """读缩放比例（如 '150%' -> 1.5）。"""
        try:
            import uiautomation as auto
        except ImportError:
            return None
        root = auto.ControlFromHandle(win.hwnd)
        if not root:
            return None
        zoom_pat = re.compile(r"^(\d+(?:\.\d+)?)%$")
        result = []

        def walk(ctrl, depth):
            if depth > 12 or result:
                return
            try:
                if isinstance(ctrl, auto.EditControl):
                    m = zoom_pat.match((ctrl.GetValuePattern().Value or "").strip())
                    if m:
                        result.append(float(m.group(1)) / 100.0)
                        return
                for sub in ctrl.GetChildren():
                    walk(sub, depth + 1)
            except Exception:
                return

        walk(root, 0)
        return result[0] if result else None

    def get_viewport_rect(self, win: FoxitWindow) -> tuple[int, int, int, int] | None:
        """文档渲染视口的屏幕矩形 (left, top, right, bottom)。

        Foxit 页面区不是 DocumentControl，而是右侧最深的无名大 Pane
        （左侧'书签'栏和整窗'工作区'需排除）。
        """
        try:
            import uiautomation as auto
        except ImportError:
            return None
        root = auto.ControlFromHandle(win.hwnd)
        if not root:
            return None
        # 窗口矩形，用于排除越界的浮动/隐藏面板
        wrect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(win.hwnd, ctypes.byref(wrect))
        wl, wt, wr, wb = wrect.left, wrect.top, wrect.right, wrect.bottom

        best = {"depth": -1, "area": 0, "rect": None, 'hwnd': 0}
        skip_names = {"书签", "工作区", "导航"}

        def walk(ctrl, depth):
            if depth > 12:
                return
            try:
                if isinstance(ctrl, auto.PaneControl) and (ctrl.Name or "") not in skip_names:
                    r = ctrl.BoundingRectangle
                    # 必须基本落在窗口矩形内（容差15px），排除隐藏/浮动面板
                    inside = (r.left >= wl - 15 and r.right <= wr + 15
                              and r.top >= wt - 15 and r.bottom <= wb + 15)
                    area = r.width() * r.height()
                    if (inside and r.width() > 200 and r.height() > 180
                            and (depth, area) > (best['depth'], best['area'])):
                        best['depth'] = depth
                        best["area"] = area
                        best["rect"] = (r.left, r.top, r.right, r.bottom)
                        best['hwnd'] = ctrl.NativeWindowHandle
                for sub in ctrl.GetChildren():
                    if (sub.Name or '') in skip_names - {'工作区'}:
                        continue
                    walk(sub, depth + 1)
            except Exception:
                return

        walk(root, 0)
        if not hasattr(self, '_viewport_handles'):
            self._viewport_handles = {}
        self._viewport_handles[win.hwnd] = best['hwnd']
        return best["rect"]
