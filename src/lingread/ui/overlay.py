"""高亮叠层：透明、点击穿透的置顶窗口，在 Foxit 视口上画出当前句位置。"""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget


class HighlightOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput  # 点击穿透
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._rects: list[QRect] = []
        self.hide()

    def show_physical_rects(self, rects, hwnd):
        """Map Win32 physical pixels into Qt coordinates on the target monitor."""
        import ctypes
        from ctypes import wintypes
        from PySide6.QtGui import QGuiApplication
        class MONITORINFOEX(ctypes.Structure):
            _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                        ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD),
                        ('szDevice', wintypes.WCHAR * 32)]
        u32 = ctypes.windll.user32
        u32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        u32.MonitorFromWindow.restype = wintypes.HANDLE
        monitor = u32.MonitorFromWindow(hwnd, 2)
        info = MONITORINFOEX()
        info.cbSize = ctypes.sizeof(info)
        u32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        if not u32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            self.clear()
            return
        screen = next((s for s in QGuiApplication.screens() if s.name() == info.szDevice), None)
        if screen is None:
            self.clear()
            return
        ratio, origin = screen.devicePixelRatio(), screen.geometry().topLeft()
        self.show_rects([(round(origin.x() + (x-info.rcMonitor.left)/ratio),
                          round(origin.y() + (y-info.rcMonitor.top)/ratio),
                          max(1, round(w/ratio)), max(1, round(h/ratio))) for x, y, w, h in rects])

    def show_rects(self, screen_rects: list[tuple[int, int, int, int]]):
        """屏幕绝对坐标矩形列表。"""
        if not screen_rects:
            self.hide()
            return
        x0 = min(r[0] for r in screen_rects)
        y0 = min(r[1] for r in screen_rects)
        x1 = max(r[0] + r[2] for r in screen_rects)
        y1 = max(r[1] + r[3] for r in screen_rects)
        self._rects = [QRect(r[0] - x0 + 4, r[1] - y0 + 4, r[2], r[3]) for r in screen_rects]
        self.setGeometry(x0 - 4, y0 - 4, (x1 - x0) + 8, (y1 - y0) + 8)
        self.show()
        self.update()

    def clear(self):
        self._rects = []
        self.hide()

    def paintEvent(self, _e):
        if not self._rects:
            return
        p = QPainter(self)
        p.setPen(QColor(255, 193, 7, 220))
        p.setBrush(QColor(255, 235, 59, 90))  # 半透明黄
        for r in self._rects:
            p.drawRoundedRect(r.adjusted(2, 2, -2, -2), 3, 3)
        p.end()
