import { useMemo, useState } from 'react'
import { useStore } from '../store'
import { api, TraceEvent } from '../api'
import clsx from 'clsx'

// ── 状态配置 ──────────────────────────────────────────────────────────────────
const STATE_DOT: Record<string, string> = {
  running:       'bg-blue-500 animate-pulse',
  pending:       'bg-ink-300',
  completed:     'bg-emerald-500',
  failed:        'bg-red-500',
  cancelled:     'bg-ink-300',
  paused:        'bg-yellow-400',
  awaiting_hitl: 'bg-orange-400 animate-pulse',
}
const STATE_LABEL: Record<string, string> = {
  running: '运行中', pending: '等待中', completed: '已完成',
  failed: '失败', cancelled: '已取消', paused: '暂停中', awaiting_hitl: '待确认',
}
const ACTIVE_STATES = new Set(['running', 'pending', 'paused', 'awaiting_hitl'])

// ── 时间工具 ──────────────────────────────────────────────────────────────────
function relTime(ts: number): string {
  const d = Date.now() / 1000 - ts
  if (d < 60)    return '刚刚'
  if (d < 3600)  return `${Math.floor(d / 60)}分钟前`
  if (d < 86400) return `${Math.floor(d / 3600)}小时前`
  return `${Math.floor(d / 86400)}天前`
}
function duration(start: number, end: number): string {
  const s = Math.round(end - start)
  if (s < 60)   return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m${s % 60}s`
  return `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m`
}

// ── 阶段进度色 ────────────────────────────────────────────────────────────────
const PHASE_COLOR: Record<string, string> = {
  modeling:   'text-blue-600',
  coding:     'text-violet-600',
  writing:    'text-emerald-600',
  reviewing:  'text-orange-500',
}

type FilterKey = 'all' | 'running' | 'completed' | 'failed'

// ── 主组件 ────────────────────────────────────────────────────────────────────
export function TaskList() {
  const { tasks, currentId, events, selectTask, removeTask } = useStore()
  const [confirmId, setConfirmId] = useState<string | null>(null)
  const [deleting, setDeleting]   = useState<string | null>(null)
  const [search, setSearch]       = useState('')
  const [filter, setFilter]       = useState<FilterKey>('all')

  const doDelete = async (id: string) => {
    setDeleting(id); try { await removeTask(id) }
    finally { setDeleting(null); setConfirmId(null) }
  }

  // 统计各状态数
  const counts = useMemo(() => ({
    running:   tasks.filter(t => ACTIVE_STATES.has(t.state)).length,
    completed: tasks.filter(t => t.state === 'completed').length,
    failed:    tasks.filter(t => t.state === 'failed').length,
  }), [tasks])

  // 过滤 + 搜索
  const filtered = useMemo(() => {
    let list = tasks
    if (filter === 'running')   list = list.filter(t => ACTIVE_STATES.has(t.state))
    if (filter === 'completed') list = list.filter(t => t.state === 'completed')
    if (filter === 'failed')    list = list.filter(t => t.state === 'failed')
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      list = list.filter(t => t.title.toLowerCase().includes(q))
    }
    return list
  }, [tasks, filter, search])

  // 分组：活跃 / 历史
  const active  = filtered.filter(t => ACTIVE_STATES.has(t.state))
  const history = filtered.filter(t => !ACTIVE_STATES.has(t.state))

  if (tasks.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center py-10 text-ink-400">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2"
          className="w-10 h-10 mb-2 opacity-30">
          <path strokeLinecap="round" strokeLinejoin="round"
            d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2
               M9 5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2" />
        </svg>
        <p className="text-xs">暂无任务</p>
        <p className="text-[11px] mt-0.5 opacity-60">点击「新建任务」开始</p>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* ── 搜索框 ── */}
      <div className="px-3 pt-2 pb-1.5">
        <div className="relative">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
            className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-ink-400 pointer-events-none">
            <circle cx="6.5" cy="6.5" r="4" /><path strokeLinecap="round" d="m10 10 2.5 2.5" />
          </svg>
          <input
            value={search} onChange={e => setSearch(e.target.value)}
            placeholder="搜索任务…"
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-ink-100 rounded-lg border border-transparent
                       focus:outline-none focus:border-ink-300 focus:bg-white placeholder-ink-400"
          />
          {search && (
            <button onClick={() => setSearch('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-400 hover:text-ink-700">
              <svg viewBox="0 0 16 16" fill="currentColor" className="w-3 h-3">
                <path d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.75.75 0 1 1 1.06 1.06L9.06 8l3.22 3.22a.75.75 0 1 1-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 0 1-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06Z" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* ── 状态过滤器 ── */}
      <div className="px-3 pb-2 flex gap-1 overflow-x-auto scrollbar-none">
        {([
          ['all',       '全部',  tasks.length],
          ['running',   '进行中', counts.running],
          ['completed', '已完成', counts.completed],
          ['failed',    '失败',  counts.failed],
        ] as [FilterKey, string, number][]).map(([k, label, n]) => (
          <button key={k} onClick={() => setFilter(k)}
            className={clsx(
              'flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] whitespace-nowrap transition-colors shrink-0',
              filter === k
                ? 'bg-ink-800 text-white'
                : 'bg-ink-100 text-ink-500 hover:bg-ink-200'
            )}>
            {label}
            <span className={clsx('px-1 rounded-full text-[9px]',
              filter === k ? 'bg-white/20' : 'bg-ink-200/80')}>
              {n}
            </span>
          </button>
        ))}
      </div>

      {/* ── 任务列表 ── */}
      <div className="flex-1 overflow-auto scrollbar-thin px-2 pb-3">
        {filtered.length === 0 && (
          <p className="text-center text-xs text-ink-400 py-6">无匹配任务</p>
        )}

        {/* 活跃任务组 */}
        {active.length > 0 && (
          <Group label="进行中">
            {active.map(t => (
              <TaskCard key={t.task_id} t={t}
                isActive={currentId === t.task_id}
                isConfirm={confirmId === t.task_id}
                isDeleting={deleting === t.task_id}
                recentEvents={currentId === t.task_id ? events : []}
                onSelect={() => selectTask(t.task_id)}
                onConfirm={() => setConfirmId(t.task_id)}
                onCancel={() => setConfirmId(null)}
                onDelete={() => doDelete(t.task_id)}
              />
            ))}
          </Group>
        )}

        {/* 历史任务组 */}
        {history.length > 0 && (
          <Group label={active.length > 0 ? '历史' : undefined}>
            {history.map(t => (
              <TaskCard key={t.task_id} t={t}
                isActive={currentId === t.task_id}
                isConfirm={confirmId === t.task_id}
                isDeleting={deleting === t.task_id}
                recentEvents={currentId === t.task_id ? events : []}
                onSelect={() => selectTask(t.task_id)}
                onConfirm={() => setConfirmId(t.task_id)}
                onCancel={() => setConfirmId(null)}
                onDelete={() => doDelete(t.task_id)}
              />
            ))}
          </Group>
        )}
      </div>
    </div>
  )
}

// ── 分组标题 ─────────────────────────────────────────────────────────────────
function Group({ label, children }: { label?: string; children: React.ReactNode }) {
  return (
    <div className="mb-1">
      {label && (
        <p className="px-2 pb-1 pt-1.5 text-[10px] font-semibold text-ink-400 uppercase tracking-wide">
          {label}
        </p>
      )}
      {children}
    </div>
  )
}

// ── 任务卡片 ─────────────────────────────────────────────────────────────────
// mini 事件配色
const MINI_EVENT_LABEL: Record<string, { icon: string; color: string; label: (e: TraceEvent) => string }> = {
  'task.started':     { icon: '▶', color: 'text-blue-400',    label: () => '任务启动' },
  'task.completed':   { icon: '✓',  color: 'text-emerald-400', label: () => '已完成' },
  'task.failed':      { icon: '✗',  color: 'text-red-400',     label: (e) => `失败: ${(e.payload?.error || '').slice(0, 20)}` },
  'phase.enter':      { icon: '▸',  color: 'text-violet-400',  label: (e) => `阶段: ${e.payload?.phase}` },
  'agent.message':    { icon: '■',  color: 'text-sky-400',     label: (e) => `${e.agent}: ${(e.payload?.content || '').slice(0, 28)}…` },
  'agent.tool_call':  { icon: '➡',  color: 'text-amber-400',   label: (e) => `调用: ${e.payload?.tool}` },
  'agent.tool_result':{ icon: '↩',  color: 'text-green-400',   label: (e) => `工具返回: ${(e.payload?.output ?? '').toString().slice(0, 24)}…` },
  'sandbox.stdout':   { icon: '»',  color: 'text-ink-400',     label: (e) => (e.payload?.text || '').slice(0, 32) },
  'sandbox.display':  { icon: '🖼', color: 'text-pink-400',   label: (e) => `图表: ${e.payload?.image}` },
  'hitl.request':     { icon: '❗',  color: 'text-orange-400',  label: () => '等待人工确认' },
}
const HIDDEN_IN_MINI = new Set(['agent.thinking', 'agent.llm_usage', 'agent.stream_chunk', 'phase.exit', 'task.created'])

function TaskCard({ t, isActive, isConfirm, isDeleting, recentEvents, onSelect, onConfirm, onCancel, onDelete }: {
  t: any; isActive: boolean; isConfirm: boolean; isDeleting: boolean
  recentEvents: TraceEvent[]
  onSelect: () => void; onConfirm: () => void; onCancel: () => void; onDelete: () => void
}) {
  const [retrying, setRetrying] = useState(false)
  const { refreshCurrent, loadTasks } = useStore()

  const handleRetry = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (retrying) return
    setRetrying(true)
    try {
      await api.retryTask(t.task_id)
      await Promise.all([loadTasks(), refreshCurrent()])
    } catch (err: any) {
      alert('重试失败：' + (err.message || err))
    } finally {
      setRetrying(false)
    }
  }

  const nowSec = Date.now() / 1000
  const isRunning = ACTIVE_STATES.has(t.state)

  // 运行时长
  const dur = isRunning
    ? duration(t.created_at, nowSec)
    : t.updated_at > t.created_at
      ? duration(t.created_at, t.updated_at)
      : null

  return (
    <div className={clsx(
      'group relative mb-1 rounded-lg overflow-hidden transition-colors',
      isActive ? 'bg-ink-800' : 'hover:bg-ink-100'
    )}>
      {/* 主体 */}
      <div onClick={() => !isConfirm && onSelect()}
        className="px-3 py-2.5 pr-8 cursor-pointer select-none">

        {/* 标题行 */}
        <div className="flex items-start gap-2 mb-1">
          <span className={clsx('w-1.5 h-1.5 rounded-full flex-shrink-0 mt-[5px]',
            isActive ? 'bg-white/70' : (STATE_DOT[t.state] || 'bg-ink-300'))} />
          <span className={clsx(
            'text-xs font-medium leading-snug line-clamp-2',
            isActive ? 'text-white' : 'text-ink-800'
          )}>
            {t.title}
          </span>
        </div>

        {/* 元数据行 1：状态 + 阶段 */}
        <div className={clsx('flex items-center gap-1.5 pl-3.5 text-[10px]',
          isActive ? 'text-ink-300' : 'text-ink-500')}>
          <span className={clsx('px-1.5 py-0.5 rounded text-[9px] font-medium shrink-0',
            isActive ? 'bg-white/15' : 'bg-ink-200/80')}>
            {STATE_LABEL[t.state] || t.state}
          </span>
          {t.phase && (
            <span className={clsx('truncate max-w-[72px] font-medium text-[9px]',
              isActive ? 'text-ink-300' : (PHASE_COLOR[t.phase] ?? 'text-ink-500'))}>
              {t.phase}
            </span>
          )}
          {t.state === 'failed' && t.error && (
            <span className="truncate text-[9px] text-red-500 max-w-[80px]" title={t.error}>
              {t.error}
            </span>
          )}
        </div>

        {/* 元数据行 2：附件数 + 时长 + 时间 */}
        <div className={clsx('flex items-center gap-2 pl-3.5 mt-0.5 text-[10px]',
          isActive ? 'text-ink-400' : 'text-ink-400')}>
          {t.data_files?.length > 0 && (
            <span className="flex items-center gap-0.5 shrink-0">
              <svg viewBox="0 0 12 12" fill="currentColor" className="w-2.5 h-2.5 opacity-60">
                <path d="M2 1.5A1.5 1.5 0 0 1 3.5 0h5A1.5 1.5 0 0 1 10 1.5v9A1.5 1.5 0 0 1 8.5 12h-5A1.5 1.5 0 0 1 2 10.5v-9Z" />
              </svg>
              {t.data_files.length}
            </span>
          )}
          {dur && (
            <span className="flex items-center gap-0.5 shrink-0">
              <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.3"
                className="w-2.5 h-2.5 opacity-60">
                <circle cx="6" cy="6" r="5" /><path strokeLinecap="round" d="M6 3v3l2 1.5" />
              </svg>
              {dur}
            </span>
          )}
          <span className="ml-auto shrink-0">{relTime(t.created_at)}</span>
        </div>

        {/* ── mini 事件流（仅选中卡片显示） ── */}
        {isActive && recentEvents.length > 0 && (() => {
          const visible = recentEvents
            .filter(e => !HIDDEN_IN_MINI.has(e.type))
            .slice(-6)
          if (!visible.length) return null
          return (
            <div className="mt-2 pl-3.5 pr-1 border-t border-white/10 pt-2 space-y-1">
              {visible.map(ev => {
                const cfg = MINI_EVENT_LABEL[ev.type]
                const ts = new Date(ev.timestamp * 1000)
                  .toTimeString().slice(0, 8)
                return (
                  <div key={ev.event_id} className="flex items-start gap-1.5 min-w-0">
                    <span className={clsx('shrink-0 text-[10px] leading-[1.4] w-3 text-center', cfg?.color ?? 'text-ink-400')}>
                      {cfg?.icon ?? '·'}
                    </span>
                    <span className="flex-1 text-[10px] text-ink-300 leading-[1.4] truncate">
                      {cfg ? cfg.label(ev) : ev.type}
                    </span>
                    <span className="shrink-0 text-[9px] text-ink-500 font-mono">{ts}</span>
                  </div>
                )
              })}
            </div>
          )
        })()}

        {/* 失败重试按钮 */}
        {t.state === 'failed' && (
          <div className="mt-2 pl-3.5" onClick={e => e.stopPropagation()}>
            <button
              onClick={handleRetry}
              disabled={retrying}
              className={clsx(
                'flex items-center gap-1 px-2.5 py-1 rounded text-[10px] font-medium transition-colors',
                isActive
                  ? 'bg-white/15 text-white hover:bg-white/25 disabled:opacity-40'
                  : 'border border-red-300 text-red-600 bg-red-50 hover:bg-red-100 disabled:opacity-50'
              )}>
              {retrying
                ? <><span className="w-2.5 h-2.5 border-2 border-current/30 border-t-current rounded-full animate-spin" />重试中…</>
                : <>↺ 重新运行</>}
            </button>
          </div>
        )}
      </div>

      {/* 删除按钮 */}
      {!isConfirm && (
        <button onClick={e => { e.stopPropagation(); onConfirm() }} title="删除"
          className={clsx(
            'absolute right-1.5 top-1/2 -translate-y-1/2 w-6 h-6',
            'flex items-center justify-center rounded transition-opacity',
            'opacity-0 group-hover:opacity-100',
            isActive
              ? 'text-ink-400 hover:text-white hover:bg-white/10'
              : 'text-ink-400 hover:text-red-600 hover:bg-red-50'
          )}>
          <TrashIcon />
        </button>
      )}

      {/* 内联删除确认 */}
      {isConfirm && (
        <div className={clsx(
          'absolute inset-0 flex items-center justify-between px-3 gap-2',
          isActive ? 'bg-ink-700/95' : 'bg-white/95 border border-ink-200 rounded-lg'
        )}>
          <span className={clsx('text-xs truncate', isActive ? 'text-ink-200' : 'text-ink-600')}>
            删除并清理？
          </span>
          <div className="flex gap-1 shrink-0">
            <button onClick={e => { e.stopPropagation(); onCancel() }}
              className={clsx('text-[11px] px-2 py-1 rounded',
                isActive ? 'text-ink-300 hover:bg-white/10' : 'text-ink-500 hover:bg-ink-100')}>
              取消
            </button>
            <button onClick={e => { e.stopPropagation(); onDelete() }} disabled={isDeleting}
              className="text-[11px] px-2.5 py-1 bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50 min-w-[36px]">
              {isDeleting ? '…' : '删除'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5">
      <path fillRule="evenodd" d="M5 3.25V4H2.75a.75.75 0 0 0 0 1.5h.3l.815 8.15A1.5 1.5 0 0 0 5.357 15h5.285a1.5 1.5 0 0 0 1.493-1.35l.815-8.15h.3a.75.75 0 0 0 0-1.5H11v-.75A2.25 2.25 0 0 0 8.75 1h-1.5A2.25 2.25 0 0 0 5 3.25Zm2.25-.75a.75.75 0 0 0-.75.75V4h3v-.75a.75.75 0 0 0-.75-.75h-1.5ZM6.05 6a.75.75 0 0 1 .787.713l.275 5.5a.75.75 0 0 1-1.498.075l-.275-5.5A.75.75 0 0 1 6.05 6Zm3.9 0a.75.75 0 0 1 .712.787l-.275 5.5a.75.75 0 0 1-1.498-.075l.275-5.5a.75.75 0 0 1 .786-.711Z" clipRule="evenodd" />
    </svg>
  )
}
