import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useStore } from '../store'
import clsx from 'clsx'

const IMAGE_EXTS = new Set(['png','jpg','jpeg','webp','gif'])
const isImageFile = (name: string) => IMAGE_EXTS.has(name.split('.').pop()?.toLowerCase() ?? '')

// 文件选择框接受的格式
const FILE_ACCEPT = [
  '.csv', '.tsv', '.xlsx', '.xls', '.json', '.txt', '.md',
  '.png', '.jpg', '.jpeg', '.webp', '.gif', '.pdf',
  '.py', '.ipynb', '.zip',
].join(',')

function fileIcon(name: string) {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  if (['csv', 'tsv'].includes(ext))        return '📊'
  if (['xlsx', 'xls'].includes(ext))       return '📗'
  if (['json'].includes(ext))              return '📋'
  if (['txt', 'md'].includes(ext))         return '📄'
  if (['png', 'jpg', 'jpeg', 'svg', 'webp'].includes(ext)) return '🖼'
  if (['pdf'].includes(ext))               return '📕'
  if (['py', 'ipynb'].includes(ext))       return '🐍'
  if (['zip', 'rar', '7z'].includes(ext))  return '📦'
  return '📎'
}

function fmtSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

// ---------- 示例题目 ----------
const EXAMPLES = [
  {
    label: '农作物种植策略',
    title: '2024 高教杯 C 题 · 农作物种植策略',
    problem: '某地区有多种农作物可供种植，各作物的产量、收益、需水量等参数不同。\n问题 1：建立数学模型，在水资源和土地约束下，给出最优种植策略使总收益最大化。\n问题 2：考虑气候不确定性，建立鲁棒优化模型，分析极端天气对种植方案的影响。\n问题 3：基于历年数据，预测未来三年各作物价格走势，并据此调整种植策略。',
  },
  {
    label: '交通流量预测',
    title: '2024 数模竞赛 · 城市交通流量优化',
    problem: '给定某城市核心路段一周的车流量时序数据（含早晚高峰、周末差异）。\n问题 1：对交通流量进行时序分析，找出周期性规律并建立预测模型。\n问题 2：识别拥堵瓶颈路段，建立交通分配模型提出优化方案。\n问题 3：评估新增公交线路对路网拥堵的缓解效果，给出量化指标。',
  },
  {
    label: '人口预测建模',
    title: '人口结构变化与政策影响分析',
    problem: '提供某省过去 30 年人口出生率、死亡率、迁移率等统计数据。\n问题 1：构建人口预测模型（如 Leslie 矩阵或 ARIMA），预测未来 20 年人口规模。\n问题 2：分析老龄化趋势对劳动力和社保资金的影响，建立可持续性评估模型。\n问题 3：对比不同生育政策情景，量化政策效果并给出政策建议。',
  },
]

