"""悬浮控制条 + 系统托盘（PySide6，无边框置顶）"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QWidget,
)

from ..player.queue import State
from .. import __version__
from ..tts.voices import VOICE_OPTIONS


def make_app_icon() -> QIcon:
    """绘制托盘/窗口图标：蓝底白字「聆」。"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#4f8cff"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(Qt.white)
    f = p.font()
    f.setPixelSize(34)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "聆")
    p.end()
    return QIcon(pm)

_STYLE = """
QWidget { background: #1f2430; border-radius: 10px; }
QPushButton {
    color: #e8eaf0; background: #2c3344; border: none;
    border-radius: 6px; padding: 6px 10px; font-size: 14px;
}
QPushButton:hover { background: #3a4358; }
QLabel { color: #9aa3b5; font-size: 12px; padding: 0 4px; }
QMenu { background: #1f2430; color: #e8eaf0; }
QMenu::item { padding: 8px 18px; }
QMenu::item:selected { background: #3a4358; }
"""
# 容器样式叠加亮边框，提升辨识度
_STYLE += """
QWidget#FloatingBar { border: 2px solid #4f8cff; }
"""


class UiBridge(QObject):
    """跨线程信号桥：后台播放线程 -> UI 线程。"""
    sentence_changed = Signal(str)
    state_changed = Signal(object)
    highlight = Signal(list)  # [(x, y, w, h), ...] 屏幕绝对坐标

SPEEDS = [("1.0x", "+0%"), ("1.25x", "+25%"), ("1.5x", "+50%"), ("2.0x", "+100%"), ("0.75x", "-25%")]
LANGS = [("🌐自动", "auto"), ("🌐中文", "zh"), ("🌐EN", "en")]


class FloatingBar(QWidget):
    voice_changed = Signal(str)
    def __init__(self, controller_holder: dict, on_pick_target, on_locate=None):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setObjectName("FloatingBar")
        self.setWindowTitle('聆阅 LingRead v'+__version__)
        self.setStyleSheet(_STYLE)
        self._holder = controller_holder
        self._on_pick_target = on_pick_target
        self._drag_pos = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        self.lbl = QLabel('🦊 聆阅 v'+__version__)
        self.btn_read = QPushButton("▶ 从光标读")
        self.btn_pause = QPushButton("⏸")
        self.btn_stop = QPushButton("⏹")
        self.btn_replay = QPushButton("↺")
        self.btn_locate = QPushButton("⌖ 定位")
        self.btn_locate.setToolTip("回到当前音频对应的正文位置；暂停时也可定位")
        self.btn_speed = QPushButton("1.0x")
        self.btn_speed.setToolTip("朗读语速（点击循环切换）")
        self._speed_idx = 0
        self.status = QLabel("就绪")
        self.btn_close = QPushButton("✕")
        self.btn_close.setToolTip("退出聆阅")
        self.btn_lang = QPushButton("🌐自动")
        self.btn_lang.setToolTip("朗读语言：自动检测 / 中文 / English（点击切换）")
        self._lang_idx = 0
        self.btn_voice = QPushButton()
        self.btn_voice.setToolTip('选择音色并记住设置；播放中切换会用新音色重读当前句，暂停时仍保持暂停')
        self.voice_menu = QMenu(self)
        self.voice_group = QActionGroup(self)
        self.voice_group.setExclusive(True)
        self.voice_actions = {}
        for value, label in VOICE_OPTIONS:
            action = self.voice_menu.addAction(label)
            action.setCheckable(True)
            action.setData(value)
            self.voice_group.addAction(action)
            action.triggered.connect(lambda checked=False, v=value: self.voice_changed.emit(v))
            self.voice_actions[value] = action
        self.btn_voice.setMenu(self.voice_menu)
        self.set_voice_label(controller_holder.get('voice', 'auto'))
        for w in (self.lbl, self.btn_read, self.btn_pause, self.btn_stop,
                  self.btn_replay, self.btn_locate, self.btn_speed, self.btn_lang, self.btn_voice, self.status, self.btn_close):
            lay.addWidget(w)
            w.setFocusPolicy(Qt.NoFocus)

        self.btn_read.clicked.connect(self._read)
        self.btn_pause.clicked.connect(self._pause)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_replay.clicked.connect(self._replay)
        if on_locate:
            self.btn_locate.clicked.connect(on_locate)
        self.btn_speed.clicked.connect(self._speed)
        self.btn_lang.clicked.connect(self._lang)
        self.btn_close.clicked.connect(self._quit)

    def set_voice_label(self, value):
        label = dict(VOICE_OPTIONS).get(value, value)
        self.btn_voice.setText('音色：'+label.split(' · ')[0])
        for key, action in self.voice_actions.items():
            action.setChecked(key == value)

    def _quit(self):
        ctrl = self._ctrl()
        if ctrl:
            ctrl.stop()
        QApplication.instance().quit()

    def _ctrl(self):
        return self._holder.get("controller")

    def _read(self):
        self._on_pick_target()  # 定位 Foxit 当前页并从该处朗读

    def _pause(self):
        ctrl = self._ctrl()
        if ctrl:
            if ctrl.state == State.READING:
                ctrl.pause()
            elif ctrl.state == State.PAUSED:
                ctrl.resume()

    def _stop(self):
        ctrl = self._ctrl()
        if ctrl:
            ctrl.stop()

    def _replay(self):
        ctrl = self._ctrl()
        if ctrl:
            ctrl.replay()

    def _speed(self):
        self._speed_idx = (self._speed_idx + 1) % len(SPEEDS)
        label, rate = SPEEDS[self._speed_idx]
        self._holder['rate'] = rate
        self.btn_speed.setText(label)
        ctrl = self._ctrl()
        if ctrl:
            ctrl.set_speed(rate)

    def _lang(self):
        self._lang_idx = (self._lang_idx + 1) % len(LANGS)
        label, mode = LANGS[self._lang_idx]
        self.btn_lang.setText(label)
        self._holder["lang_mode"] = mode

    def set_sentence(self, text: str):
        self.status.setTextFormat(Qt.PlainText)
        self.status.setText(text[:28] + ("…" if len(text) > 28 else ""))

    def set_state(self, state: State):
        names = {State.IDLE: "就绪", State.READING: "朗读中", State.PAUSED: "已暂停"}
        if state == State.IDLE:
            self.status.setTextFormat(Qt.PlainText)
            self.status.setText(names[state])

    # 拖拽移动
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None


def make_tray(app: QApplication, bar: FloatingBar) -> QSystemTrayIcon:
    icon = make_app_icon()
    bar.setWindowIcon(icon)
    tray = QSystemTrayIcon(icon, app)
    menu = QMenu()
    act_show = QAction("显示/隐藏悬浮条", menu)
    act_show.triggered.connect(lambda: bar.setVisible(not bar.isVisible()))
    act_quit = QAction("退出聆阅", menu)
    act_quit.triggered.connect(bar._quit)
    menu.addAction(act_show)
    menu.addAction(act_quit)
    tray.setContextMenu(menu)
    tray.setToolTip('聆阅 LingRead v'+__version__+'（右键退出）')
    tray.show()
    return tray
