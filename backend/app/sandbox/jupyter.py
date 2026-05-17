"""本地 Jupyter Kernel 沙箱。

- 每个任务持有独立 KernelManager，状态隔离
- 工作目录指向任务工作区，所有产物落地磁盘
- 执行结果按 stream / display / execute_result / error 归类
- 执行同时通过事件总线推送，支撑前端实时显示
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import re as _re

from jupyter_client.manager import AsyncKernelManager

from ..core.config import get_settings
from ..core.events import EventType, emit
from ..core.logging import logger
from .notebook import NotebookRecorder

# ---------- CJK 字体缓存 ----------
# 缓存仅作 "提示"：每次仍在 kernel 内验证字体能被 findfont() 真正解析；
# 验证失败立即降级到完整扫描，确保不会出现"缓存生效但中文渲染成豆腐方块"的情况。
_FONT_CACHE_TTL = 86400.0   # 24 小时
_FONT_CACHE_PATH = Path.home() / ".cache" / "mathoi" / "cjk_font_cache.json"


def _load_cjk_cache_hint() -> dict:
    """读取本地缓存，返回 {font, path}（空值表示未知）。"""
    try:
        if _FONT_CACHE_PATH.exists():
            data = json.loads(_FONT_CACHE_PATH.read_text(encoding="utf-8"))
            if time.time() - float(data.get("ts", 0)) < _FONT_CACHE_TTL:
                return {
                    "font": str(data.get("font") or ""),
                    "path": str(data.get("path") or ""),
                }
    except Exception:
        pass
    return {"font": "", "path": ""}


def _save_cjk_cache(font_name: Optional[str], font_path: Optional[str] = None) -> None:
    """将扫描结果写入缓存文件（font="" 也缓存，避免下次再扫）。"""
    try:
        _FONT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _FONT_CACHE_PATH.write_text(
            json.dumps({
                "font": font_name or "",
                "path": font_path or "",
                "ts": time.time(),
            }),
            encoding="utf-8",
        )
    except Exception:
        pass


def _make_cjk_preamble() -> str:
    """生成 CJK 字体检测/注入代码。

    - 把缓存中的字体名/路径作为 hint 注入 kernel 代码；
    - kernel 内仍执行完整的"hint 验证 → 系统已注册 → 文件扫描 → fc-list"四级流程；
    - 任何路径下都通过 findfont() 验证可解析，避免假阳性。
    """
    hint = _load_cjk_cache_hint()
    return _CJK_DETECT_TEMPLATE.format(
        hint_font=repr(hint.get("font") or ""),
        hint_path=repr(hint.get("path") or ""),
    )


# 完整 CJK 字体探测代码（cache hint 优先 → 完整扫描兜底）
# 扫描结束后打印特殊标记 __MATHOI_CJK__:<font_name>|<font_path>，供主进程回写缓存
_CJK_DETECT_TEMPLATE = r"""
import os as _os, subprocess as _sp
import matplotlib.font_manager as _fm

_HINT_FONT = {hint_font}
_HINT_PATH = {hint_path}

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
    try: _fm.fontManager._findfont_cached.cache_clear()
    except Exception: pass


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
    except Exception: pass
if _HINT_FONT and _verify_font(_HINT_FONT):
    _cjk_font = _HINT_FONT
    _cjk_path = _HINT_PATH

# ── 2) 系统已注册：在 ttflist 中按优先级匹配 ─────────────────────────────────
if _cjk_font is None:
    _installed_names = {{f.name for f in _fm.fontManager.ttflist}}
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
    _names_before = {{f.name for f in _fm.fontManager.ttflist}}
    for _d in _FONT_DIRS:
        if not _os.path.isdir(_d): continue
        for _root, _dirs, _fnames in _os.walk(_d):
            for _fn in _fnames:
                if not _fn.lower().endswith(('.ttf','.ttc','.otf')): continue
                if not any(_k in _fn.lower() for _k in _CJK_FILE_KW): continue
                _fp = _os.path.join(_root, _fn)
                try:
                    _fm.fontManager.addfont(_fp); _registered.append(_fp)
                except Exception: pass
    if _registered:
        _clear_findfont_cache()
        _names_after = {{f.name for f in _fm.fontManager.ttflist}}
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
                        _cjk_font = _entry[0].name; _cjk_path = _fc_path; break
                except Exception: pass
    except Exception: pass

