from pathlib import Path


class DocumentBinding:
    """A user-confirmed path is valid only for the current Foxit context."""
    def __init__(self):
        self.key = None
        self.path = None

    def observe(self, win):
        key = (win.hwnd, win.pid, win.title) if win else None
        if key != self.key:
            self.key, self.path = key, None

    def resolve(self, win, automatic, choose):
        self.observe(win)
        if self.path is not None and self.path.is_file():
            return self.path, False
        self.path = None
        if automatic and Path(automatic).is_file():
            return Path(automatic), False
        selected = choose()
        if not selected:
            return None, False
        path = Path(selected)
        if not path.is_file() or path.suffix.lower() != '.pdf':
            raise ValueError('请选择存在的 PDF 文件')
        self.path = path
        return path, True