export function CreateTask({ onClose }: { onClose: () => void }) {
  const [title, setTitle]         = useState('')
  const [problem, setProblem]     = useState('')
  const [files, setFiles]         = useState<File[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [dragOver, setDragOver]   = useState(false)
  const [err, setErr]             = useState('')
  const [showExamples, setShowExamples] = useState(false)
  const fileInputRef   = useRef<HTMLInputElement>(null)
  const cameraInputRef = useRef<HTMLInputElement>(null)
  const { loadTasks, selectTask } = useStore()

  // 图片预览缓存
  const [previews, setPreviews] = useState<Record<string, string>>({})

  // 累加文件（去重：同名新文件替换旧文件）
  // ⚠️ 不在 setFiles 回调内调用 setPreviews，避免 React setState 嵌套副作用
  const addFiles = (incoming: FileList | null) => {
    if (!incoming) return
    const incomingArr = Array.from(incoming)
    setFiles(prev => {
      const map = new Map(prev.map(f => [f.name, f]))
      incomingArr.forEach(f => map.set(f.name, f))
      return Array.from(map.values())
    })
    setPreviews(prev => {
      const next = { ...prev }
      incomingArr.forEach(f => {
        if (isImageFile(f.name) && !next[f.name]) {
          next[f.name] = URL.createObjectURL(f)
        }
      })
      return next
    })
  }

  const removeFile = (name: string) => {
    if (previews[name]) URL.revokeObjectURL(previews[name])
    setPreviews(prev => { const p = { ...prev }; delete p[name]; return p })
    setFiles(prev => prev.filter(f => f.name !== name))
  }

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
  const hasImage  = files.some(f => isImageFile(f.name))

  // 应用示例
  const applyExample = (ex: typeof EXAMPLES[0]) => {
    setTitle(ex.title)
    setProblem(ex.problem)
    setShowExamples(false)
  }

  return (
    // 移动端：从底部弹出 (items-end)；桌面：居中显示 (sm:items-center)
    <div className="fixed inset-0 bg-black/40 flex items-end sm:items-center justify-center z-50 p-0 sm:p-4">
      <div className="bg-white w-full sm:max-w-[680px] max-h-[94dvh] sm:max-h-[90vh] flex flex-col
                      rounded-t-2xl sm:rounded-xl shadow-2xl">

        {/* 头部 */}
        <div className="px-5 py-3.5 border-b border-ink-200 flex items-center justify-between shrink-0">
          <h3 className="font-semibold text-sm">新建建模任务</h3>
          <div className="flex items-center gap-2">
            {/* 示例题目按钮 */}
            <button
              type="button"
              onClick={() => setShowExamples(v => !v)}
              className="text-xs px-2.5 py-1 rounded border border-ink-200 text-ink-500 hover:bg-ink-50">
              📚 示例题目
            </button>
            <button onClick={onClose} className="text-ink-400 hover:text-ink-800 text-xl leading-none w-8 h-8 flex items-center justify-center">✕</button>
          </div>
        </div>

        {/* 示例工中 (collapsible) */}
        {showExamples && (
          <div className="shrink-0 border-b border-ink-200 bg-ink-50 px-4 py-3">
            <p className="text-[11px] text-ink-400 mb-2">点击即可自动填入标题与赛题，可再手动修改</p>
            <div className="flex flex-wrap gap-2">
              {EXAMPLES.map(ex => (
                <button
                  key={ex.label}
                  type="button"
                  onClick={() => applyExample(ex)}
                  className="text-xs px-3 py-1.5 rounded-full border border-ink-300 bg-white
                             hover:bg-ink-800 hover:text-white hover:border-ink-800 transition-colors">
                  {ex.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* 正文 */}
        <div className="flex-1 overflow-auto p-4 sm:p-5 space-y-4">

          {/* 标题 */}
          <div>
            <label className="text-xs text-ink-500">任务标题</label>
            <input
              value={title}
              onChange={e => setTitle(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && e.currentTarget.blur()}
              placeholder="如：2024 国赛 A 题 · 板凳龙"
              className="mt-1 w-full px-3 py-2.5 border border-ink-200 rounded-lg text-sm
                         focus:outline-none focus:border-ink-500"
            />
          </div>

          {/* 赛题 */}
          <div>
            <div className="flex items-center justify-between">
              <label className="text-xs text-ink-500">赛题描述（含数据说明、子问题）</label>
              <span className="text-[11px] text-ink-400">{problem.length} 字</span>
            </div>
            <textarea
              value={problem}
              onChange={e => setProblem(e.target.value)}
              rows={8}
              placeholder="粘贴完整赛题，包含问题 1/2/3..."
              className="mt-1 w-full px-3 py-2.5 border border-ink-200 rounded-lg text-sm font-mono
                         focus:outline-none focus:border-ink-500 resize-none leading-relaxed"
            />
          </div>

          {/* 附件区 */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-xs text-ink-500 flex items-center gap-1.5">
                数据附件
                {files.length > 0 && (
                  <span className="text-ink-400">
                    {files.length} 个 &middot; {fmtSize(totalSize)}
                  </span>
                )}
                {hasImage && (
                  <span className="px-1.5 py-0.5 bg-violet-100 text-violet-600 text-[10px] rounded">
                    含图片·可视觉
                  </span>
                )}
              </label>
            </div>

            {/* 隐藏 file input：fixed 定位移出视口 + 1px 尺寸
                - 不用 w-0/h-0：零尺寸时部分 iOS/Android 浏览器不弹 file picker
                - 不用 pointer-events-none：某些浏览器检查此属性判断元素可交互性
                - fixed 脱离 overflow:auto 祖先，避免 iOS 下 scroll 容器拦截 */}
            <input
              ref={fileInputRef}
              type="file" multiple
              accept={FILE_ACCEPT}
              className="fixed opacity-0 w-px h-px -top-40 -left-40"
              tabIndex={-1}
              aria-hidden
              onChange={e => { addFiles(e.target.files); e.target.value = '' }}
            />

            {/* 拍照 input */}
            <input
              ref={cameraInputRef}
              type="file"
              accept="image/*" capture="environment"
              className="fixed opacity-0 w-px h-px -top-40 -left-40"
              tabIndex={-1}
              aria-hidden
              onChange={e => { addFiles(e.target.files); e.target.value = '' }}
            />

            {/* 拖拽区（桌面） */}
            <div
              onDragOver={e => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={onDrop}
              className={clsx(
                'hidden sm:flex items-center justify-center border-2 border-dashed rounded-lg',
                'px-4 py-4 mb-3 transition-colors text-center cursor-pointer',
                dragOver ? 'border-blue-400 bg-blue-50' : 'border-ink-200 bg-ink-50 hover:bg-ink-100/60'
              )}
              onClick={() => fileInputRef.current?.click()}
            >
              <div className="flex flex-col items-center gap-1 text-ink-400 pointer-events-none">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"
                  className="w-7 h-7 opacity-40">
                  <path strokeLinecap="round" strokeLinejoin="round"
                    d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5
                       m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5" />
                </svg>
                <p className="text-xs">拖拽文件到此处 / 点击选择</p>
                <p className="text-[10px] opacity-60">CSV · XLSX · JSON · PNG · PDF · PY …</p>
              </div>
            </div>

            {/* 文件列表 */}
            {files.length > 0 && (
              <ul className="space-y-1.5 mb-3">
                {files.map(f => {
                  const isImg = isImageFile(f.name)
                  const prev  = previews[f.name]
                  return (
                    <li key={f.name}
                      className="flex items-center gap-3 px-3 py-2.5 bg-white rounded-lg
                                 border border-ink-200 text-xs">
                      {isImg && prev
                        ? <img src={prev} alt={f.name}
                            className="w-9 h-9 rounded object-cover shrink-0 border border-ink-100" />
                        : <span className="text-lg leading-none shrink-0 w-9 text-center">{fileIcon(f.name)}</span>
                      }
                      <div className="flex-1 min-w-0">
                        <p className="truncate font-medium text-ink-700">{f.name}</p>
                        <p className="text-[10px] text-ink-400 flex gap-2">
                          <span>{fmtSize(f.size)}</span>
                          {isImg && <span className="text-violet-500">视觉</span>}
                        </p>
                      </div>
                      {/* 显式删除按钮（所有设备均可点） */}
                      <button
                        type="button"
                        onClick={() => removeFile(f.name)}
                        title="删除文件"
                        className="shrink-0 w-8 h-8 flex items-center justify-center
                                   rounded-lg text-ink-400 hover:text-red-500 hover:bg-red-50
                                   transition-colors -mr-1">
                        <svg viewBox="0 0 16 16" fill="currentColor" className="w-4 h-4">
                          <path d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.75.75
                            1 1 1.06 1.06L9.06 8l3.22 3.22a.75.75 0 1 1-1.06
                            1.06L8 9.06l-3.22 3.22a.75.75 0 0 1-1.06-1.06L6.94 8
                            3.72 4.78a.75.75 0 0 1 0-1.06Z" />
                        </svg>
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}

            {/* 文件操作按钮行：使用 button + ref.click()，比 label htmlFor 在移动端更可靠 */}
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="flex-1 flex items-center justify-center gap-1.5 py-3 sm:py-2
                           border border-ink-300 rounded-lg text-xs text-ink-600 bg-white
                           hover:bg-ink-50 active:bg-ink-100 transition-colors font-medium">
                <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 opacity-70">
                  <path d="M3 10a.75.75 0 0 1 .75-.75h4.5v-4.5a.75.75 0 0 1 1.5 0v4.5h4.5a.75.75 0 0 1 0 1.5h-4.5v4.5a.75.75 0 0 1-1.5 0v-4.5h-4.5A.75.75 0 0 1 3 10Z" />
                </svg>
                选择文件
              </button>
              {/* 拍照上传 */}
              <button
                type="button"
                onClick={() => cameraInputRef.current?.click()}
                className="flex items-center justify-center gap-1.5 px-4 py-3 sm:py-2
                           border border-ink-300 rounded-lg text-xs text-ink-600 bg-white
                           hover:bg-ink-50 active:bg-ink-100 transition-colors">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
                  className="w-4 h-4">
                  <path strokeLinecap="round" strokeLinejoin="round"
                    d="M6.827 6.175A2.31 2.31 0 0 1 5.186 7.23c-.38.054-.757.112-1.134.175
                       C2.999 7.58 2.25 8.507 2.25 9.574V18a2.25 2.25 0 0 0 2.25 2.25h15A2.25
                       2.25 0 0 0 21.75 18V9.574c0-1.067-.75-1.994-1.802-2.169a47.865 47.865
                       0 0 0-1.134-.175 2.31 2.31 0 0 1-1.64-1.055l-.822-1.316a2.192 2.192
                       0 0 0-1.736-1.039 48.774 48.774 0 0 0-5.232 0 2.192 2.192 0 0
                       0-1.736 1.039l-.821 1.316Z" />
                  <path strokeLinecap="round" strokeLinejoin="round"
                    d="M16.5 12.75a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0ZM18.75 10.5h.008v.008h-.008V10.5Z" />
                </svg>
                <span className="hidden sm:inline">拍照</span>
                <span className="sm:hidden">拍照上传</span>
              </button>
            </div>
          </div>

          {err && <p className="text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">{err}</p>}
        </div>

        {/* 底部 */}
        <div className="px-4 sm:px-5 py-3.5 border-t border-ink-200 flex items-center justify-between shrink-0 bg-white">
          <p className="text-[11px] text-ink-400">
            {problem.trim().length < 10
              ? `赛题描述至少 10 字`
              : `已添加 ${files.length} 个附件`}
          </p>
          <div className="flex gap-2">
            <button type="button" onClick={onClose}
              className="px-3 py-2 text-sm text-ink-500 hover:text-ink-800">取消</button>
            <button
              type="button"
              onClick={submit}
              disabled={submitting || !title.trim() || problem.trim().length < 10}
              className="px-5 py-2 bg-ink-800 text-white rounded-lg text-sm
                         hover:bg-ink-700 disabled:opacity-40 transition-colors font-medium">
              {submitting ? '创建中…' : '开始建模'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
