"""聆阅 LingRead 打包入口（PyInstaller），带崩溃日志。"""
import os
import sys
import traceback
import logging
from logging.handlers import RotatingFileHandler

LOG_DIR = os.environ.get('LINGREAD_DATA_DIR', os.path.join(os.path.expanduser("~"), ".lingread"))
os.makedirs(LOG_DIR, exist_ok=True)
LOG = os.path.join(LOG_DIR, "crash.log")
logging.basicConfig(level=logging.INFO,
    handlers=[RotatingFileHandler(os.path.join(LOG_DIR, 'runtime.log'),
                                  maxBytes=1_000_000, backupCount=2, encoding='utf-8')],
    format='%(asctime)s %(levelname)s %(name)s %(message)s')

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
from lingread.diagnostics import configure, event
configure(LOG_DIR)


def _excepthook(t, v, tb):
    event('uncaught_exception', error_type=t.__name__)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("".join(traceback.format_exception(t, v, tb)) + "\n")


sys.excepthook = _excepthook

try:
    from lingread.__main__ import main

    if __name__ == "__main__":
        sys.exit(main())
except Exception:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(traceback.format_exc() + "\n")
    raise
