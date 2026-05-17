"""CJK 字体探测脚本（在沙箱 kernel 内 exec）。

由 loader.build_init_code 注入运行时变量：
- _HINT_FONT: 上次缓存的字体名（可能为空字符串）
- _HINT_PATH: 上次缓存的字体文件路径（可能为空字符串）

执行结束后向 stdout 打印一行：
    __MATHOI_CJK__:<font_name>|<font_path>
主进程解析这行回写本地缓存。

副作用：在 kernel namespace 写入两个变量供后续 mpl_setup 使用：
- _cjk_font: 字体名或 None
- _sans:     [字体名, 'DejaVu Sans'] 或 ['DejaVu Sans']
"""
# ruff: noqa
# 该文件作为字符串读取后 exec，不会被 Python 直接 import。
# fmt: off
import os as _os, subprocess as _sp
import matplotlib.font_manager as _fm

_CJK_PREFER = [
    'Noto Sans CJK SC','Noto Sans CJK TC','Noto Sans CJK JP',
    'Noto Sans SC','Noto Serif CJK SC',
    'Source Han Sans SC','Source Han Sans CN','Source Han Sans',
    'Microsoft YaHei','Microsoft YaHei UI',
    'SimHei','SimSun','FangSong','KaiTi',
    'WenQuanYi Micro Hei','WenQuanYi Zen Hei','WenQuanYi Bitmap Song',
    'PingFang SC','Heiti SC','STHeiti','STSong','STFangsong',
    'HarmonyOS Sans SC','OPPO Sans','MiSans',
    'Arial Unicode MS','Songti SC','Kaiti SC',
]


def _clear_findfont_cache():
    try:
        _fm.fontManager._findfont_cached.cache_clear()
    except Exception:
        pass


def _verify_font(name):
    "findfont() 验证：返回非 DejaVu 路径才算解析成功。"
    if not name:
        return False
    _clear_findfont_cache()
    import warnings as _warnings
    try:
        with _warnings.catch_warnings(record=True):
            _warnings.simplefilter('always')
            _path = _fm.findfont(name, fallback_to_default=False)
    except Exception:
        return False
    if not _path:
        return False
    return 'dejavu' not in _path.lower()


_cjk_font = None
_cjk_path = ''

# ── 1) cache hint：先尝试缓存的字体名 / 路径 ───────────────────────────────────
if _HINT_PATH and _os.path.isfile(_HINT_PATH):
    try:
        _fm.fontManager.addfont(_HINT_PATH)
        _clear_findfont_cache()
    except Exception:
        pass
if _HINT_FONT and _verify_font(_HINT_FONT):
    _cjk_font = _HINT_FONT
    _cjk_path = _HINT_PATH

# ── 2) 系统已注册：在 ttflist 中按优先级匹配 ─────────────────────────────────
if _cjk_font is None:
    _installed_names = {f.name for f in _fm.fontManager.ttflist}
    for _name in _CJK_PREFER:
        if _name in _installed_names and _verify_font(_name):
            _cjk_font = _name
            _hits = [f for f in _fm.fontManager.ttflist if f.name == _name]
            _cjk_path = _hits[0].fname if _hits else ''
            break

# ── 3) 文件扫描：常见目录搜索关键词 → addfont → 重试匹配 ──────────────────────
if _cjk_font is None:
    _CJK_FILE_KW = (
        'simhei','simsun','simfang','simkai','kaiti','fangsong',
        'yahei','msyh','msyhbd',
        'notocjk','notosanscjk','notoserif','noto_cjk',
        'notosans_cjk','notosanskjk',
        'wqy','wenquanyi',
        'sourcehan','source_han',
        'pingfang','heiti','stheiti','stsong','stfang',
        'arialuni','arialunicode',
        'harmonyos','opposans','misans','lxgw',
    )
    _FONT_DIRS = [
        r'C:\Windows\Fonts',
        '/usr/share/fonts','/usr/local/share/fonts',
        '/usr/share/fonts/truetype','/usr/share/fonts/opentype',
        '/usr/share/fonts/noto','/usr/share/fonts/wqy',
        _os.path.expanduser('~/.fonts'),
        _os.path.expanduser('~/.local/share/fonts'),
        '/Library/Fonts','/System/Library/Fonts',
        _os.path.expanduser('~/Library/Fonts'),
    ]
    _registered = []
    _names_before = {f.name for f in _fm.fontManager.ttflist}
    for _d in _FONT_DIRS:
        if not _os.path.isdir(_d):
            continue
        for _root, _dirs, _fnames in _os.walk(_d):
            for _fn in _fnames:
                if not _fn.lower().endswith(('.ttf','.ttc','.otf')):
                    continue
                if not any(_k in _fn.lower() for _k in _CJK_FILE_KW):
                    continue
                _fp = _os.path.join(_root, _fn)
                try:
                    _fm.fontManager.addfont(_fp)
                    _registered.append(_fp)
                except Exception:
                    pass
    if _registered:
        _clear_findfont_cache()
        _names_after = {f.name for f in _fm.fontManager.ttflist}
        # 优先 PREFER 列表
        for _name in _CJK_PREFER:
            if _name in _names_after and _verify_font(_name):
                _cjk_font = _name
                _hits = [f for f in _fm.fontManager.ttflist if f.name == _name]
                _cjk_path = _hits[0].fname if _hits else ''
                break
        # 兜底：取本次新增的任意可用字体
        if _cjk_font is None:
            for _name in (_names_after - _names_before):
                if _verify_font(_name):
                    _cjk_font = _name
                    _hits = [f for f in _fm.fontManager.ttflist if f.name == _name]
                    _cjk_path = _hits[0].fname if _hits else ''
                    break

# ── 4) fc-list：Linux 兜底（系统配置但 matplotlib 未识别）────────────────────
if _cjk_font is None:
    try:
        _fc_lines = _sp.check_output(
            ['fc-list', ':lang=zh'], timeout=8, stderr=_sp.DEVNULL
        ).decode('utf-8', errors='ignore').splitlines()
        for _fc_line in _fc_lines:
            _fc_path = _fc_line.split(':')[0].strip()
            if _fc_path.endswith(('.ttf','.ttc','.otf')) and _os.path.isfile(_fc_path):
                try:
                    _fm.fontManager.addfont(_fc_path)
                    _clear_findfont_cache()
                    _entry = [f for f in _fm.fontManager.ttflist if f.fname == _fc_path]
                    if _entry and _verify_font(_entry[0].name):
                        _cjk_font = _entry[0].name
                        _cjk_path = _fc_path
                        break
                except Exception:
                    pass
    except Exception:
        pass

# ── 5) 名称特征兜底 ──────────────────────────────────────────────────────────
if _cjk_font is None:
    _kws = ('CJK','Hei','Kai','Song','Ming','Gothic',
            'Yahei','SimSun','SimHei','Noto','WenQuan',
            'Source Han','PingFang','Heiti','Harmony','LXGW')
    for _f in _fm.fontManager.ttflist:
        if any(_k.lower() in _f.name.lower() for _k in _kws) and _verify_font(_f.name):
            _cjk_font = _f.name
            _cjk_path = _f.fname
            break

_sans = [_cjk_font, 'DejaVu Sans'] if _cjk_font else ['DejaVu Sans']
if _cjk_font:
    print(f'[Sandbox] CJK font: {_cjk_font}')
    print(f'__MATHOI_CJK__:{_cjk_font}|{_cjk_path}')
else:
    print('[Sandbox] WARNING: 未找到 CJK 字体，中文将显示为方框。')
    print('__MATHOI_CJK__:|')
