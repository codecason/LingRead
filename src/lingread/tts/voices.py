"""Voice choices verified against the service, and per-user preferences."""
import json
import os
from pathlib import Path
import tempfile

VOICE_OPTIONS = (
    ('auto', '自动（随语言）'),
    ('zh-CN-XiaoxiaoNeural', '晓晓 · 中文女声'),
    ('zh-CN-XiaoyiNeural', '晓伊 · 中文女声'),
    ('zh-CN-YunxiNeural', '云希 · 中文男声'),
    ('zh-CN-YunjianNeural', '云健 · 中文男声'),
    ('en-US-AvaNeural', 'Ava · 英文女声'),
    ('en-US-AndrewNeural', 'Andrew · 英文男声'),
)

def settings_path():
    return Path(os.environ.get('LINGREAD_DATA_DIR', str(Path.home()/'.lingread'))) / 'preferences.json'

def _read(path):
    try:
        data=json.loads(path.read_text('utf-8'))
        return data if isinstance(data,dict) else {}
    except (OSError,ValueError):
        return {}

def load_voice(path=None):
    value=_read(Path(path) if path is not None else settings_path()).get('voice','auto')
    return value if isinstance(value,str) and value in dict(VOICE_OPTIONS) else 'auto'

def save_voice(voice,path=None):
    if voice not in dict(VOICE_OPTIONS):
        raise ValueError('不支持的音色')
    path=Path(path) if path is not None else settings_path()
    data=_read(path)
    data['voice']=voice
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                                         prefix='preferences-',suffix='.tmp',delete=False) as f:
            temporary=Path(f.name)
            json.dump(data,f,ensure_ascii=False,indent=2)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

def resolve_voice(selected,language):
    if selected and selected!='auto':
        return selected
    return 'zh-CN-XiaoxiaoNeural' if language=='zh' else 'en-US-AndrewNeural'
