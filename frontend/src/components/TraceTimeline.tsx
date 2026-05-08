import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '../store'
import { api, Task, TraceEvent } from '../api'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import clsx from 'clsx'

// ---- 颜色映射 ----
const AGENT_BADGE: Record<string, string> = {
  coordinator: 'bg-pink-100 text-pink-700 border-pink-200',
  modeler: 'bg-purple-100 text-purple-700 border-purple-200',
  coder:   'bg-blue-100 text-blue-700 border-blue-200',
  writer:  'bg-emerald-100 text-emerald-700 border-emerald-200',
}
const AGENT_BORDER: Record<string, string> = {
  coordinator: 'border-l-pink-400',
  modeler: 'border-l-purple-400',
  coder:   'border-l-blue-400',
  writer:  'border-l-emerald-400',
  sandbox: 'border-l-ink-500',
}

const fmtN = (n: number) => n >= 1000 ? (n / 1000).toFixed(1) + 'K' : String(n)

// ==============================
// 实时状态栏
// ==============================
function LiveStatus({ events, current, streamingContent }: {
  events: TraceEvent[]; current: Task | null
  streamingContent: Record<string, string>
}) {
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    if (!current) return
    const start = current.created_at
    setElapsed(Date.now() / 1000 - start)
    if (current.state === 'completed' || current.state === 'failed') return
    const t = setInterval(() => setElapsed(Date.now() / 1000 - start), 1000)
    return () => clearInterval(t)
  }, [current?.task_id, current?.state, current?.created_at])

  if (!current) return null

  // 跳过 llm_usage / thinking，找最新可视事件
  const lastVisible = [...events].reverse().find(
    e => e.type !== 'agent.llm_usage' && e.type !== 'agent.thinking'
  )
  const lastThinking = events.at(-1)?.type === 'agent.thinking' ? events.at(-1) : null
  const currentPhase = [...events].reverse().find(e => e.type === 'phase.enter')?.payload?.phase
  const streamingAgents = Object.keys(streamingContent ?? {})

  const isActive = current.state === 'running'

  const statusLabel = () => {
    if (current.state === 'completed') return '✓ 任务完成'
    if (current.state === 'failed')    return `✗ 失败：${current.error || ''}`
    if (current.state === 'paused')    return '⏸ 已暂停'
    if (current.state === 'awaiting_hitl') {
      const ctx = current.hitl_request?.context
      if (ctx?.redo_round !== undefined) {
        return `⏸ 建模方案（第 ${(ctx.redo_round as number) + 1} 稿）待审核`
      }
      return '⏸ 等待人工介入'
    }
    if (streamingAgents.length > 0) return `${streamingAgents.join('/')} 输出中…`
    if (lastThinking) return `${lastThinking.agent} 思考中（第 ${lastThinking.payload?.step} 步）`
    if (lastVisible?.type === 'agent.tool_call')
      return `${lastVisible.agent} → 调用工具：${lastVisible.payload?.tool}`
    if (lastVisible?.type?.startsWith('sandbox.')) return '🔧 执行代码中...'
    if (currentPhase) return `阶段：${currentPhase}`
    return '运行中...'
  }

  const barCls: Record<string, string> = {
    running:       'bg-blue-50 border-blue-200 text-blue-800',
    paused:        'bg-yellow-50 border-yellow-200 text-yellow-800',
    awaiting_hitl: 'bg-orange-50 border-orange-200 text-orange-800',
    completed:     'bg-emerald-50 border-emerald-200 text-emerald-800',
    failed:        'bg-red-50 border-red-200 text-red-800',
  }

  const fmtTime = (s: number) =>
    s < 60 ? `${Math.floor(s)}s` : `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`

  const [retrying, setRetrying] = useState(false)
  const { refreshCurrent } = useStore()

  const handleRetry = useCallback(async () => {
    if (!current || retrying) return
    setRetrying(true)
    try {
      await api.retryTask(current.task_id)
      await refreshCurrent()
    } catch (e: any) {
      alert('重试失败：' + (e.message || e))
    } finally {
      setRetrying(false)
    }
  }, [current, retrying, refreshCurrent])

  return (
    <div className={clsx('px-4 py-2 border-b flex items-center gap-3 text-xs',
      barCls[current.state] || 'bg-ink-50 border-ink-200 text-ink-700')}>
      {isActive && (
        <span className="relative flex h-2 w-2 flex-shrink-0">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
          <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-500" />
        </span>
      )}
      <span className="flex-1 font-medium truncate">{statusLabel()}</span>
      {currentPhase && isActive && (
        <span className="text-[10px] px-1.5 py-0.5 bg-white/60 rounded border border-current/20 shrink-0">
          {currentPhase}
        </span>
      )}
      {current.state === 'failed' && (
        <button
          onClick={handleRetry}
          disabled={retrying}
          className="shrink-0 flex items-center gap-1 px-2.5 py-1 rounded text-[11px] font-medium
                     bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 transition-colors">
          {retrying
            ? <><span className="w-3 h-3 border-2 border-white/40 border-t-white rounded-full animate-spin" />重试中…</>
            : <>↺ 重试</>}
        </button>
      )}
      <span className="font-mono text-[10px] opacity-60 shrink-0">{fmtTime(elapsed)}</span>
    </div>
  )
}

