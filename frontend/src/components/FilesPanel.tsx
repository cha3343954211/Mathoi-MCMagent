import { useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '../store'
import { api } from '../api'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import clsx from 'clsx'

// ── 文件分类 ──────────────────────────────────────────────────────────────────
const GROUP_ORDER = ['paper', 'report', 'figure', 'script', 'data', 'export', 'other']
const GROUP_LABEL: Record<string, string> = {
  paper: '📄 论文', report: '📋 报告', figure: '🖼 图表',
  script: '🐍 代码', data: '📊 数据', export: '📦 导出', other: '📁 其他',
}

function classify(name: string): string {
  const lower = name.toLowerCase()
  if (lower === 'paper.md') return 'paper'
  if (/^(analysis_report|modeling_plan)\.md$/.test(lower)) return 'report'
  if (/\.(png|jpe?g|svg|webp|gif)$/.test(lower)) return 'figure'
  if (/\.(py|ipynb)$/.test(lower)) return 'script'
  if (/\.(csv|xlsx?|json|txt|tsv)$/.test(lower)) return 'data'
  if (/\.(docx?|pdf)$/.test(lower)) return 'export'
  if (/\.md$/.test(lower)) return 'report'
  return 'other'
}

function fmtSize(bytes: number): string {
  if (bytes < 1024)        return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

// ── 下载链接组件 ──────────────────────────────────────────────────────────────
function DownloadLink({
  url, name, label, title, className,
}: {
  url: string; name: string; label?: string; title?: string; className?: string
}) {
  return (
    <a href={url} download={name} title={title}
      className={clsx(
        'flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 hover:underline transition-colors',
        'px-2 py-1 rounded hover:bg-blue-50',
        className,
      )}>
      <svg viewBox="0 0 16 16" fill="currentColor" className="w-3 h-3 shrink-0">
        <path d="M8.75 2.75a.75.75 0 0 0-1.5 0v5.69L5.03 6.22a.75.75 0 0 0-1.06 1.06l3.5 3.5a.75.75 0 0 0 1.06 0l3.5-3.5a.75.75 0 0 0-1.06-1.06L8.75 8.44V2.75Z" />
        <path d="M3.5 9.75a.75.75 0 0 0-1.5 0v1.5A2.75 2.75 0 0 0 4.75 14h6.5A2.75 2.75 0 0 0 14 11.25v-1.5a.75.75 0 0 0-1.5 0v1.5c0 .69-.56 1.25-1.25 1.25h-6.5c-.69 0-1.25-.56-1.25-1.25v-1.5Z" />
      </svg>
      {label ?? '下载'}
    </a>
  )
}

// ── 主组件 ──────────────────────────────────────────────────────────────────────────────
export function FilesPanel() {
  const { current } = useStore()
  const [files, setFiles]       = useState<{ name: string; size: number }[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [content, setContent]   = useState<string>('')
  const [loading, setLoading]   = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)  // 移动端可折叠
  const [rewriting, setRewriting] = useState(false)
  const [editing, setEditing]     = useState(false)
  const [editContent, setEditContent] = useState('')
  const [saving, setSaving]       = useState(false)
  const prevTaskId = useRef<string>('')

  // 从文件名解析章节 key（sec_abstract.md → abstract，sec_q1.md → q1）
  const sectionKey = (() => {
    if (!selected) return null
    const m = selected.match(/^sec_(.+)\.md$/)
    if (!m) return null
    const k = m[1]   // abstract | restatement | q1 | ...
    return k
  })()

  const onRewrite = async () => {
    if (!current || !sectionKey || rewriting) return
    if (!confirm(`确定重写「${selected}」？当前内容将被新版本替换。`)) return
    setRewriting(true)
    try {
      await api.rewriteSection(current.task_id, sectionKey)
      // 任务变为 running，等 task.completed 事件后自动刷新文件列表
    } catch (e: any) {
      alert('重写失败：' + (e?.message || e))
      setRewriting(false)
    }
  }

  // 重写完成后重新加载文件内容
  const { current: currentTask } = useStore()
  useEffect(() => {
    if (!rewriting) return
    if (currentTask?.state === 'completed' || currentTask?.state === 'failed') {
      setRewriting(false)
      // 重新触发文件内容加载
      if (selected && /\.md$/i.test(selected)) {
        fetch(api.fileUrl(currentTask.task_id, selected))
          .then(r => r.text()).then(setContent).catch(() => {})
      }
    }
  }, [currentTask?.state, rewriting])

  // 切换文件时退出编辑模式
  useEffect(() => { setEditing(false) }, [selected])

  const onStartEdit = () => { setEditContent(content); setEditing(true) }
  const onCancelEdit = () => setEditing(false)
  const onSaveEdit = async () => {
    if (!current || !selected || saving) return
    setSaving(true)
    try {
      await api.writeFile(current.task_id, selected, editContent)
      setContent(editContent)
      setEditing(false)
    } catch (e: any) {
      alert('保存失败：' + (e?.message || e))
    } finally { setSaving(false) }
  }

  // 文件列表刷新
  useEffect(() => {
    if (!current) return
    api.files(current.task_id).then(r => {
      setFiles(r.files)
      const names = new Set(r.files.map(f => f.name))
      const hasPaper = names.has('paper.md')
      const hasPlan  = names.has('modeling_plan.md')
      const isHITL   = current.state === 'awaiting_hitl'

      // 新任务或无已选文件时，智能选择默认文件
      const isNewTask = current.task_id !== prevTaskId.current
      if (isNewTask || !selected) {
        prevTaskId.current = current.task_id
        // HITL 等待中优先展示建模方案；有论文时优先论文；否则不选
        const autoSelect = isHITL && hasPlan ? 'modeling_plan.md'
          : hasPaper ? 'paper.md'
          : null
        setSelected(autoSelect)
        setContent('')
      }
    })
  }, [current?.task_id, current?.updated_at])

  // 文件内容加载
  useEffect(() => {
    if (!current || !selected) { setContent(''); return }
    if (/\.(md|txt|py|json|csv|tsv)$/i.test(selected)) {
      setLoading(true)
      fetch(api.fileUrl(current.task_id, selected))
        .then(r => r.text())
        .then(t => { setContent(t); setLoading(false) })
        .catch(() => { setContent(''); setLoading(false) })
    } else {
      setContent('')
    }
  }, [current?.task_id, selected])

  if (!current) return null

  const grouped = useMemo(() => {
    const map: Record<string, typeof files> = {}
    for (const f of files) {
      const g = classify(f.name)
      ;(map[g] ??= []).push(f)
    }
    return map
  }, [files])

  const isImage = !!selected && /\.(png|jpe?g|gif|svg|webp)$/i.test(selected)
  const isMd    = !!selected && /\.md$/i.test(selected)
  const isPy    = !!selected && /\.py$/i.test(selected)
  const isPaper = selected === 'paper.md'

  return (
    <div className="h-full flex">
      {/* ── 左侧文件树 ── */}
      <div className={clsx(
        'flex-shrink-0 border-r border-ink-200 bg-white flex flex-col transition-all duration-200',
        sidebarOpen ? 'w-48 sm:w-56' : 'w-0 overflow-hidden border-r-0'
      )}>
        {/* 头部：下载按钮 */}
        <div className="px-3 py-2 border-b border-ink-100 shrink-0 flex items-center gap-2">
          <a href={api.archiveUrl(current.task_id)} download={`${current.task_id}.zip`}
            className="flex items-center gap-1 text-xs text-ink-500 hover:text-ink-800 transition-colors"
            title="下载全部文件 ZIP">
            <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5 shrink-0">
              <path d="M8.75 2.75a.75.75 0 0 0-1.5 0v5.69L5.03 6.22a.75.75 0 0 0-1.06 1.06l3.5 3.5a.75.75 0 0 0 1.06 0l3.5-3.5a.75.75 0 0 0-1.06-1.06L8.75 8.44V2.75Z" />
              <path d="M3.5 9.75a.75.75 0 0 0-1.5 0v1.5A2.75 2.75 0 0 0 4.75 14h6.5A2.75 2.75 0 0 0 14 11.25v-1.5a.75.75 0 0 0-1.5 0v1.5c0 .69-.56 1.25-1.25 1.25h-6.5c-.69 0-1.25-.56-1.25-1.25v-1.5Z" />
            </svg>
            <span>ZIP</span>
          </a>
          {/* Notebook 下载（运行中或完成后可用）*/}
          {files.some(f => f.name === 'notebook.ipynb') && (
            <a href={api.notebookUrl(current.task_id)}
              download={`${current.task_id}_notebook.ipynb`}
              className="flex items-center gap-1 text-xs text-amber-600 hover:text-amber-800 transition-colors"
              title="下载 Jupyter Notebook（可复现执行过程）">
              <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5 shrink-0">
                <path fillRule="evenodd" d="M8 1a.75.75 0 0 1 .75.75V6h3.5a.75.75 0 0 1 0 1.5h-3.5v3.5a.75.75 0 0 1-1.5 0V7.5H3.75a.75.75 0 0 1 0-1.5h3.5V1.75A.75.75 0 0 1 8 1Z" clipRule="evenodd" />
                <path d="M2 13.25a.75.75 0 0 1 .75-.75h10.5a.75.75 0 0 1 0 1.5H2.75a.75.75 0 0 1-.75-.75Z" />
              </svg>
              <span>.ipynb</span>
            </a>
          )}
        </div>

        {/* 文件列表 */}
        <div className="flex-1 overflow-auto scrollbar-thin">
          {files.length === 0 ? (
            <p className="text-xs text-ink-400 p-4 text-center">暂无产物文件</p>
          ) : (
            GROUP_ORDER.filter(g => grouped[g]?.length).map(g => (
              <div key={g}>
                <div className="px-3 pt-3 pb-1 text-[10px] font-semibold text-ink-400 uppercase tracking-wide">
                  {GROUP_LABEL[g]}
                </div>
                {grouped[g].map(f => (
                  <button key={f.name}
                    onClick={() => { setSelected(f.name); setSidebarOpen(false) }}
                    className={clsx(
                      'w-full text-left px-3 py-2.5 sm:py-2 text-xs transition-colors flex items-center gap-2',
                      selected === f.name
                        ? 'bg-ink-800 text-white'
                        : 'hover:bg-ink-100 text-ink-700'
                    )}>
                    <span className="truncate flex-1">{f.name}</span>
                    <span className={clsx('text-[10px] shrink-0',
                      selected === f.name ? 'text-ink-300' : 'text-ink-400')}>
                      {fmtSize(f.size)}
                    </span>
                  </button>
                ))}
              </div>
            ))
          )}
        </div>
      </div>

      {/* ── 右侧预览区 ── */}
      <div className="flex-1 overflow-auto scrollbar-thin bg-ink-50 flex flex-col min-w-0">
        {/* 文件列表切换条 */}
        <div className="flex items-center gap-2 px-3 py-2 border-b border-ink-200 bg-white shrink-0">
          <button
            onClick={() => setSidebarOpen(v => !v)}
            title={sidebarOpen ? '隐藏文件列表' : '显示文件列表'}
            className={clsx(
              'flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border transition-colors',
              sidebarOpen
                ? 'bg-ink-800 text-white border-ink-800'
                : 'border-ink-300 text-ink-600 hover:bg-ink-100'
            )}>
            <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5">
              <path d="M2 4.75A.75.75 0 0 1 2.75 4h10.5a.75.75 0 0 1 0 1.5H2.75A.75.75 0 0 1 2 4.75ZM2 8a.75.75 0 0 1 .75-.75h10.5a.75.75 0 0 1 0 1.5H2.75A.75.75 0 0 1 2 8Zm0 3.25a.75.75 0 0 1 .75-.75h10.5a.75.75 0 0 1 0 1.5H2.75a.75.75 0 0 1-.75-.75Z" />
            </svg>
            {sidebarOpen ? '隐藏列表' : `文件列表${files.length > 0 ? ` (${files.length})` : ''}`}
          </button>
          {selected && (
            <span className="text-xs text-ink-500 truncate flex-1">{selected}</span>
          )}
          {/* 编辑 / 重写按钮：仅对 .md 且任务非运行中时显示 */}
          {selected && /\.md$/i.test(selected) && current.state !== 'running' && (
            <>
              {editing ? (
                <>
                  <button onClick={onCancelEdit}
                    className="text-xs px-2.5 py-1 rounded border border-ink-200 text-ink-600 hover:bg-ink-50 shrink-0">
                    取消
                  </button>
                  <button onClick={onSaveEdit} disabled={saving}
                    className="text-xs px-3 py-1 rounded bg-ink-800 text-white hover:bg-ink-700 disabled:opacity-50 shrink-0">
                    {saving ? '保存中…' : '保存'}
                  </button>
                </>
              ) : (
                <button onClick={onStartEdit}
                  className="text-xs px-2.5 py-1 rounded border border-ink-200 text-ink-600 hover:bg-ink-50 shrink-0">
                  ✎ 编辑
                </button>
              )}
              {sectionKey && !editing && (
                <button onClick={onRewrite} disabled={rewriting}
                  title="重新用 AI 撰写此节"
                  className="text-xs px-2.5 py-1 rounded border border-violet-200 text-violet-700 hover:bg-violet-50 disabled:opacity-50 shrink-0">
                  {rewriting ? <><span className="animate-spin inline-block">⟳</span> 重写中…</> : <>✦ AI 重写</>}
                </button>
              )}
            </>
          )}
        </div>

        <div className="flex-1 overflow-auto">
        {!selected && (
          <div className="h-full flex flex-col items-center justify-center text-ink-400 gap-2 py-12">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2"
              className="w-12 h-12 opacity-20">
              <path strokeLinecap="round" strokeLinejoin="round"
                d="M9 12h6m-6 4h6m2 5H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5.586a1 1 0 0 1 .707.293l5.414 5.414a1 1 0 0 1 .293.707V19a2 2 0 0 1-2 2z" />
            </svg>
            <p className="text-xs">点击「文件列表」选择文件</p>
          </div>
        )}

        {selected && (
          <div className={clsx('min-h-full', isPaper ? 'p-0' : 'p-5')}>
            {/* 编辑模式：全屏 textarea */}
            {editing && isMd && (
              <textarea
                value={editContent}
                onChange={e => setEditContent(e.target.value)}
                spellCheck={false}
                className="w-full h-full min-h-[600px] font-mono text-[12px] leading-relaxed p-4 resize-none focus:outline-none bg-white border-0"
                placeholder="Markdown 内容…"
              />
            )}
            {/* Markdown 预览 */}
            {isMd && !editing && (
              <MdViewer
                content={content}
                loading={loading}
                taskId={current.task_id}
                isPaper={isPaper}
                fileName={selected}
                fileUrl={api.fileUrl(current.task_id, selected)}
                pdfUrl={isPaper ? api.exportPdfUrl(current.task_id) : undefined}
                docxUrl={isPaper ? api.exportDocxUrl(current.task_id) : undefined}
              />
            )}

            {/* 图片预览 */}
            {isImage && (
              <div className="flex flex-col items-center gap-3">
                <img
                  src={api.fileUrl(current.task_id, selected)}
                  alt={selected}
                  className="max-w-full rounded-lg border border-ink-200 shadow-sm"
                />
                <DownloadLink url={api.fileUrl(current.task_id, selected)} name={selected} />
              </div>
            )}

            {/* 文本 / 代码预览 */}
            {!isImage && !isMd && (
              <div>
                {/* 工具栏 */}
                <div className="flex items-center gap-2 mb-3">
                  <DownloadLink url={api.fileUrl(current.task_id, selected)} name={selected} />
                  {isPy && (
                    <DownloadLink
                      url={api.exportIpynbUrl(current.task_id, selected)}
                      name={selected.replace(/\.py$/, '.ipynb')}
                      label="Jupyter (.ipynb)"
                      title="将 .py 脚本转为 Jupyter Notebook 格式下载"
                    />
                  )}
                </div>
                {loading ? (
                  <p className="text-xs text-ink-400">加载中…</p>
                ) : content ? (
                  <pre className="text-xs bg-white rounded-lg border border-ink-200 p-4 whitespace-pre-wrap overflow-x-auto max-w-full font-mono leading-relaxed">
                    {content}
                  </pre>
                ) : (
                  <p className="text-xs text-ink-400">二进制文件，无法预览</p>
                )}
              </div>
            )}
          </div>
        )}
        </div>{/* flex-1 overflow-auto */}
      </div>{/* 右侧预览区 */}
    </div>
  )
}

// ── Markdown / 论文查看器 ─────────────────────────────────────────────────────
function MdViewer({
  content, loading, taskId, isPaper, fileName, fileUrl, pdfUrl, docxUrl,
}: {
  content: string; loading: boolean; taskId: string
  isPaper: boolean; fileName: string; fileUrl: string; pdfUrl?: string; docxUrl?: string
}) {
  const components = useMemo(() => ({
    img({ src, alt }: { src?: string; alt?: string }) {
      if (!src) return null
      const resolved = /^https?:\/\//.test(src)
        ? src
        : api.fileUrl(taskId, src.replace(/^\.\//, ''))
      return (
        <span className="block my-4 text-center">
          <img src={resolved} alt={alt ?? ''}
            className="inline-block max-w-full rounded-lg border border-ink-200 shadow-sm" />
          {alt && <span className="block mt-1.5 text-xs text-ink-500 italic">{alt}</span>}
        </span>
      )
    },
    pre({ children, ...props }: any) {
      return (
        <pre {...props}
          style={{ maxWidth: '100%', overflowX: 'auto', whiteSpace: 'pre' }}
          className="my-3 bg-gray-900 text-gray-100 rounded-lg p-4 text-xs font-mono leading-relaxed">
          {children}
        </pre>
      )
    },
    code({ inline, children, ...props }: any) {
      if (inline) {
        return (
          <code {...props} className="px-1 py-0.5 bg-ink-100 text-ink-700 rounded text-[0.85em] font-mono">
            {children}
          </code>
        )
      }
      return <code {...props}>{children}</code>
    },
    p({ children, ...props }: any) {
      return <p className="my-3 leading-relaxed" {...props}>{children}</p>
    },
  }), [taskId])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-32 text-xs text-ink-400">
        <span className="animate-pulse">加载中…</span>
      </div>
    )
  }

  if (!content) {
    return <p className="text-xs text-ink-400 p-5">（文件为空）</p>
  }

  return (
    <div className={clsx(isPaper && 'bg-white')}>
      {/* 顶部工具栏 */}
      <div className={clsx(
        'sticky top-0 z-10 flex items-center justify-between px-5 py-2 border-b text-xs',
        isPaper ? 'bg-white border-ink-200' : 'bg-ink-50 border-ink-200',
      )}>
        <span className="font-medium text-ink-600">{fileName}</span>
        <div className="flex items-center gap-1">
          <span className="text-ink-400 text-[11px] mr-2">{content.length.toLocaleString()} 字符</span>
          <DownloadLink url={fileUrl} name={fileName} label="MD" />
          {docxUrl && (
            <DownloadLink
              url={docxUrl}
              name="paper.docx"
              label="DOCX"
              title="导出 Word 文档（pandoc 优先，降级 python-docx）"
            />
          )}
          {pdfUrl && (
            <DownloadLink
              url={pdfUrl}
              name="paper.pdf"
              label="PDF"
              title="通过 pandoc 导出 PDF（需服务器已安装 pandoc + xelatex/wkhtmltopdf）"
            />
          )}
        </div>
      </div>

      {/* 正文 */}
      <div className={clsx(
        'markdown-body min-w-0 overflow-hidden',
        isPaper
          ? 'px-12 py-10 max-w-4xl mx-auto'
          : 'px-6 py-6 bg-white rounded-lg border border-ink-200 m-5',
      )}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkMath]}
          rehypePlugins={[rehypeKatex]}
          components={components as any}
        >
          {content}
        </ReactMarkdown>
      </div>
    </div>
  )
}
