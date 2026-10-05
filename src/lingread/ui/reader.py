"""Qt-side reading coordination. All PDF geometry and widget work stays here."""
from __future__ import annotations
import ctypes
import logging
from concurrent.futures import ThreadPoolExecutor
import sys
import time
from ..diagnostics import event, heartbeat
from PySide6.QtCore import QObject, Signal, QTimer, Qt, QEventLoop
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QGuiApplication, QCursor
from ..bridge.foxit import FoxitBridge, get_mouse_point
from ..bridge.geometry import capture_viewport, render_page, register_page
from ..text.engine import TextEngine, ReadingPosition
from ..tts.edge import EdgeTTSEngine
from ..tts.voices import load_voice, save_voice, resolve_voice
from ..player.queue import PlaybackController, State
from .bar import FloatingBar, make_tray
from .overlay import HighlightOverlay

log = logging.getLogger(__name__)


def resolve_start(engine, selection, page, point):
    if selection:
        pos = engine.locate_selection(selection, page, point)
        if pos is None:
            event('start_rejected', reason='selection_not_in_pdf', page=page, chars=len(selection))
            raise ValueError('选区无法匹配 PDF 文字，请重新选择文字（扫描件需要 OCR）')
        return pos
    if point:
        pos = engine.position_at_point(page, *point)
        if pos:
            return pos
        event('start_rejected', reason='point_misses_text', page=page)
        raise ValueError('光标未命中文字，请圈选要朗读的文字')
    event('start_rejected', reason='no_selection_or_mapped_point', page=page)
    raise ValueError('请先在 Foxit 圈选文字或点击文字，再按朗读')


class Events(QObject):
    playback = Signal(object)
    geometry = Signal(object)


def follow_direction(rect, viewport):
    """Keep the spoken line inside a small vertical reading margin."""
    margin = min(60, (viewport[3] - viewport[1]) / 8)
    if rect[1] < viewport[1] + margin:
        return -1
    if rect[3] > viewport[3] - margin:
        return 1
    return 0