// ==============================
// Token 统计栏
// ==============================
function TokenBar({ events }: { events: TraceEvent[] }) {
  const [expanded, setExpanded] = useState(false)

  const { total, byAgent } = useMemo(() => {
    const usageEvs = events.filter(e => e.type === 'agent.llm_usage')
    const total = { calls: 0, prompt: 0, completion: 0, total: 0, cost: 0 }
    const map: Record<string, typeof total & { model: string }> = {}

    for (const ev of usageEvs) {
      const p = ev.payload
      total.calls++
      total.prompt     += p.prompt_tokens     || 0
      total.completion += p.completion_tokens || 0
      total.total      += p.total_tokens      || 0
      total.cost       += p.cost_usd          || 0

      const a = ev.agent || 'unknown'
      if (!map[a]) map[a] = { calls: 0, prompt: 0, completion: 0, total: 0, cost: 0, model: p.model || '' }
      map[a].calls++
      map[a].prompt     += p.prompt_tokens     || 0
      map[a].completion += p.completion_tokens || 0
      map[a].total      += p.total_tokens      || 0
      map[a].cost       += p.cost_usd          || 0
    }

    return { total, byAgent: Object.entries(map) }
  }, [events])

  if (total.calls === 0) return null

  return (
    <div className="px-4 py-1.5 bg-amber-50 border-b border-amber-200 text-[11px]">
      <div
        className="flex items-center gap-3 cursor-pointer select-none"
        onClick={() => setExpanded(v => !v)}>
        <span className="font-medium text-amber-800">⚡ Tokens</span>
        <span className="font-mono text-ink-600" title="Prompt tokens">↑{fmtN(total.prompt)}</span>
        <span className="font-mono text-ink-600" title="Completion tokens">↓{fmtN(total.completion)}</span>
        <span className="font-mono font-semibold text-ink-800" title="Total tokens">∑{fmtN(total.total)}</span>
        {total.cost > 0 && (
          <span className="font-mono text-emerald-700">${total.cost.toFixed(4)}</span>
        )}
        <span className="ml-auto text-amber-700">{total.calls} 次调用 {expanded ? '▲' : '▼'}</span>
      </div>

      {expanded && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {byAgent.map(([agent, s]) => (
            <div key={agent}
              className={clsx('flex items-center gap-1.5 px-2 py-1 rounded border text-[10px]',
                AGENT_BADGE[agent] || 'bg-ink-100 text-ink-600 border-ink-200')}>
              <span className="font-semibold">{agent}</span>
              <span className="font-mono opacity-80">↑{fmtN(s.prompt)}</span>
              <span className="font-mono opacity-80">↓{fmtN(s.completion)}</span>
              <span className="font-mono font-bold">∑{fmtN(s.total)}</span>
              {s.cost > 0 && <span className="font-mono">${s.cost.toFixed(4)}</span>}
              {s.model && <span className="opacity-50 font-mono">{s.model}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ==============================
// 主组件
// ==============================
export function TraceTimeline() {
  const { events, current, streamingContent } = useStore()
  const [filter, setFilter] = useState<'all' | 'agent' | 'sandbox' | 'task'>('all')
  const [autoScroll, setAutoScroll] = useState(true)
  const ref = useRef<HTMLDivElement>(null)

  // 隐藏纯噪声事件：thinking / llm_usage 由状态栏和 token 栏承担
  const filtered = useMemo(() => {
    const hidden = new Set(['agent.thinking', 'agent.llm_usage'])
    const base = events.filter(e => !hidden.has(e.type))
    if (filter === 'all') return base
    return base.filter(e => e.type.startsWith(filter))
  }, [events, filter])

  // 自动滚底（监听事件列表 + 流式内容双重触发）
  const streamingKey = Object.values(streamingContent).join('').length
  useEffect(() => {
    if (!autoScroll) return
    const el = ref.current
    if (el) el.scrollTop = el.scrollHeight
  }, [filtered.length, streamingKey, autoScroll])

  // 手动滚动时暂停自动滚底
  const handleScroll = () => {
    const el = ref.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
    setAutoScroll(atBottom)
  }

  const [ctrlBusy, setCtrlBusy] = useState<string | null>(null)
  const { refreshCurrent } = useStore()

  const ctrlClick = (key: string, fn: () => Promise<any>) => async () => {
    if (ctrlBusy) return
    setCtrlBusy(key)
    try { await fn() } catch {}
    // 主动刷新，不完全依赖 WS 事件
    setTimeout(() => { refreshCurrent(); setCtrlBusy(null) }, 350)
  }

  return (
    <div className="h-full flex flex-col">
      <LiveStatus events={events} current={current} streamingContent={streamingContent} />
      <TokenBar events={events} />

      {/* 工具栏 */}
      <div className="px-4 py-2 border-b border-ink-200 flex items-center gap-2 bg-white">
        <div className="flex bg-ink-100 rounded p-0.5 text-xs">
          {(['all', 'agent', 'sandbox', 'task'] as const).map(k => (
            <button key={k} onClick={() => setFilter(k)}
              className={clsx('px-2.5 py-0.5 rounded transition-colors',
                filter === k ? 'bg-white shadow-sm font-medium' : 'text-ink-500 hover:text-ink-700')}>
              {k}
            </button>
          ))}
        </div>
        <span className="text-[11px] text-ink-400">{filtered.length} 条</span>
        {!autoScroll && (
          <button
            onClick={() => { setAutoScroll(true); ref.current?.scrollTo({ top: 1e9, behavior: 'smooth' }) }}
            className="text-[11px] px-2 py-0.5 bg-blue-100 text-blue-700 rounded hover:bg-blue-200">
            ↓ 跳到底部
          </button>
        )}
        <div className="flex-1" />
        {current && (
          <div className="flex gap-1.5 text-xs">
            {/* 暂停：仅运行中时显示 */}
            {current.state === 'running' && (
              <button
                disabled={ctrlBusy === 'pause'}
                onClick={ctrlClick('pause', () => api.pause(current.task_id))}
                className="px-2 py-1 bg-yellow-100 hover:bg-yellow-200 text-yellow-800 rounded border border-yellow-200 disabled:opacity-50">
                {ctrlBusy === 'pause' ? '…' : '⏸ 暂停'}
              </button>
            )}
            {/* 继续：仅暂停中时显示 */}
            {current.state === 'paused' && (
              <button
                disabled={ctrlBusy === 'resume'}
                onClick={ctrlClick('resume', () => api.resume(current.task_id))}
                className="px-2 py-1 bg-emerald-100 hover:bg-emerald-200 text-emerald-800 rounded border border-emerald-200 disabled:opacity-50">
                {ctrlBusy === 'resume' ? '…' : '▶ 继续'}
              </button>
            )}
            {/* 取消：仅活跃状态 */}
            {['running', 'paused', 'awaiting_hitl', 'pending'].includes(current.state) && (
              <button onClick={async () => {
                try { await api.cancel(current.task_id) } catch {}
                setTimeout(() => refreshCurrent(), 400)
              }}
                className="px-2 py-1 bg-red-50 text-red-600 hover:bg-red-100 rounded border border-red-200">
                ✕ 取消
              </button>
            )}
          </div>
        )}
      </div>

      {/* 事件流 */}
      <div ref={ref} onScroll={handleScroll}
        className="flex-1 overflow-auto scrollbar-thin px-4 py-3 space-y-1.5 bg-ink-50">
        {filtered.map(ev => {
          // 找紧跟其后的 llm_usage 事件，用于 token badge
          const idx = events.indexOf(ev)
          const usageEv = idx >= 0 && events[idx + 1]?.type === 'agent.llm_usage'
            ? events[idx + 1] : undefined
          return <EventCard key={ev.event_id} ev={ev} usageEv={usageEv} />
        })}

        {/* 思考动画 —— 无流式输出时才显示 */}
        {events.at(-1)?.type === 'agent.thinking' && Object.keys(streamingContent).length === 0 && (
          <div className="flex items-center gap-2 py-2 px-3 text-xs text-ink-400">
            <span className="flex gap-1 items-end h-3">
              {[0, 1, 2].map(i => (
                <span key={i} className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-bounce"
                  style={{ animationDelay: `${i * 0.15}s` }} />
              ))}
            </span>
            <span>{events.at(-1)?.agent} 思考中...</span>
          </div>
        )}

        {/* 实时流式输出卡片 */}
        {Object.entries(streamingContent).map(([agentName, text]) => (
          <StreamingCard key={agentName} agent={agentName} content={text} />
        ))}

        {filtered.length === 0 && (
          <p className="text-xs text-ink-400 py-4 text-center">暂无事件</p>
        )}
      </div>
    </div>
  )
}

// ==============================
// 事件卡片
// ==============================
function EventCard({ ev, usageEv }: { ev: TraceEvent; usageEv?: TraceEvent }) {
  const [collapsed, setCollapsed] = useState(true)
  const time = new Date(ev.timestamp * 1000).toLocaleTimeString()
  const agent = ev.agent || (ev.type.startsWith('sandbox') ? 'sandbox' : 'system')

  const isPhase = ev.type.startsWith('phase.')
  const isTask  = ev.type.startsWith('task.')

  if (isPhase || isTask) {
    // 轻量行内样式，不显示卡片框
    return (
      <div className="flex items-center gap-2 px-2 py-1 text-xs text-ink-400">
        <span className="w-12 shrink-0 font-mono text-[10px]">{time}</span>
        <EventBody ev={ev} collapsed={collapsed} onToggle={() => setCollapsed(v => !v)} />
      </div>
    )
  }

  return (
    <div className={clsx(
      'border border-ink-200 rounded-lg bg-white border-l-[3px] min-w-0 overflow-hidden',
      AGENT_BORDER[agent] || 'border-l-ink-300'
    )}>
      {/* 头部 */}
      <div className="flex items-center gap-2 px-3 pt-2 pb-1.5 text-[11px] text-ink-400">
        <span className={clsx('px-1.5 py-0.5 rounded border text-[10px] font-medium',
          AGENT_BADGE[agent] || 'bg-ink-100 text-ink-600 border-ink-200')}>
          {agent}
        </span>
        <span className="font-mono text-[10px] opacity-70">{ev.type}</span>
        {/* Token badge —— 仅在 agent.message 后有 llm_usage 时显示 */}
        {usageEv && ev.type === 'agent.message' && (
          <span className="flex items-center gap-1 px-1.5 py-0.5 bg-amber-50 text-amber-700 border border-amber-200 rounded text-[10px] font-mono">
            <span title="Prompt tokens">↑{fmtN(usageEv.payload.prompt_tokens || 0)}</span>
            <span title="Completion tokens">↓{fmtN(usageEv.payload.completion_tokens || 0)}</span>
            <span title="Total" className="font-semibold">∑{fmtN(usageEv.payload.total_tokens || 0)}</span>
          </span>
        )}
        <span className="ml-auto font-mono">{time}</span>
      </div>
      {/* 内容 */}
      <div className="px-3 pb-3">
        <EventBody ev={ev} collapsed={collapsed} onToggle={() => setCollapsed(v => !v)} />
      </div>
    </div>
  )
}

// ==============================
// 失败事件行（内含重试按钮）
// ==============================
function FailedEventRow({ error, taskId }: { error: string; taskId: string }) {
  const [retrying, setRetrying] = useState(false)
  const { refreshCurrent } = useStore()

  const handleRetry = useCallback(async () => {
    if (retrying) return
    setRetrying(true)
    try {
      await api.retryTask(taskId)
      await refreshCurrent()
    } catch (e: any) {
      alert('重试失败：' + (e.message || e))
    } finally {
      setRetrying(false)
    }
  }, [taskId, retrying, refreshCurrent])

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="text-[11px] text-red-700">✗ 失败：{error}</span>
      <button
        onClick={handleRetry}
        disabled={retrying}
        className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium
                   border border-red-300 text-red-600 bg-red-50 hover:bg-red-100
                   disabled:opacity-50 transition-colors shrink-0">
        {retrying
          ? <><span className="w-2.5 h-2.5 border-2 border-red-300 border-t-red-600 rounded-full animate-spin" />重试中…</>
          : <>↺ 重试</>}
      </button>
    </div>
  )
}

// ==============================
// 事件内容
// ==============================
function EventBody({ ev, collapsed, onToggle }: {
  ev: TraceEvent
  collapsed: boolean
  onToggle: () => void
}) {
  const p = ev.payload

  switch (ev.type) {
    case 'agent.message':
      return p.content ? (
        <div className="markdown-body text-sm leading-relaxed">
          <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
            {String(p.content)}
          </ReactMarkdown>
        </div>
      ) : (
        <span className="text-xs text-ink-400">[空消息{p.has_tools ? ' · 调用工具' : ''}]</span>
      )

    case 'agent.tool_call':
      return (
        <div className="text-xs space-y-1">
          <div className="font-mono font-semibold text-blue-700">→ {p.tool}</div>
          <pre className="bg-ink-100 rounded p-2 overflow-x-auto text-[11px] max-w-full whitespace-pre-wrap break-all">
            {JSON.stringify(p.args, null, 2)}
          </pre>
        </div>
      )

    case 'agent.tool_result': {
      const str = JSON.stringify(p.result, null, 2)
      const long = str.length > 500
      return (
        <div className="text-xs space-y-1">
          <div className="flex items-center gap-2">
            <span className="font-mono text-emerald-700">← {p.tool}</span>
            {long && (
              <button onClick={onToggle}
                className="text-[10px] text-blue-600 hover:underline ml-auto">
                {collapsed ? '展开' : '收起'}
              </button>
            )}
          </div>
          <pre className={clsx(
            'bg-ink-50 border border-ink-200 rounded p-2 overflow-x-auto text-[11px] transition-all max-w-full whitespace-pre-wrap break-all',
            collapsed && long ? 'max-h-24 overflow-hidden' : 'max-h-[400px] overflow-auto'
          )}>{str}</pre>
        </div>
      )
    }

    case 'sandbox.stdout': {
      const long = (p.text || '').length > 600
      return (
        <div className="text-xs space-y-1">
          {long && (
            <div className="flex justify-end">
              <button onClick={onToggle} className="text-[10px] text-ink-400 hover:text-ink-700">
                {collapsed ? '展开全部' : '收起'}
              </button>
            </div>
          )}
          <pre className={clsx(
            'bg-ink-900 text-emerald-300 rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono text-[11px] leading-relaxed max-w-full',
            collapsed && long ? 'max-h-40 overflow-hidden' : ''
          )}>{p.text}</pre>
        </div>
      )
    }

    case 'sandbox.stderr':
      return (
        <pre className="text-[11px] bg-red-50 text-red-700 rounded p-2 overflow-x-auto whitespace-pre-wrap max-w-full">
          {p.text}
        </pre>
      )

    case 'sandbox.result':
      return (
        <pre className="text-[11px] bg-ink-100 rounded p-2 overflow-x-auto whitespace-pre-wrap max-w-full">
          {p.text}
        </pre>
      )

    case 'sandbox.display':
      return p.image ? <SandboxImage image={p.image} /> : null

    case 'phase.enter':
      return (
        <span className="text-[11px] px-2 py-0.5 bg-blue-100 text-blue-700 rounded font-medium">
          ▶ {p.phase}
        </span>
      )
    case 'phase.exit':
      return (
        <span className="text-[11px] px-2 py-0.5 bg-emerald-100 text-emerald-700 rounded font-medium">
          ✓ {p.phase}
        </span>
      )

    case 'hitl.request':
      return <span className="text-xs text-orange-700 font-medium">⏸ {p.prompt}</span>
    case 'hitl.resolved': {
      const actionMap: Record<string, string> = {
        approve: '✓ 方案已确认，继续执行',
        edit:    '✏️ 方案已手动修改并通过',
        redo:    '🔄 已请求重新生成方案',
      }
      const act = p.response?.action || 'approve'
      return <span className="text-xs text-emerald-700">{actionMap[act] ?? `✓ 已处理（${act}）`}</span>
    }
    case 'hitl.timeout':
      return (
        <span className="text-xs text-amber-700 font-medium">
          ⏱ {p.message || '人工审核超时，已自动批准'}
        </span>
      )

    case 'artifact.missing':
      return (
        <div className="text-xs text-orange-800 space-y-1">
          <div className="font-medium">
            ⚠️ {p.phase || '阶段'} 缺失图表 {(p.missing as string[])?.length || 0} 张，尝试补做：
          </div>
          <ul className="list-disc list-inside font-mono text-[10px] text-orange-700">
            {(p.missing as string[] || []).map(f => <li key={f}>{f}</li>)}
          </ul>
        </div>
      )
    case 'artifact.recovered':
      return (
        <div className="text-xs text-emerald-700 space-y-1">
          <div className="font-medium">
            ✓ {p.phase || '阶段'} 补做成功 {(p.recovered as string[])?.length || 0} 张
            {(p.still_missing as string[])?.length ? `，仍缺 ${(p.still_missing as string[]).length} 张` : ''}
          </div>
          {(p.still_missing as string[])?.length > 0 && (
            <ul className="list-disc list-inside font-mono text-[10px] text-amber-700">
              {(p.still_missing as string[]).map(f => <li key={f}>{f}</li>)}
            </ul>
          )}
        </div>
      )

    case 'task.created':   return <span className="text-[11px] text-ink-500">📌 任务已创建</span>
    case 'task.started':   return <span className="text-[11px] text-blue-700">▶ 任务开始运行</span>
    case 'task.completed': return <span className="text-[11px] text-emerald-700 font-semibold">✓ 任务已完成</span>
    case 'task.failed':    return <FailedEventRow error={p.error} taskId={ev.task_id} />
    case 'task.cancelled': return <span className="text-[11px] text-ink-500">■ 任务已取消</span>
    case 'task.paused':    return <span className="text-[11px] text-yellow-700">⏸ 已暂停</span>
    case 'task.resumed':   return <span className="text-[11px] text-blue-700">▶ 已恢复运行</span>

    default:
      return (
        <pre className="text-[11px] text-ink-400 overflow-x-auto whitespace-pre-wrap break-all max-w-full">
          {JSON.stringify(p, null, 2)}
        </pre>
      )
  }
}

// sandbox.display 独立组件，规避 hooks 条件调用
function SandboxImage({ image }: { image: string }) {
  const url = useTaskFileUrl(image)
  return <img src={url} alt="figure" className="max-w-full rounded border border-ink-200 mt-1" />
}

// ==============================
// 流式输出卡片
// ==============================
function StreamingCard({ agent, content }: { agent: string; content: string }) {
  return (
    <div className={clsx(
      'border border-ink-200 rounded-lg bg-white border-l-[3px] min-w-0 overflow-hidden',
      AGENT_BORDER[agent] || 'border-l-blue-400',
    )}>
      {/* 头部 */}
      <div className="flex items-center gap-2 px-3 pt-2 pb-1.5 text-[11px] text-ink-400">
        <span className={clsx('px-1.5 py-0.5 rounded border text-[10px] font-medium',
          AGENT_BADGE[agent] || 'bg-ink-100 text-ink-600 border-ink-200')}>
          {agent}
        </span>
        <span className="font-mono text-[10px] opacity-60">streaming…</span>
        {/* 流式动画指示器 */}
        <span className="flex gap-0.5 items-end h-3 ml-1">
          {[0, 1, 2].map(i => (
            <span key={i} className="w-1 h-1 rounded-full bg-blue-400 animate-bounce"
              style={{ animationDelay: `${i * 0.12}s` }} />
          ))}
        </span>
      </div>
      {/* 内容：实时 Markdown 预览 */}
      <div className="px-3 pb-3">
        <div className="markdown-body text-sm leading-relaxed">
          <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
            {content}
          </ReactMarkdown>
          {/* 打字机光标 */}
          <span className="inline-block w-0.5 h-[1em] bg-current align-middle animate-pulse ml-0.5 opacity-70" />
        </div>
      </div>
    </div>
  )
}

function useTaskFileUrl(absPath: string): string {
  const { current } = useStore()
  if (!current) return ''
  const wd = current.work_dir.replace(/\\/g, '/')
  const norm = absPath.replace(/\\/g, '/')
  const rel = norm.startsWith(wd)
    ? norm.slice(wd.length).replace(/^\/+/, '')
    : norm.split('/').pop() || ''
  return api.fileUrl(current.task_id, rel)
}
