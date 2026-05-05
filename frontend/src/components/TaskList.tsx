import { useState } from 'react'
import { useStore } from '../store'
import clsx from 'clsx'

// ---- 状态圆点颜色 ----
const STATE_DOT: Record<string, string> = {
  running:       'bg-blue-500 animate-pulse',
  pending:       'bg-ink-400',
  completed:     'bg-emerald-500',
  failed:        'bg-red-500',
  cancelled:     'bg-ink-300',
  paused:        'bg-yellow-400',
  awaiting_hitl: 'bg-orange-500 animate-pulse',
}
const STATE_LABEL: Record<string, string> = {
  running:       '运行中',
  pending:       '等待中',
  completed:     '已完成',
  failed:        '失败',
  cancelled:     '已取消',
  paused:        '已暂停',
  awaiting_hitl: '等待确认',
}

function relTime(ts: number): string {
  const diff = Date.now() / 1000 - ts
  if (diff < 60)    return '刚刚'
  if (diff < 3600)  return `${Math.floor(diff / 60)}分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`
  return `${Math.floor(diff / 86400)}天前`
}

export function TaskList() {
  const { tasks, currentId, selectTask, removeTask } = useStore()
  const [confirmId, setConfirmId] = useState<string | null>(null)
  const [deleting, setDeleting]   = useState<string | null>(null)

  const doDelete = async (id: string) => {
    setDeleting(id)
    try { await removeTask(id) }
    finally { setDeleting(null); setConfirmId(null) }
  }

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
    <div className="flex-1 overflow-auto scrollbar-thin px-2 pb-3">
      {tasks.map(t => {
        const isActive   = currentId === t.task_id
        const isConfirm  = confirmId === t.task_id
        const isDeleting = deleting === t.task_id

        return (
          <div key={t.task_id}
            className={clsx(
              'group relative mb-1 rounded-lg overflow-hidden transition-colors',
              isActive ? 'bg-ink-800' : 'hover:bg-ink-100'
            )}>

            {/* 主体区域 */}
            <div
              onClick={() => !isConfirm && selectTask(t.task_id)}
              className="px-3 py-2.5 pr-9 cursor-pointer select-none">
              {/* 标题 + 状态点 */}
              <div className="flex items-start gap-2">
                <span className={clsx(
                  'w-1.5 h-1.5 rounded-full flex-shrink-0 mt-[5px]',
                  isActive ? 'bg-white/70' : (STATE_DOT[t.state] || 'bg-ink-300')
                )} />
                <span className={clsx(
                  'text-sm font-medium leading-snug break-all line-clamp-2',
                  isActive ? 'text-white' : 'text-ink-800'
                )}>
                  {t.title}
                </span>
              </div>
              {/* 元数据行 */}
              <div className={clsx(
                'text-[10px] mt-1 pl-3.5 flex items-center gap-1.5',
                isActive ? 'text-ink-300' : 'text-ink-400'
              )}>
                <span className={clsx(
                  'px-1 py-0.5 rounded text-[9px] font-medium',
                  isActive ? 'bg-white/10' : 'bg-ink-200/60'
                )}>
                  {STATE_LABEL[t.state] || t.state}
                </span>
                {t.phase && <span className="truncate max-w-[60px]">· {t.phase}</span>}
                <span className="ml-auto shrink-0">{relTime(t.created_at)}</span>
              </div>
            </div>

            {/* 删除按钮（hover 出现） */}
            {!isConfirm && (
              <button
                onClick={e => { e.stopPropagation(); setConfirmId(t.task_id) }}
                title="删除任务"
                className={clsx(
                  'absolute right-2 top-1/2 -translate-y-1/2',
                  'w-6 h-6 flex items-center justify-center rounded',
                  'opacity-0 group-hover:opacity-100 transition-opacity',
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
                isActive ? 'bg-ink-700/95' : 'bg-white/95 border border-ink-200'
              )}>
                <span className={clsx('text-xs truncate', isActive ? 'text-ink-200' : 'text-ink-600')}>
                  删除并清理文件？
                </span>
                <div className="flex gap-1 shrink-0">
                  <button
                    onClick={e => { e.stopPropagation(); setConfirmId(null) }}
                    className={clsx(
                      'text-[11px] px-2 py-1 rounded',
                      isActive ? 'text-ink-300 hover:bg-white/10' : 'text-ink-500 hover:bg-ink-100'
                    )}>
                    取消
                  </button>
                  <button
                    onClick={e => { e.stopPropagation(); doDelete(t.task_id) }}
                    disabled={isDeleting}
                    className="text-[11px] px-2 py-1 bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50 min-w-[36px]">
                    {isDeleting ? '…' : '删除'}
                  </button>
                </div>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function TrashIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5">
      <path fillRule="evenodd" d="M5 3.25V4H2.75a.75.75 0 0 0 0 1.5h.3l.815 8.15A1.5 1.5 0 0 0 5.357 15h5.285a1.5 1.5 0 0 0 1.493-1.35l.815-8.15h.3a.75.75 0 0 0 0-1.5H11v-.75A2.25 2.25 0 0 0 8.75 1h-1.5A2.25 2.25 0 0 0 5 3.25Zm2.25-.75a.75.75 0 0 0-.75.75V4h3v-.75a.75.75 0 0 0-.75-.75h-1.5ZM6.05 6a.75.75 0 0 1 .787.713l.275 5.5a.75.75 0 0 1-1.498.075l-.275-5.5A.75.75 0 0 1 6.05 6Zm3.9 0a.75.75 0 0 1 .712.787l-.275 5.5a.75.75 0 0 1-1.498-.075l.275-5.5a.75.75 0 0 1 .786-.711Z" clipRule="evenodd" />
    </svg>
  )
}
