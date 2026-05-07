import { useState, useEffect } from 'react'
import { useStore } from '../store'
import { api } from '../api'

/** 极简 Markdown → HTML（粗体 / 代码 / 标题 / 列表，无外部依赖）*/
function miniMd(text: string): string {
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/^### (.+)$/gm, '<h4 class="text-xs font-semibold text-amber-800 mt-3 mb-1">$1</h4>')
    .replace(/^## (.+)$/gm, '<h3 class="text-sm font-bold text-orange-800 mt-4 mb-1 border-b border-orange-200 pb-0.5">$1</h3>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code class="bg-orange-100 px-0.5 rounded text-[10px] font-mono">$1</code>')
    .replace(/^[-*] (.+)$/gm, '<li class="ml-3 list-disc">$1</li>')
    .replace(/\n/g, '<br/>')
}

export function HITLPanel() {
  const { current, refreshCurrent } = useStore()
  const [mode, setMode] = useState<'preview' | 'edit' | 'redo'>('preview')
  const [edited, setEdited] = useState('')
  const [feedback, setFeedback] = useState('')
  const [loading, setLoading] = useState(false)

  const req = current?.hitl_request
  const plan: string = req?.context?.plan ?? req?.context?.plan_preview ?? ''
  const redoRound: number = req?.context?.redo_round ?? 0
  const maxRedo: number = req?.context?.max_redo ?? 2
  const canRedo = redoRound < maxRedo

  // 新的 HITL 请求到来时（通过 ts 识别）重置所有状态
  useEffect(() => {
    if (!req) return
    setMode('preview')
    setEdited('')
    setFeedback('')
  }, [req?.ts])

  // 切换到编辑模式时预填整体方案（仅首次）
  useEffect(() => {
    if (mode === 'edit' && !edited) setEdited(plan)
  }, [mode])

  if (!req) return null

  const send = async (action: string) => {
    setLoading(true)
    try {
      await api.hitl(current!.task_id, {
        action,
        edited_plan: action === 'edit' ? edited : undefined,
        feedback:    action === 'redo' ? feedback : undefined,
      })
      await refreshCurrent()
      setMode('preview')
    } finally {
      setLoading(false)
    }
  }

  return (
    /* 移动端全屏覆盖；桌面端右侧抽屉 */
    <div className="fixed inset-0 z-40 flex items-stretch md:relative md:inset-auto md:w-[28rem] md:flex-none">
      {/* 移动端半透明遮罩 */}
      <div className="absolute inset-0 bg-black/30 md:hidden" />

      <aside className="relative ml-auto w-full max-w-sm md:max-w-none bg-amber-50 border-l border-amber-300 flex flex-col shadow-xl md:shadow-none">

        {/* 顶栏 */}
        <header className="px-4 py-3 border-b border-amber-300 bg-amber-100 flex-shrink-0">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-amber-900 flex items-center gap-1.5">
              <span>⏸</span>
              <span>建模方案待审核</span>
            </h3>
            {canRedo && (
              <span className="text-[11px] text-amber-700 bg-amber-200 px-2 py-0.5 rounded-full">
                第{redoRound + 1}稿 · 还可重做{maxRedo - redoRound}次
              </span>
            )}
          </div>
          <p className="text-xs text-amber-800 mt-1">{req.prompt}</p>
          {/* 节概览 */}
          {req.context?.sections?.length > 0 && (
            <p className="text-[11px] text-amber-700 mt-1">
              已解析节：{(req.context.sections as string[]).join(' · ')}
            </p>
          )}
        </header>

        {/* 模式切换 tab */}
        <div className="flex border-b border-amber-300 bg-amber-50 flex-shrink-0">
          {(['preview', 'edit', 'redo'] as const).map(m => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`flex-1 py-1.5 text-xs font-medium transition-colors ${
                mode === m
                  ? 'text-amber-900 border-b-2 border-amber-600'
                  : 'text-amber-600 hover:text-amber-800'
              }`}
            >
              {m === 'preview' ? '📄 预览' : m === 'edit' ? '✏️ 修改' : '🔄 重做'}
            </button>
          ))}
        </div>

        {/* 内容区 */}
        <div className="flex-1 overflow-auto p-4 text-xs">
          {mode === 'preview' && (
            <div
              className="leading-relaxed text-ink-700"
              dangerouslySetInnerHTML={{ __html: miniMd(plan) }}
            />
          )}

          {mode === 'edit' && (
            <div className="flex flex-col h-full gap-2">
              <p className="text-amber-700">直接在下方编辑方案，点击「改后通过」提交：</p>
              <textarea
                value={edited}
                onChange={e => setEdited(e.target.value)}
                className="flex-1 min-h-48 w-full px-2 py-1.5 border border-amber-300 rounded text-[11px] font-mono resize-none focus:outline-none focus:ring-1 focus:ring-amber-400"
                placeholder="加载中…"
              />
            </div>
          )}

          {mode === 'redo' && (
            <div className="flex flex-col gap-2">
              <p className="text-amber-700">
                {canRedo
                  ? `描述修改意见，AI 将重新生成方案（剩余${maxRedo - redoRound}次）：`
                  : '已达到最大重做次数，请选择「预览」后通过或手动编辑。'}
              </p>
              <textarea
                value={feedback}
                onChange={e => setFeedback(e.target.value)}
                rows={6}
                disabled={!canRedo}
                placeholder={canRedo ? '例：第二问应改用蒙特卡洛模拟；EDA 需增加时序分析…' : ''}
                className="w-full px-2 py-1.5 border border-amber-300 rounded text-[11px] resize-none focus:outline-none focus:ring-1 focus:ring-amber-400 disabled:opacity-50"
              />
            </div>
          )}
        </div>

        {/* 操作按钮 */}
        <footer className="px-4 py-3 border-t border-amber-300 bg-amber-50 flex flex-col gap-2 flex-shrink-0">
          <button
            onClick={() => send('approve')}
            disabled={loading}
            className="w-full py-2 bg-emerald-600 text-white rounded-lg text-sm font-medium hover:bg-emerald-700 disabled:opacity-40 transition-colors"
          >
            ✓ 确认方案，开始执行
          </button>
          <div className="flex gap-2">
            <button
              onClick={() => send('edit')}
              disabled={loading || mode !== 'edit' || !edited.trim()}
              className="flex-1 py-1.5 bg-blue-600 text-white rounded text-xs font-medium hover:bg-blue-700 disabled:opacity-40 transition-colors"
            >
              ✏️ 改后通过
            </button>
            <button
              onClick={() => send('redo')}
              disabled={loading || mode !== 'redo' || !feedback.trim() || !canRedo}
              className="flex-1 py-1.5 bg-orange-500 text-white rounded text-xs font-medium hover:bg-orange-600 disabled:opacity-40 transition-colors"
            >
              🔄 重新生成
            </button>
          </div>
        </footer>
      </aside>
    </div>
  )
}
