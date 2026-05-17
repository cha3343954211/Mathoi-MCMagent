"""matplotlib / seaborn 全局风格 + save_fig / std_fig 辅助。

依赖前置脚本（cjk_detect.py）已设置变量 _sans / _cjk_font。
loader 在拼接时确保顺序：cjk_detect → mpl_setup。

副作用：
- 写入 plt.rcParams 全局风格
- 注册 std_fig / save_fig 函数到 kernel namespace
- 配置 seaborn（若已安装）

save_fig 行为：
- 保存图片到工作区当前目录（cwd 已被 chdir 到 work_dir）
- 同时向 figures.jsonl 追加一条元数据：{file, scope, caption, ts}
- 关闭所有 figure 释放内存
"""
# ruff: noqa
# fmt: off

# ── 标准学术配色（所有图表统一使用）────────────────────────────
COLORS = {
    'primary':   '#2E5B88',
    'secondary': '#E85D4C',
    'tertiary':  '#4A9B7F',
    'warning':   '#F0A500',
    'neutral':   '#6B6B6B',
    'light':     '#B8D4E8',
    'bg':        '#F7F7F7',
}
PALETTE = [COLORS['primary'], COLORS['secondary'], COLORS['tertiary'],
           COLORS['warning'], COLORS['neutral'], COLORS['light']]

# ── 图幅尺寸常量 ────────────────────────────────────────────────
FIG_SINGLE = (6, 4.5)   # 单图
FIG_DOUBLE = (11, 4.5)  # 左右两图
FIG_WIDE   = (9, 3.5)   # 宽条形图
FIG_SQUARE = (6, 6)     # 热力图/散点图
FIG_TALL   = (5, 7)     # 竖向多子图

# ── 全局 rcParams（学术论文风格，每个任务只设一次）──────────────
plt.rcParams.update({
    # 字体
    'font.family':          'sans-serif',
    'font.sans-serif':      _sans,
    'font.size':            11,
    'axes.unicode_minus':   False,
    # 标题 / 标签
    'axes.titlesize':       12,
    'axes.titleweight':     'bold',
    'axes.titlepad':        8,
    'axes.labelsize':       11,
    'axes.labelpad':        5,
    # 轴线
    'axes.linewidth':       1.0,
    'axes.spines.top':      False,
    'axes.spines.right':    False,
    'axes.edgecolor':       '#444444',
    # 刻度
    'xtick.labelsize':      10,
    'ytick.labelsize':      10,
    'xtick.major.size':     4,
    'ytick.major.size':     4,
    'xtick.minor.visible':  False,
    'ytick.minor.visible':  False,
    # 图例
    'legend.fontsize':      10,
    'legend.frameon':       False,
    'legend.loc':           'best',
    # 网格
    'axes.grid':            True,
    'grid.color':           '#DDDDDD',
    'grid.linewidth':       0.6,
    'grid.linestyle':       '--',
    'axes.axisbelow':       True,
    # 线条 / 标记
    'lines.linewidth':      1.8,
    'lines.markersize':     5,
    'patch.linewidth':      0.8,
    # 颜色循环
    'axes.prop_cycle':      plt.cycler(color=PALETTE),
    # 背景
    'figure.facecolor':     'white',
    'axes.facecolor':       'white',
    # 分辨率
    'figure.dpi':           120,
    'savefig.dpi':          300,
    'savefig.bbox':         'tight',
    'savefig.pad_inches':   0.15,
    'savefig.facecolor':    'white',
})

# ── seaborn 集成（若已安装则对齐主题）────────────────────────────
try:
    import seaborn as sns
    sns.set_theme(style='ticks', palette=PALETTE,
                  font=_sans[0] if _sans else 'sans-serif')
    sns.set_context('paper', font_scale=1.1)
    # set_theme 会重置部分 rcParams，需补丁回覆
    plt.rcParams['axes.unicode_minus'] = False
    plt.rcParams['font.sans-serif'] = _sans
    plt.rcParams['savefig.dpi'] = 300
    plt.rcParams['savefig.facecolor'] = 'white'
    plt.rcParams['axes.grid'] = True
    plt.rcParams['grid.color'] = '#DDDDDD'
    plt.rcParams['axes.prop_cycle'] = plt.cycler(color=PALETTE)
    print('[Sandbox] seaborn integrated')
except ImportError:
    pass

# ── 便捷辅助函数 ────────────────────────────────────────────────
import json as _json
import time as _time

def std_fig(size=FIG_SINGLE):
    "创建标准学术图幅，返回 (fig, ax)。"
    return plt.subplots(figsize=size)


def _infer_scope_from_name(fname):
    """命名兜底：从文件名前缀推断 scope，与 catalog 兼容逻辑保持一致。

    返回 'eda' / 'q1' / 'q2' / ... / 'sensitivity' / ''.
    """
    name = str(fname).lower()
    base = name.rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
    if base.startswith('fig_eda'):
        return 'eda'
    if base.startswith('fig_sens'):
        return 'sensitivity'
    import re as _re
    m = _re.match(r'fig_q(\d+)', base)
    if m:
        return 'q' + m.group(1)
    return ''


def save_fig(fname, fig=None, *, scope=None, caption=None):
    """保存图片到工作区，自动设置 dpi/bbox，并写入 figures.jsonl 元数据。

    参数：
    - fname: 文件名（建议用规范命名 fig_q1_xxx.png / fig_eda_xxx.png / fig_sens_xxx.png）
    - fig:   matplotlib Figure；不传则取当前 plt.gcf()
    - scope: 'eda' | 'q1'..'qN' | 'sensitivity'；不传则从文件名推断
    - caption: 图片简短说明（中文），用于 Writer 撰写图注
    """
    (fig or plt.gcf()).savefig(
        fname, dpi=300, bbox_inches='tight', facecolor='white',
    )
    plt.close('all')
    eff_scope = scope or _infer_scope_from_name(fname)
    eff_caption = caption or ''
    try:
        with open('figures.jsonl', 'a', encoding='utf-8') as _f:
            _f.write(_json.dumps({
                'file': str(fname).replace('\\', '/').rsplit('/', 1)[-1],
                'scope': eff_scope,
                'caption': eff_caption,
                'ts': _time.time(),
            }, ensure_ascii=False) + '\n')
    except Exception:
        pass
    print(f'[saved] {fname}' + (f'  (scope={eff_scope})' if eff_scope else ''))


print(f'Sandbox ready | cwd: {os.getcwd()} | font: {_cjk_font or "fallback"}')