# ── 5) 名称特征兜底 ──────────────────────────────────────────────────────────
if _cjk_font is None:
    _kws = ('CJK','Hei','Kai','Song','Ming','Gothic',
            'Yahei','SimSun','SimHei','Noto','WenQuan',
            'Source Han','PingFang','Heiti','Harmony','LXGW')
    for _f in _fm.fontManager.ttflist:
        if any(_k.lower() in _f.name.lower() for _k in _kws) and _verify_font(_f.name):
            _cjk_font = _f.name; _cjk_path = _f.fname; break

_sans = [_cjk_font, 'DejaVu Sans'] if _cjk_font else ['DejaVu Sans']
if _cjk_font:
    print(f'[Sandbox] CJK font: {{_cjk_font}}')
    print(f'__MATHOI_CJK__:{{_cjk_font}}|{{_cjk_path}}')
else:
    print('[Sandbox] WARNING: 未找到 CJK 字体，中文将显示为方框。')
    print('__MATHOI_CJK__:|')
"""

# 清理 ANSI 转义码（traceback 中 colorama 输出会干扰 LLM 理解）
_ANSI_RE = _re.compile(r"\x1b\[[0-9;]*[mGKHF]")


@dataclass
class ExecResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    text_result: str = ""               # text/plain 形式的最终结果
    images: list[str] = field(default_factory=list)  # 本地保存路径
    error: Optional[str] = None
    traceback: list[str] = field(default_factory=list)

    def to_llm_feedback(self, max_chars: int = 4000) -> str:
        """压缩成给 LLM 的反馈文本。"""
        parts: list[str] = []
        if self.stdout:
            parts.append(f"[stdout]\n{self.stdout}")
        if self.text_result:
            parts.append(f"[result]\n{self.text_result}")
        if self.images:
            parts.append(f"[images saved]\n" + "\n".join(self.images))
        if self.stderr:
            parts.append(f"[stderr]\n{self.stderr}")
        if self.error:
            parts.append(f"[error]\n{self.error}\n" + "\n".join(self.traceback[-10:]))
        text = "\n\n".join(parts) if parts else "[no output]"
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... (truncated, total {len(text)} chars)"
        return text


_KERNEL_START_TIMEOUT = 90   # wait_for_ready 超时（秒）
_KERNEL_START_RETRIES = 3    # 最大重试次数
_KERNEL_RETRY_DELAY  = 3     # 重试间隔（秒）
_KERNEL_HEALTH_INTERVAL = 60.0  # 后台健康检查间隔（秒）
_KERNEL_HEALTH_TIMEOUT = 5.0    # is_alive 单次超时（秒）
_KERNEL_HEALTH_MAX_FAILS = 2    # 连续失败多少次后重启


class JupyterSandbox:
    """异步本地 Kernel 沙箱。"""

    def __init__(self, task_id: str, work_dir: Path) -> None:
        self.task_id = task_id
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._km: Optional[AsyncKernelManager] = None
        self._kc: Any = None
        self._image_seq = 0
        self._lock = asyncio.Lock()
        self._interrupt_requested: bool = False  # 用户发起的中断标志
        self._notebook: NotebookRecorder = NotebookRecorder()
        self._notebook_path: Path = self.work_dir / "notebook.ipynb"
        self._health_task: Optional[asyncio.Task] = None

    async def _check_paused(self) -> None:
        """若任务被暂停则阻塞，直到恢复或取消。不中断内核，仅暂停消息处理。"""
        try:
            from ..tasks.manager import task_manager  # 懒导入，避免循环依赖
            await task_manager.wait_if_paused(self.task_id)
        except Exception:
            pass

    def add_phase_marker(self, label: str) -> None:
        """在 notebook 中插入分节标题 cell（由 orchestrator 在每个阶段开始时调用）。"""
        self._notebook.add_phase_marker(label)
        self._notebook.flush(self._notebook_path)

    async def interrupt(self) -> None:
        """中断当前正在执行的代码（等价于 Jupyter 的 '■ Stop' 按钮）。"""
        self._interrupt_requested = True
        if self._km:
            try:
                await self._km.interrupt_kernel()
                logger.info("Kernel interrupt sent | task={}", self.task_id)
            except Exception as e:
                logger.warning("Kernel interrupt failed: {} | task={}", e, self.task_id)

    async def start(self) -> None:
        settings = get_settings()
        last_exc: Exception = RuntimeError("unknown")
        for attempt in range(1, _KERNEL_START_RETRIES + 1):
            try:
                # 每次尝试前彻底清理上一次的残留
                await self._cleanup()
                self._km = AsyncKernelManager(kernel_name=settings.sandbox_kernel)
                await self._km.start_kernel(cwd=str(self.work_dir))
                self._kc = self._km.client()
                self._kc.start_channels()
                await self._kc.wait_for_ready(timeout=_KERNEL_START_TIMEOUT)
                # 预热：注入工作目录 + 学术 matplotlib 全局配置
                # CJK 字体检测（缓存命中时走快路径，跳过完整扫描）
                _cjk_preamble = _make_cjk_preamble()
                _init_result = await self._silent_exec(
                    "import os, sys, json, gc\n"
                    "import warnings, logging\n"
                    "warnings.filterwarnings('ignore')\n"
                    # matplotlib.font_manager 通过 logging 而非 warnings 发出告警，
                    # 单独压制，防止 'findfont: Font family ... not found' 污染 stderr
                    "logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)\n"
                    "import matplotlib\n"
                    "matplotlib.use('Agg')\n"
                    "import matplotlib.pyplot as plt\n"
                    f"os.chdir(r'{self.work_dir}')\n"
                    "\n"
                    "# ── CJK 字体探测（首次完整扫描，后续缓存命中走快路径）──\n"
                    + _cjk_preamble + "\n"
                    "\n"
                    "# ── 标准学术配色（所有图表统一使用）────────────────────\n"
                    "COLORS = {\n"
                    "    'primary':   '#2E5B88',\n"
                    "    'secondary': '#E85D4C',\n"
                    "    'tertiary':  '#4A9B7F',\n"
                    "    'warning':   '#F0A500',\n"
                    "    'neutral':   '#6B6B6B',\n"
                    "    'light':     '#B8D4E8',\n"
                    "    'bg':        '#F7F7F7',\n"
                    "}\n"
                    "PALETTE = [COLORS['primary'], COLORS['secondary'], COLORS['tertiary'],\n"
                    "           COLORS['warning'], COLORS['neutral'], COLORS['light']]\n"
                    "\n"
                    "# ── 图幅尺寸常量 ────────────────────────────────────────\n"
                    "FIG_SINGLE = (6, 4.5)   # 单图\n"
                    "FIG_DOUBLE = (11, 4.5)  # 左右两图\n"
                    "FIG_WIDE   = (9, 3.5)   # 宽条形图\n"
                    "FIG_SQUARE = (6, 6)     # 热力图/散点图\n"
                    "FIG_TALL   = (5, 7)     # 竖向多子图\n"
                    "\n"
                    "# ── 全局 rcParams（学术论文风格，每个任务只设一次）──────\n"
                    "plt.rcParams.update({\n"
                    "    # 字体\n"
                    "    'font.family':          'sans-serif',\n"
                    "    'font.sans-serif':      _sans,\n"
                    "    'font.size':            11,\n"
                    "    'axes.unicode_minus':   False,\n"
                    "    # 标题 / 标签\n"
                    "    'axes.titlesize':       12,\n"
                    "    'axes.titleweight':     'bold',\n"
                    "    'axes.titlepad':        8,\n"
                    "    'axes.labelsize':       11,\n"
                    "    'axes.labelpad':        5,\n"
                    "    # 轴线\n"
                    "    'axes.linewidth':       1.0,\n"
                    "    'axes.spines.top':      False,\n"
                    "    'axes.spines.right':    False,\n"
                    "    'axes.edgecolor':       '#444444',\n"
                    "    # 刻度\n"
                    "    'xtick.labelsize':      10,\n"
                    "    'ytick.labelsize':      10,\n"
                    "    'xtick.major.size':     4,\n"
                    "    'ytick.major.size':     4,\n"
                    "    'xtick.minor.visible':  False,\n"
                    "    'ytick.minor.visible':  False,\n"
                    "    # 图例\n"
                    "    'legend.fontsize':      10,\n"
                    "    'legend.frameon':       False,\n"
                    "    'legend.loc':           'best',\n"
                    "    # 网格\n"
                    "    'axes.grid':            True,\n"
                    "    'grid.color':           '#DDDDDD',\n"
                    "    'grid.linewidth':       0.6,\n"
                    "    'grid.linestyle':       '--',\n"
                    "    'axes.axisbelow':       True,\n"
                    "    # 线条 / 标记\n"
                    "    'lines.linewidth':      1.8,\n"
                    "    'lines.markersize':     5,\n"
                    "    'patch.linewidth':      0.8,\n"
                    "    # 颜色循环\n"
                    "    'axes.prop_cycle':      plt.cycler(color=PALETTE),\n"
                    "    # 背景\n"
                    "    'figure.facecolor':     'white',\n"
                    "    'axes.facecolor':       'white',\n"
                    "    # 分辨率\n"
                    "    'figure.dpi':           120,\n"
                    "    'savefig.dpi':          300,\n"
                    "    'savefig.bbox':         'tight',\n"
                    "    'savefig.pad_inches':   0.15,\n"
                    "    'savefig.facecolor':    'white',\n"
                    "})\n"
                    "\n"
                    "# ── seaborn 集成（若已安装则对齐主题）───────────────────\n"
                    "try:\n"
                    "    import seaborn as sns\n"
                    "    sns.set_theme(style='ticks', palette=PALETTE, font=_sans[0] if _sans else 'sans-serif')\n"
                    "    sns.set_context('paper', font_scale=1.1)\n"
                    "    # set_theme 会重置部分 rcParams，需补丁回覆\n"
                    "    plt.rcParams['axes.unicode_minus'] = False\n"
                    "    plt.rcParams['font.sans-serif'] = _sans\n"
                    "    plt.rcParams['savefig.dpi'] = 300\n"
                    "    plt.rcParams['savefig.facecolor'] = 'white'\n"
                    "    plt.rcParams['axes.grid'] = True\n"
                    "    plt.rcParams['grid.color'] = '#DDDDDD'\n"
                    "    plt.rcParams['axes.prop_cycle'] = plt.cycler(color=PALETTE)\n"
                    "    print('[Sandbox] seaborn integrated')\n"
                    "except ImportError:\n"
                    "    pass\n"
                    "\n"
                    "# ── 便捷辅助函数 ────────────────────────────────────────\n"
                    "def std_fig(size=FIG_SINGLE):\n"
                    "    '''创建标准学术图幅，返回 (fig, ax)。'''\n"
                    "    return plt.subplots(figsize=size)\n"
                    "\n"
                    "def save_fig(fname, fig=None):\n"
                    "    '''保存图片到工作区，自动设置 dpi/bbox。'''\n"
                    "    (fig or plt.gcf()).savefig(fname, dpi=300, bbox_inches='tight',\n"
                    "                              facecolor='white')\n"
                    "    plt.close('all')\n"
                    "    print(f'[saved] {fname}')\n"
                    "\n"
                    "print(f'Sandbox ready | cwd: {os.getcwd()} | font: {_cjk_font or \"fallback\"}')\n"
                )
                # 解析缓存标记，回写主进程缓存文件（格式：__MATHOI_CJK__:<font>|<path>）
                if "__MATHOI_CJK__" in _init_result:
                    for _line in _init_result.splitlines():
                        if _line.startswith("__MATHOI_CJK__:"):
                            _payload = _line.split(":", 1)[1].strip()
                            if "|" in _payload:
                                _f, _p = _payload.split("|", 1)
                            else:
                                _f, _p = _payload, ""
                            _save_cjk_cache(_f or None, _p or None)
                            break
                logger.info("Sandbox started (attempt {}) | task={} cwd={}",
                            attempt, self.task_id, self.work_dir)
                self._start_health_worker()
                return  # 成功
            except Exception as e:
                last_exc = e
                logger.warning("Sandbox start attempt {}/{} failed: {}", attempt, _KERNEL_START_RETRIES, e)
                await self._cleanup()
                if attempt < _KERNEL_START_RETRIES:
                    await asyncio.sleep(_KERNEL_RETRY_DELAY)
        raise RuntimeError(
            f"Kernel 启动失败（已重试 {_KERNEL_START_RETRIES} 次）：{last_exc}\n"
            "请检查 ipykernel 是否已安装（pip install ipykernel）"
        ) from last_exc

    async def _cleanup(self) -> None:
        """彻底关闭当前 km/kc，忽略所有异常。"""
        current = asyncio.current_task()
        # 若由健康检查协程自身触发重启，不能 cancel/await 自己；否则会自等待死锁。
        if self._health_task and not self._health_task.done() and self._health_task is not current:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._health_task = None
        # 先摘引用，再操作，避免重入
        kc, km = self._kc, self._km
        self._kc = None
        self._km = None
        try:
            if kc:
                kc.stop_channels()
        except Exception:
            pass
        try:
            if km:
                await km.shutdown_kernel(now=True)
        except Exception:
            pass

    async def stop(self) -> None:
        await self._cleanup()

    def _start_health_worker(self) -> None:
        """启动后台 Kernel 健康检查。"""
        if self._health_task is None or self._health_task.done():
            self._health_task = asyncio.create_task(self._health_worker())

    async def _health_worker(self) -> None:
        """定期检测 Kernel 存活；空闲时连续失败自动重启。"""
        fails = 0
        while True:
            try:
                await asyncio.sleep(_KERNEL_HEALTH_INTERVAL)
                await self._check_paused()
                # 正在执行代码时跳过健康检查，避免与 execute 的 ZMQ 消息读取互相干扰
                if self._lock.locked():
                    continue
                km = self._km
                if km is None:
                    continue
                alive = await asyncio.wait_for(km.is_alive(), timeout=_KERNEL_HEALTH_TIMEOUT)
                if alive:
                    fails = 0
                    continue
                fails += 1
                logger.warning("Kernel health check failed {}/{} | task={}",
                               fails, _KERNEL_HEALTH_MAX_FAILS, self.task_id)
                if fails >= _KERNEL_HEALTH_MAX_FAILS:
                    async with self._lock:
                        logger.warning("Kernel unhealthy, restarting | task={}", self.task_id)
                        await self._restart_kernel()
                    fails = 0
            except asyncio.CancelledError:
                return
            except Exception as e:
                fails += 1
                logger.warning("Kernel health check error {}/{}: {} | task={}",
                               fails, _KERNEL_HEALTH_MAX_FAILS, e, self.task_id)
                if fails >= _KERNEL_HEALTH_MAX_FAILS and not self._lock.locked():
                    try:
                        async with self._lock:
                            await self._restart_kernel()
                        fails = 0
                    except Exception as re:
                        logger.warning("Kernel health restart failed: {} | task={}", re, self.task_id)

    async def _restart_kernel(self) -> None:
        """执行中发现内核崩溃时，原地重启。"""
        logger.warning("Kernel dead, restarting | task={}", self.task_id)
        await self._cleanup()
        await self.start()

    async def __aenter__(self) -> "JupyterSandbox":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()

    async def _silent_exec(self, code: str) -> str:
        """内部预热执行，不发事件。预热失败只记 warning，不中断启动。
        返回执行期间的 stdout 输出（供调用方解析特殊标记）。
        """
        if self._kc is None:
            return ""
        stdout_buf: list[str] = []
        try:
            msg_id = self._kc.execute(code)
            deadline = asyncio.get_running_loop().time() + 30
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    logger.warning("sandbox warmup timeout | task={}", self.task_id)
                    break
                try:
                    msg = await asyncio.wait_for(self._kc.get_iopub_msg(), timeout=min(remaining, 10))
                except asyncio.TimeoutError:
                    break
                msg_type = msg.get("msg_type", "")
                parent_id = msg.get("parent_header", {}).get("msg_id")
                if parent_id == msg_id:
                    if msg_type == "stream":
                        stdout_buf.append(msg["content"].get("text", ""))
                    elif msg_type == "status" and msg["content"].get("execution_state") == "idle":
                        break
        except Exception as e:
            logger.warning("sandbox warmup error (non-fatal): {} | task={}", e, self.task_id)
        return "".join(stdout_buf)

    async def execute(self, code: str, *, timeout: Optional[int] = None, emit_events: bool = True) -> ExecResult:
        """执行一段代码，返回结构化结果。执行前检测内核存活，崩溃则自动重启。"""
        # 执行前检查：若任务已被暂停，等待恢复后再发起新 cell
        await self._check_paused()
        async with self._lock:
            # 检测内核是否还活着，死亡则重启
            if self._km is not None:
                try:
                    # 加 5s 超时：ZMQ socket 活性检查有时卡死
                    alive = await asyncio.wait_for(self._km.is_alive(), timeout=5.0)
                    if not alive:
                        logger.warning("Kernel not alive before execute, restarting | task={}", self.task_id)
                        await self._restart_kernel()
                except (asyncio.TimeoutError, Exception) as e:
                    logger.warning("Kernel liveness check failed ({}), restarting | task={}", e, self.task_id)
                    await self._restart_kernel()
            return await self._execute_impl(code, timeout=timeout, emit_events=emit_events)

    async def _execute_impl(self, code: str, *, timeout: Optional[int], emit_events: bool) -> ExecResult:
        assert self._kc is not None
        settings = get_settings()
        timeout = timeout or settings.sandbox_timeout

        result = ExecResult(success=True)
        # ── Notebook 录制：记录代码 cell（在发送给 kernel 之前）────────────
        self._notebook.record_cell(code)
        msg_id = self._kc.execute(code)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                result.success = False
                result.error = f"Execution timeout after {timeout}s"
                try:
                    if self._km:
                        await self._km.interrupt_kernel()
                except Exception:
                    pass
                break
            # 暂停检查：每轮循环前检查，确保 pause 能在 1s 内生效
            await self._check_paused()
            try:
                # 封顶 1s，保证暂停请求能在下一轮被及时响应
                msg = await asyncio.wait_for(
                    self._kc.get_iopub_msg(),
                    timeout=min(remaining, 1.0),
                )
            except asyncio.TimeoutError:
                continue
            if msg.get("parent_header", {}).get("msg_id") != msg_id:
                continue

            mtype = msg["msg_type"]
            content = msg["content"]

            if mtype == "stream":
                text = content.get("text", "")
                stream_name = content.get("name", "stdout")
                if stream_name == "stderr":
                    result.stderr += text
                    self._notebook.record_stderr(text)
                    if emit_events:
                        await emit(EventType.SANDBOX_STDERR, self.task_id, text=text)
                else:
                    result.stdout += text
                    self._notebook.record_stdout(text)
                    if emit_events:
                        await emit(EventType.SANDBOX_STDOUT, self.task_id, text=text)

            elif mtype in ("display_data", "execute_result"):
                data = content.get("data", {})
                if "image/png" in data:
                    img_path = self._save_image(data["image/png"], "png")
                    result.images.append(str(img_path))
                    self._notebook.record_image(data["image/png"], "image/png")
                    if emit_events:
                        await emit(EventType.SANDBOX_DISPLAY, self.task_id, image=str(img_path), kind="png")
                elif "image/jpeg" in data:
                    img_path = self._save_image(data["image/jpeg"], "jpg")
                    result.images.append(str(img_path))
                    self._notebook.record_image(data["image/jpeg"], "image/jpeg")
                    if emit_events:
                        await emit(EventType.SANDBOX_DISPLAY, self.task_id, image=str(img_path), kind="jpg")
                if "text/plain" in data and mtype == "execute_result":
                    result.text_result = data["text/plain"]
                    self._notebook.record_result(data["text/plain"])
                    if emit_events:
                        await emit(EventType.SANDBOX_RESULT, self.task_id, text=result.text_result)

            elif mtype == "error":
                result.success = False
                raw_err = content.get("ename", "") + ": " + content.get("evalue", "")
                raw_tb = content.get("traceback", [])
                # 清理 ANSI 转义码，避免 LLM 收到乱码
                result.error = _ANSI_RE.sub("", raw_err)
                result.traceback = [_ANSI_RE.sub("", line) for line in raw_tb]
                self._notebook.record_error(
                    content.get("ename", "Error"),
                    content.get("evalue", ""),
                    raw_tb,
                )
                if emit_events:
                    await emit(
                        EventType.SANDBOX_STDERR,
                        self.task_id,
                        text=result.error + "\n" + "\n".join(result.traceback),
                    )

            elif mtype == "status" and content.get("execution_state") == "idle":
                # cell 执行完毕：刷新 notebook（增量覆盖，保持文件始终可用）
                self._notebook.flush(self._notebook_path)
                break

        # 清除中断标志
        if self._interrupt_requested:
            self._interrupt_requested = False
            if not result.error:
                result.success = False
                result.error = "KeyboardInterrupt: 任务被用户中断"
        return result

    def _save_image(self, b64: str, ext: str) -> Path:
        self._image_seq += 1
        path = self.work_dir / f"figure_{self._image_seq:03d}.{ext}"
        path.write_bytes(base64.b64decode(b64))
        return path
