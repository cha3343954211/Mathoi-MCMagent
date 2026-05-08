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
_FONT_CACHE_TTL = 86400.0   # 24 小时
_FONT_CACHE_PATH = Path.home() / ".cache" / "mathoi" / "cjk_font_cache.json"


def _load_cjk_cache() -> Optional[str]:
    """读取本地缓存，有效期内返回字体名，否则返回 None。"""
    try:
        if not _FONT_CACHE_PATH.exists():
            return None
        data = json.loads(_FONT_CACHE_PATH.read_text(encoding="utf-8"))
        if time.time() - float(data.get("ts", 0)) < _FONT_CACHE_TTL:
            return data.get("font") or None
    except Exception:
        pass
    return None


def _save_cjk_cache(font_name: Optional[str]) -> None:
    """将扫描结果写入缓存文件（font=None 也缓存，避免下次再扫）。"""
    try:
        _FONT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _FONT_CACHE_PATH.write_text(
            json.dumps({"font": font_name or "", "ts": time.time()}),
            encoding="utf-8",
        )
    except Exception:
        pass


def _make_cjk_preamble() -> str:
    """生成 CJK 字体检测/注入代码。若缓存命中则跳过四重扫描，直接注入字体名。"""
    cached = _load_cjk_cache()
    if cached:
        logger.debug("CJK font cache hit: {}", cached)
        return (
            f"import matplotlib.font_manager as _fm\n"
            f"_cjk_font = {repr(cached)}\n"
            f"_sans = [_cjk_font, 'DejaVu Sans']\n"
            f"print(f'[Sandbox] CJK font (cached): {cached}')\n"
        )
    if cached == "":
        # 上次扫描也没找到，直接降级
        logger.debug("CJK font cache: no CJK font found on last scan")
        return (
            "_cjk_font = None\n"
            "_sans = ['DejaVu Sans']\n"
            "print('[Sandbox] CJK font (cached): none')\n"
        )
    # 无缓存或缓存失效 → 完整扫描，结果通过 _MATHOI_CJK_RESULT 变量传回
    logger.debug("CJK font cache miss, running full scan")
    return _FULL_CJK_SCAN_CODE


# 完整 CJK 字体四重扫描代码（模块级常量，避免重复构造）
# 扫描结束后打印特殊标记 __MATHOI_CJK__:<font_name>，供主进程回写缓存
_FULL_CJK_SCAN_CODE = r"""
import subprocess as _sp, os as _os
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
_installed_names = {f.name for f in _fm.fontManager.ttflist}
_cjk_font = next((f for f in _CJK_PREFER if f in _installed_names), None)

# 检测到候选名后立即清除 _findfont_cached（旧缓存可能包含 "not found" 条目）
if _cjk_font:
    try: _fm.fontManager._findfont_cached.cache_clear()
    except Exception: pass
    # 验证 findfont() 实际能解析（通过 logging 捕获不到 not-found，改用返回值判断）
    import warnings as _warnings
    with _warnings.catch_warnings(record=True):
        _warnings.simplefilter('always')
        _probe = _fm.findfont(_cjk_font)
    if not _probe or 'dejavu' in _probe.lower():
        # findfont 仍回退到 DejaVu Sans：说明系统缓存陈旧，让文件扫描兜底
        _cjk_font = None

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
        try: _fm.fontManager._findfont_cached.cache_clear()
        except Exception: pass
        _installed_names2 = {f.name for f in _fm.fontManager.ttflist}
        _cjk_font = next((f for f in _CJK_PREFER if f in _installed_names2), None)
        if _cjk_font is None:
            _cjk_font = next(iter(_installed_names2 - _installed_names), None)

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
                    _fc_entry = [e for e in _fm.fontManager.ttflist if e.fname == _fc_path]
                    if _fc_entry:
                        _cjk_font = _fc_entry[0].name; break
                except Exception: pass
    except Exception: pass

if _cjk_font is None:
    _kws = ('CJK','Hei','Kai','Song','Ming','Gothic',
            'Yahei','SimSun','SimHei','Noto','WenQuan',
            'Source Han','PingFang','Heiti','Harmony','LXGW')
    _candidates = [f.name for f in _fm.fontManager.ttflist
                   if any(k.lower() in f.name.lower() for k in _kws)]
    _cjk_font = _candidates[0] if _candidates else None

_sans = [_cjk_font, 'DejaVu Sans'] if _cjk_font else ['DejaVu Sans']
if _cjk_font:
    print(f'[Sandbox] CJK font: {_cjk_font}')
    print(f'__MATHOI_CJK__:{_cjk_font}')
else:
    print('[Sandbox] WARNING: 未找到 CJK 字体，中文将显示为方框。')
    print('__MATHOI_CJK__:')
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
                # 解析缓存标记（仅完整扫描时出现），回写主进程缓存文件
                if "__MATHOI_CJK__" in _init_result:
                    for _line in _init_result.splitlines():
                        if _line.startswith("__MATHOI_CJK__:"):
                            _detected = _line.split(":", 1)[1].strip()
                            _save_cjk_cache(_detected or None)
                            break
                logger.info("Sandbox started (attempt {}) | task={} cwd={}",
                            attempt, self.task_id, self.work_dir)
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
