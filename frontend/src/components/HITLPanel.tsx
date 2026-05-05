import { useState } from 'react'
import { useStore } from '../store'
import { api } from '../api'

export function HITLPanel() {
  const { current, refreshCurrent } = useStore()
  const [edited, setEdited] = useState('')
  const [feedback, setFeedback] = useState('')

  if (!current?.hitl_request) return null
  const req = current.hitl_request

  const send = async (action: string) => {
    await api.hitl(current.task_id, {
      action,
      edited_plan: action === 'edit' ? edited : undefined,
      feedback: action === 'redo' ? feedback : undefined
    })
    await refreshCurrent()
  }

  return (
    <aside className="w-96 bg-orange-50 border-l border-orange-200 flex flex-col">
      <header className="px-4 py-3 border-b border-orange-200">
        <h3 className="text-sm font-semibold text-orange-800">⏸ 等待人工介入</h3>
        <p className="text-xs text-orange-700 mt-1">{req.prompt}</p>
      </header>
      <div className="flex-1 overflow-auto scrollbar-thin p-4 space-y-3 text-xs">
        {req.context?.plan_preview && (
          <details open>
            <summary className="cursor-pointer text-ink-600 font-medium">建模方案预览</summary>
            <pre className="mt-2 bg-white rounded border border-orange-200 p-2 whitespace-pre-wrap text-[11px] max-h-64 overflow-auto">
              {req.context.plan_preview}
            </pre>
          </details>
        )}
        <div>
          <label className="text-ink-600">修改后的方案（仅当点 "改后通过"）</label>
          <textarea
            value={edited}
            onChange={e => setEdited(e.target.value)}
            rows={6}
            className="mt-1 w-full px-2 py-1.5 border border-orange-200 rounded text-[11px] font-mono"
          />
        </div>
        <div>
          <label className="text-ink-600">反馈（仅当点 "重做"）</label>
          <textarea
            value={feedback}
            onChange={e => setFeedback(e.target.value)}
            rows={3}
            placeholder="如：第二问应改用蒙特卡洛模拟"
            className="mt-1 w-full px-2 py-1.5 border border-orange-200 rounded text-[11px]"
          />
        </div>
      </div>
      <footer className="px-4 py-3 border-t border-orange-200 flex flex-col gap-1.5">
        <button onClick={() => send('approve')} className="px-3 py-1.5 bg-emerald-600 text-white rounded text-sm hover:bg-emerald-700">
          通过
        </button>
        <button onClick={() => send('edit')} disabled={!edited.trim()} className="px-3 py-1.5 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:opacity-40">
          改后通过
        </button>
        <button onClick={() => send('redo')} disabled={!feedback.trim()} className="px-3 py-1.5 bg-orange-600 text-white rounded text-sm hover:bg-orange-700 disabled:opacity-40">
          重做
        </button>
      </footer>
    </aside>
  )
}
