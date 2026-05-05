import { useRef, useState } from 'react'
import { api } from '../api'
import { useStore } from '../store'

function fileIcon(name: string) {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  if (['csv', 'tsv'].includes(ext))        return '📊'
  if (['xlsx', 'xls'].includes(ext))       return '📗'
  if (['json'].includes(ext))              return '📋'
  if (['txt', 'md'].includes(ext))         return '📄'
  if (['png', 'jpg', 'jpeg', 'svg'].includes(ext)) return '🖼'
  if (['pdf'].includes(ext))               return '📕'
  if (['py'].includes(ext))                return '🐍'
  if (['zip', 'rar', '7z'].includes(ext))  return '📦'
  return '📎'
}

function fmtSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function CreateTask({ onClose }: { onClose: () => void }) {
  const [title, setTitle]       = useState('')
  const [problem, setProblem]   = useState('')
  const [files, setFiles]       = useState<File[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [err, setErr]           = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const FILE_INPUT_ID = 'create-task-file-input'
  const { loadTasks, selectTask } = useStore()

  // 累加文件（去重：同名新文件替换旧文件）
  const addFiles = (incoming: FileList | null) => {
    if (!incoming) return
    setFiles(prev => {
      const map = new Map(prev.map(f => [f.name, f]))
      Array.from(incoming).forEach(f => map.set(f.name, f))
      return Array.from(map.values())
    })
  }

  const removeFile = (name: string) =>
    setFiles(prev => prev.filter(f => f.name !== name))

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); setDragOver(false)
    addFiles(e.dataTransfer.files)
  }

  const submit = async () => {
    setErr('')
    if (!title.trim() || problem.trim().length < 10) return
    setSubmitting(true)
    try {
      const task = await api.createTask(title.trim(), problem.trim(), files)
      await loadTasks()
      await selectTask(task.task_id)
      onClose()
    } catch (e: any) {
      setErr(e?.message || '创建失败')
    } finally {
      setSubmitting(false)
    }
  }

  const totalSize = files.reduce((s, f) => s + f.size, 0)

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-2xl w-[660px] max-h-[90vh] flex flex-col">
        {/* 头部 */}
        <div className="px-5 py-3 border-b border-ink-200 flex items-center justify-between">
          <h3 className="font-semibold text-sm">新建建模任务</h3>
          <button onClick={onClose} className="text-ink-400 hover:text-ink-800 text-lg leading-none">✕</button>
        </div>

        {/* 正文 */}
        <div className="flex-1 overflow-auto p-5 space-y-4">
          {/* 标题 */}
          <div>
            <label className="text-xs text-ink-500">任务标题</label>
            <input
              value={title}
              onChange={e => setTitle(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && e.currentTarget.blur()}
              placeholder="如：2024 国赛 A 题 · 板凳龙"
              className="mt-1 w-full px-3 py-2 border border-ink-200 rounded text-sm focus:outline-none focus:border-ink-500"
            />
          </div>

          {/* 赛题 */}
          <div>
            <label className="text-xs text-ink-500">赛题描述（含数据说明、子问题）</label>
            <textarea
              value={problem}
              onChange={e => setProblem(e.target.value)}
              rows={9}
              placeholder="粘贴完整赛题，包含问题 1/2/3..."
              className="mt-1 w-full px-3 py-2 border border-ink-200 rounded text-sm font-mono focus:outline-none focus:border-ink-500 resize-none"
            />
            <p className="text-[11px] text-ink-400 mt-0.5 text-right">{problem.length} 字</p>
          </div>

          {/* 附件区 */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-xs text-ink-500">
                数据附件
                {files.length > 0 && (
                  <span className="ml-1.5 text-ink-400">
                    {files.length} 个 · {fmtSize(totalSize)}
                  </span>
                )}
              </label>
              <label
                htmlFor={FILE_INPUT_ID}
                className="text-xs text-blue-600 hover:underline flex items-center gap-1 cursor-pointer select-none">
                <span className="text-base leading-none">+</span> 添加文件
              </label>
            </div>

            {/* file input：sr-only 视觉隐藏，label 关联触发 */}
            <input
              id={FILE_INPUT_ID}
              ref={inputRef}
              type="file"
              multiple
              className="sr-only"
              onChange={e => { addFiles(e.target.files); e.target.value = '' }}
            />

            {/* 拖拽区 / 文件列表 */}
            <div
              onDragOver={e => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={onDrop}
              onClick={() => { if (files.length === 0) document.getElementById(FILE_INPUT_ID)?.click() }}
              className={`
                border-2 border-dashed rounded-lg transition-colors
                ${dragOver ? 'border-blue-400 bg-blue-50' : 'border-ink-200 bg-ink-50'}
                ${files.length === 0 ? 'cursor-pointer hover:border-ink-400' : ''}
              `}
            >
              {files.length === 0 ? (
                <label htmlFor={FILE_INPUT_ID} className="flex flex-col items-center justify-center py-6 text-ink-400 cursor-pointer w-full">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"
                    className="w-8 h-8 mb-2 opacity-50">
                    <path strokeLinecap="round" strokeLinejoin="round"
                      d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5" />
                  </svg>
                  <p className="text-xs">拖拽文件到此，或点击选择</p>
                  <p className="text-[11px] mt-0.5 opacity-60">支持 CSV / XLSX / JSON / TXT / PNG 等</p>
                </label>
              ) : (
                <ul className="p-2 space-y-1">
                  {files.map(f => (
                    <li key={f.name}
                      className="flex items-center gap-2 px-2 py-1.5 bg-white rounded border border-ink-200 text-xs group">
                      <span className="text-base leading-none">{fileIcon(f.name)}</span>
                      <span className="flex-1 truncate font-medium text-ink-700">{f.name}</span>
                      <span className="text-ink-400 shrink-0">{fmtSize(f.size)}</span>
                      <button
                        type="button"
                        onClick={e => { e.stopPropagation(); removeFile(f.name) }}
                        title="移除"
                        className="opacity-0 group-hover:opacity-100 transition-opacity text-ink-400 hover:text-red-600 shrink-0 ml-1">
                        <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5">
                          <path d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.75.75 0 1 1 1.06 1.06L9.06 8l3.22 3.22a.75.75 0 1 1-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 0 1-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06Z" />
                        </svg>
                      </button>
                    </li>
                  ))}
                  {/* 底部：继续添加按钮 */}
                  <li>
                    <label
                      htmlFor={FILE_INPUT_ID}
                      onClick={e => e.stopPropagation()}
                      className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 rounded border border-dashed border-ink-300 text-xs text-ink-400 hover:border-ink-500 hover:text-ink-600 transition-colors cursor-pointer">
                      <span className="text-sm leading-none">+</span> 继续添加文件
                    </label>
                  </li>
                </ul>
              )}
            </div>
          </div>

          {err && <p className="text-xs text-red-600">{err}</p>}
        </div>

        {/* 底部 */}
        <div className="px-5 py-3 border-t border-ink-200 flex items-center justify-between">
          <p className="text-[11px] text-ink-400">
            {problem.trim().length < 10
              ? `赛题描述至少 10 字（当前 ${problem.trim().length} 字）`
              : ''}
          </p>
          <div className="flex gap-2">
            <button onClick={onClose}
              className="px-3 py-1.5 text-sm text-ink-500 hover:text-ink-800">取消</button>
            <button
              onClick={submit}
              disabled={submitting || !title.trim() || problem.trim().length < 10}
              className="px-4 py-1.5 bg-ink-800 text-white rounded text-sm hover:bg-ink-700 disabled:opacity-40 transition-colors">
              {submitting ? '创建中…' : '开始建模'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