def run_gui(pdf_path, page, voice):
    app = QApplication.instance() or QApplication(sys.argv)
    bridge, events = FoxitBridge(), Events()
    holder = {'lang_mode': 'auto', 'voice': voice or load_voice()}
    overlay = HighlightOverlay()
    workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix='page-registration')
    ctx = dict(engine=None, win=None, page=page, anchor=None, down=False,
               drag=None, windows=[], windows_at=0, epoch=0, geometry=None,
               future=None, gray=None, gray_page=-1, word=None, busy=False,
               error=None, scroll_at=0, geo_id=0, locating=False, locate_until=0,
               recover_at=0)
    u32 = ctypes.windll.user32
    u32.GetForegroundWindow.restype = ctypes.c_void_p

    def show_error(message):
        log.warning('reader_error: %s', message)
        ctx['error'] = message
        bar.status.setTextFormat(Qt.PlainText)
        bar.status.setText(message)
        bar.status.setToolTip(message)

    def to_pdf(point, geo):
        x, y, scale, _ = geo
        pt = ((point[0] - x) / scale, (point[1] - y) / scale)
        import pymupdf
        return tuple(pymupdf.Point(*pt) * ctx['engine'].doc[ctx['page']].derotation_matrix)

    def show_word():
        if ctx['locating'] and time.monotonic() > ctx['locate_until']:
            ctx['locating'] = False
        if not ctx['word'] or not ctx['geometry'] or not ctx['win']:
            overlay.clear()
            return
        if u32.GetForegroundWindow() not in (ctx['win'].hwnd, int(bar.winId())):
            overlay.clear()
            return
        s, start, end = ctx['word']
        if s.page != ctx['page']:
            return
        rects, _ = ctx['engine'].rects_for_range(s, start, end)
        x, y, scale, viewport = ctx['geometry']
        rotation = ctx['engine'].doc[s.page].rotation_matrix
        screen_rects = []
        for r in rects:
            r = r * rotation
            left, top, right, bottom = x+r.x0*scale, y+r.y0*scale, x+r.x1*scale, y+r.y1*scale
            left, top = max(left, viewport[0]), max(top, viewport[1])
            right, bottom = min(right, viewport[2]), min(bottom, viewport[3])
            if right > left and bottom > top:
                screen_rects.append((round(left), round(top), round(right-left), round(bottom-top)))
        overlay.show_physical_rects(screen_rects, ctx['win'].hwnd)
        ctrl = holder.get('controller')
        if rects and ctrl and ctx['locating']:
            r = rects[0] * rotation
            direction = follow_direction((x+r.x0*scale, y+r.y0*scale,
                                          x+r.x1*scale, y+r.y1*scale), viewport)
            if not direction:
                ctx['locating'] = False
            if direction and time.monotonic() - ctx['scroll_at'] > .4:
                if bridge.scroll_document(ctx['win'], direction):
                    ctx['scroll_at'] = time.monotonic()
                    ctx['geometry'] = None
                    ctx['geo_id'] += 1
                    QTimer.singleShot(150, refresh_geometry)

    def geometry_result(payload):
        epoch, pg, geo_id, viewport, result = payload
        if epoch != ctx['epoch'] or pg != ctx['page'] or geo_id != ctx['geo_id']:
            return
        event('geometry', throttle=30, request=epoch, page=pg,
              reason='matched' if result else 'no_match', geometry=bool(result))
        if result:
            x, y, scale, _ = result
            ctx['geometry'] = (viewport[0]+x, viewport[1]+y, scale*2, viewport)
        else:
            ctx['geometry'] = None
            # Only an explicit Locate request may bring the reader back.
            ctrl = holder.get('controller')
            if (ctx['word'] and ctrl and ctx['locating']
                    and time.monotonic() < ctx['locate_until']
                    and time.monotonic() - ctx['recover_at'] > 1.5):
                current = bridge.get_current_page(ctx['win'])
                if current is not None and current != ctx['page']:
                    ctx['recover_at'] = time.monotonic()
                    bridge.follow_page(ctx['win'], ctx['page'])
        show_word()

    events.geometry.connect(geometry_result)

    def refresh_geometry(synchronous=False):
        if ctx['locating'] and time.monotonic() > ctx['locate_until']:
            ctx['locating'] = False
        win, eng = ctx['win'], ctx['engine']
        if not win or not eng or (ctx['busy'] and not synchronous):
            return
        if u32.GetForegroundWindow() not in (win.hwnd, int(bar.winId())):
            overlay.clear()
            ctx['geometry'] = None
            return
        if not synchronous and ctx['future'] and not ctx['future'].done():
            return
        try:
            viewport = bridge.get_viewport_rect(win)
            if not viewport:
                return
            pno = ctx['page']
            if ctx['gray_page'] != pno:
                ctx['gray'] = render_page(eng.doc[pno])
                ctx['gray_page'] = pno
            screen = capture_viewport(viewport)
            expected = (bridge.get_zoom(win) or 1.0) * bridge.dpi(win) / 72 / 2
            epoch = ctx['epoch']
            ctx['geo_id'] += 1
            geo_id = ctx['geo_id']
            if synchronous:
                geometry_result((epoch, pno, geo_id, viewport, register_page(ctx['gray'], screen, expected)))
            else:
                future = workers.submit(register_page, ctx['gray'], screen, expected)
                ctx['future'] = future
                def ready(f):
                    try:
                        result = f.result()
                    except Exception:
                        result = None
                    events.geometry.emit((epoch, pno, geo_id, viewport, result))
                future.add_done_callback(ready)
        except Exception as exc:
            event('geometry_exception', throttle=30, request=ctx['epoch'], error_type=type(exc).__name__)
            ctx['geometry'] = None
            overlay.clear()
            print(f'[geometry] {exc}')

    def playback_event(payload):
        epoch, kind, value, session = payload
        if epoch != ctx['epoch']:
            return
        ctrl = holder.get('controller')
        if ctrl and session is not ctrl._session:
            return
        if kind == 'error':
            show_error('朗读失败：' + value)
        elif kind == 'state':
            log.info('playback_state=%s', value)
            if ctrl and value != ctrl.state:
                return
            bar.set_state(value)
            if value == State.IDLE:
                ctx['locating'] = False
                ctx['word'] = None
                overlay.clear()
                if ctx['error']:
                    show_error(ctx['error'])
        elif ctrl and ctrl.state != State.IDLE:
            if kind == 'sentence':
                s = value
                bar.set_sentence(s.text)
                if s.page != ctx['page']:
                    ctx['page'] = s.page
                    ctx['geometry'] = None
                    overlay.clear()
                    ctx['locating'] = False
                    QTimer.singleShot(200, refresh_geometry)
            elif kind == 'word':
                ctx['word'] = value
                show_word()
    events.playback.connect(playback_event)

    def poll_mouse():
        if ctx['busy']:
            return
        now = time.monotonic()
        if now - ctx['windows_at'] > 1:
            ctx['windows'] = bridge.find_windows()
            ctx['windows_at'] = now
        fg = u32.GetForegroundWindow()
        down = bool(u32.GetAsyncKeyState(1) & 0x8000)
        point = get_mouse_point()
        # A non-activating toolbar keeps Foxit as the foreground window. Mouse
        # presses on our own controls are not new document positions.
        if bar.frameGeometry().contains(QCursor.pos()):
            ctx['down'] = down
            ctx['drag'] = None
            return
        win = next((w for w in ctx['windows'] if w.hwnd == fg), None)
        if down and not ctx['down'] and win:
            viewport = bridge.get_viewport_rect(win)
            if viewport and viewport[0] <= point[0] < viewport[2] and viewport[1] <= point[1] < viewport[3]:
                ctx['drag'] = (win, point)
                ctx['anchor'] = (win.hwnd, win.title, point)
            else:
                ctx['drag'] = None
                ctx['anchor'] = None
        elif not down and ctx['down'] and ctx['drag']:
            drag_win, start = ctx['drag']
            # Reading order, not drag direction. The actual copied selection is authoritative.
            anchor = min((start, point), key=lambda p: (round(p[1] / 8), p[0]))
            ctx['anchor'] = (drag_win.hwnd, drag_win.title, anchor)
            ctx['drag'] = None
        ctx['down'] = down
        if ctx['win'] and fg not in (ctx['win'].hwnd, int(bar.winId())):
            overlay.clear()

    def pick_target():
        if ctx['busy']:
            return
        ctx['busy'] = True
        bar.btn_read.setEnabled(False)
        bar.status.setTextFormat(Qt.PlainText)
        bar.status.setText('正在获取选区…')
        app.processEvents(QEventLoop.ExcludeUserInputEvents)
        log.info('read_requested anchor=%s', bool(ctx['anchor']))
        ctx['epoch'] += 1
        epoch = ctx['epoch']
        started_at = time.monotonic()
        event('read_request', request=epoch, anchor=bool(ctx['anchor']))
        ctx['error'] = None
        ctx['word'] = None
        ctx['locating'] = False
        overlay.clear()
        old = holder.get('controller')
        if old:
            old.stop()
        try:
            win = bridge.find_active_window() if not pdf_path else None
            anchor = ctx['anchor']
            path, pg, selection = pdf_path, page, ''
            if win:
                path = bridge.resolve_document_path(win)
                if path is None:
                    event('start_rejected', request=epoch, reason='document_path_unknown')
                    raise ValueError('无法确认当前 PDF 路径，请使用 --pdf 指定文件')
                pg = bridge.get_current_page(win)
                if pg is None:
                    event('start_rejected', request=epoch, reason='page_control_unreadable')
                    raise ValueError('无法读取 Foxit 当前页码')
            if not path:
                event('start_rejected', request=epoch, reason='foxit_not_found')
                raise ValueError('请先用 Foxit 打开 PDF')
            path = str(path)
            if ctx['engine'] is None or ctx['engine'].pdf_path != path:
                bar.status.setText('正在索引 PDF…')
                app.processEvents(QEventLoop.ExcludeUserInputEvents)
                if ctx['engine']:
                    ctx['engine'].close()
                ctx['engine'] = TextEngine(path)
                ctx['gray_page'] = -1
            eng = ctx['engine']
            if not 0 <= pg < eng.page_count:
                raise ValueError('当前页码不在 PDF 范围内')
            ctx.update(win=win, page=pg, geometry=None)
            event('document_ready', request=epoch, page=pg, chars=len(selection),
                  hwnd=win.hwnd if win else None)
            point = None
            if win:
                refresh_geometry(synchronous=True)
                if anchor and anchor[:2] == (win.hwnd, win.title) and ctx['geometry']:
                    point = to_pdf(anchor[2], ctx['geometry'])
                # Clipboard fallback can change foreground focus. Capture the
                # click-to-PDF transform first, while Foxit still owns focus.
                selection = bridge.selection_text(win)
                log.info('foxit_selected page=%s selection_chars=%s', pg, len(selection))
                event('start_inputs', request=epoch, page=pg, chars=len(selection),
                      anchor=bool(anchor), matched=bool(anchor and anchor[:2] == (win.hwnd, win.title)),
                      geometry=bool(ctx['geometry']), point=bool(point), foreground=u32.GetForegroundWindow())
                if not selection and not point and anchor:
                    raise ValueError('已记录点击，但页面坐标匹配失败。请保持 Foxit 正文可见后重试，或圈选文字朗读。')
                pos = resolve_start(eng, selection, pg, point)
            else:
                pos = ReadingPosition(eng.first_sentence_on_page(pg))
            if pos.sentence_id < 0:
                raise ValueError('该页没有可朗读文本')
            lang = holder['lang_mode']
            if lang == 'auto':
                lang = eng.detect_language()
            chosen = resolve_voice(holder['voice'], lang)
            def emit(kind, value):
                events.playback.emit((epoch, kind, value, ctrl._session))
            ctrl = PlaybackController(eng, EdgeTTSEngine(voice=chosen),
                on_sentence=lambda s: emit('sentence', s),
                on_state=lambda st: emit('state', st),
                on_progress=lambda s, a, b: emit('word', (s, a, b)),
                on_error=lambda e: emit('error', e))
            holder['controller'] = ctrl
            ctrl.diagnostic_request = epoch
            ctrl.set_speed(holder.get('rate', '+0%'))
            ctrl.play_from_position(pos.sentence_id, pos.offset)
            event('read_started', request=epoch, page=pg, sentence=pos.sentence_id,
                  offset=pos.offset, geometry=bool(ctx['geometry']), ms=round((time.monotonic()-started_at)*1000))
            bar.status.setText('正在合成语音…')
            log.info('play_started page=%s sentence=%s offset=%s geometry=%s', pg,
                     pos.sentence_id, pos.offset, bool(ctx['geometry']))
            print(f'[start] page={eng.sentences[pos.sentence_id].page+1} sentence={pos.sentence_id} offset={pos.offset} selection={bool(selection)} geometry={bool(ctx["geometry"])}')
        except Exception as exc:
            event('read_failed', request=epoch, error_type=type(exc).__name__,
                  anchor=bool(ctx['anchor']), geometry=bool(ctx['geometry']),
                  ms=round((time.monotonic()-started_at)*1000))
            log.exception('read_start_failed')
            show_error(str(exc))
        finally:
            ctx['busy'] = False
            bar.btn_read.setEnabled(True)

    def locate_current():
        ctrl = holder.get('controller')
        if not ctx['word'] or not ctx['win'] or not ctrl or ctrl.state == State.IDLE:
            show_error('请先开始朗读，再定位当前文字')
            return
        win = ctx['win']
        if u32.IsIconic(win.hwnd):
            u32.ShowWindow(win.hwnd, 9)
        u32.SetForegroundWindow(win.hwnd)
        ctx['locating'] = True
        ctx['locate_until'] = time.monotonic() + 12
        current = bridge.get_current_page(win)
        if current != ctx['page'] or ctx['geometry'] is None:
            bridge.follow_page(win, ctx['page'])
        ctx['geometry'] = None
        ctx['geo_id'] += 1
        QTimer.singleShot(200, refresh_geometry)

    def change_voice(selected):
        try:
            save_voice(selected)
        except (OSError, ValueError) as exc:
            bar.set_voice_label(holder['voice'])
            show_error('无法保存音色：'+str(exc))
            return
        holder['voice'] = selected
        bar.set_voice_label(selected)
        ctx['error'] = None
        ctrl = holder.get('controller')
        if ctrl:
            lang = holder['lang_mode']
            if lang == 'auto':
                lang = ctx['engine'].detect_language()
            ctrl.set_voice(resolve_voice(selected, lang))
        log.info('voice_selected=%s', selected)

    bar = FloatingBar(holder, pick_target, locate_current)
    bar.voice_changed.connect(change_voice)
    mouse_timer = QTimer(app)
    mouse_timer.timeout.connect(poll_mouse)
    mouse_timer.start(30)
    geometry_timer = QTimer(app)
    geometry_timer.timeout.connect(refresh_geometry)
    geometry_timer.start(700)
    diagnostic_timer = QTimer(app)
    diagnostic_timer.timeout.connect(lambda: heartbeat(request=ctx['epoch'], page=ctx['page'],
        busy=ctx['busy'], geometry=bool(ctx['geometry']),
        pending=bool(ctx['future'] and not ctx['future'].done()),
        state=holder['controller'].state.name if holder.get('controller') else 'IDLE'))
    diagnostic_timer.start(30000)
    screen = QGuiApplication.primaryScreen().availableGeometry()
    bar.adjustSize()
    bar.move(screen.center().x() - bar.width() // 2, screen.top() + 40)
    bar.show()
    tray = make_tray(app, bar)
    def cleanup():
        diagnostic_timer.stop()
        event('shutdown')
        ctx['epoch'] += 1
        mouse_timer.stop()
        geometry_timer.stop()
        if holder.get('controller'):
            holder['controller'].stop()
        workers.shutdown(wait=True, cancel_futures=True)
        if ctx['engine']:
            ctx['engine'].close()
        overlay.close()
        tray.hide()
    app.aboutToQuit.connect(cleanup)
    return app.exec()
