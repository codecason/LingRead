"""Bounded, metadata-only diagnostics. Never log document or clipboard text."""
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import threading
import time
import uuid
from datetime import datetime, timezone
from . import __version__

_log = logging.getLogger('lingread.diagnostic')
_log.propagate = False
_log.addHandler(logging.NullHandler())
_start = time.monotonic()
_run = uuid.uuid4().hex[:12]
_recent = {}
_lock = threading.RLock()
_fields = set('request session page sentence offset chars reason error_type ms attempt voice rate bytes boundaries hwnd foreground anchor geometry point matched state rss_mb threads handles busy pending elapsed_s'.split())

def configure(folder):
    close()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    # Separate files prevent two running releases from corrupting rotation.
    path = folder / f'diagnostic-{os.getpid()}-{_run}.jsonl'
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(message)s'))
    _log.addHandler(handler)
    _log.setLevel(logging.INFO)
    _recent.clear()
    event('startup')
    return path

def close():
    for handler in list(_log.handlers):
        _log.removeHandler(handler)
        handler.close()
    _log.addHandler(logging.NullHandler())

def event(name, throttle=0, **fields):
    try:
        data = {k:v for k,v in fields.items() if k in _fields and isinstance(v, (str,int,float,bool,type(None)))}
        now = time.monotonic()
        with _lock:
            if throttle:
                signature = (data.get('reason'), data.get('page'))
                old = _recent.get(name)
                if old and old[0] == signature and now-old[1] < throttle:
                    return
                _recent[name] = (signature, now)
            _log.info(json.dumps(dict(time=datetime.now(timezone.utc).isoformat(),
                version=__version__, pid=os.getpid(), run=_run,
                uptime_s=round(now-_start, 3), thread=threading.current_thread().name,
                event=name, **data), ensure_ascii=False))
    except Exception:
        pass  # A failed diagnostic write must not stop reading.

def heartbeat(**fields):
    try:
        import psutil
        proc = psutil.Process()
        event('heartbeat', rss_mb=round(proc.memory_info().rss/1048576,1),
              threads=proc.num_threads(), handles=proc.num_handles(), **fields)
    except Exception as exc:
        event('heartbeat_failed', error_type=type(exc).__name__)
