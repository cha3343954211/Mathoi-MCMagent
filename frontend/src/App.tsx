import { useEffect, useRef, useState } from 'react'
import { useStore } from './store'
import { TaskList } from './components/TaskList'
import { CreateTask } from './components/CreateTask'
import { TraceTimeline } from './components/TraceTimeline'
import { HITLPanel } from './components/HITLPanel'
import { FilesPanel } from './components/FilesPanel'
import { ModelConfig } from './components/ModelConfig'
import { AuthPage } from './components/AuthPage'
import { AdminPanel } from './components/AdminPanel'
import { api, WsStatus } from './api'
import clsx from 'clsx'

type Tab = 'trace' | 'files'

export default function App() {
  const { user, authReady, current, bootstrap, logout, removeTask, wsStatus, interruptCurrent } = useStore()
  const [tab, setTab]               = useState<Tab>('trace')
  const [showCreate, setShowCreate]  = useState(false)
  const [showModels, setShowModels]  = useState(false)
  const [showAdmin, setShowAdmin]    = useState(false)
  const [showUserMenu, setShowUserMenu] = useState(false)
  const [showChangePwd, setShowChangePwd] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting]      = useState(false)
  const [collapsed, setCollapsed]    = useState(false)
  const [mobileOpen, setMobileOpen]   = useState(false)  // 移动端抽屉
  const [interruptMsg, setInterruptMsg] = useState('')
  const [runSecs, setRunSecs]         = useState(0)

  // 运行计时器——任务处于 running 时开始计时
  useEffect(() => {
    if (current?.state === 'running') {
      const start = current.updated_at ? current.updated_at * 1000 : Date.now()
      const id = setInterval(() => setRunSecs(Math.floor((Date.now() - start) / 1000)), 1000)
      return () => clearInterval(id)
    } else {
      setRunSecs(0)
    }
  }, [current?.state, current?.task_id])

  const fmtDuration = (s: number) => {
    if (s < 60) return `${s}s`
    if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`
    return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
  }

  // 切换任务后自动关闭移动端抽屉
  useEffect(() => { setMobileOpen(false) }, [current?.task_id])

  useEffect(() => { bootstrap() }, [])

  // 关闭用户菜单（点击外部）
  // 任务完成时自动跳到产物Tab
  const prevState = useRef<string>('')
  useEffect(() => {
    if (!current) return
    if (current.state === 'completed' && prevState.current !== 'completed') {
      setTab('files')
    }
    prevState.current = current.state
  }, [current?.state])

  // 检测当前任务是否有 paper.md（通过事件流中 task.completed 后判断）
  const hasPaper = current?.state === 'completed'

  const menuRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!showUserMenu) return
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node))
        setShowUserMenu(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showUserMenu])

  const doDeleteCurrent = async () => {
    if (!current) return
    setDeleting(true)
    try { await removeTask(current.task_id) }
    finally { setDeleting(false); setConfirmDelete(false) }
  }

  if (!authReady) {
    return <div className="min-h-screen flex items-center justify-center text-ink-400 text-sm">加载中…</div>
  }
  if (!user) return <AuthPage />

  return (
    <div className="h-full flex">
      {/* ======== 移动端遗罩层 ======== */}
      {mobileOpen && (
        <div
          className="fixed inset-0 bg-black/30 z-30 sm:hidden"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* ======== 侧边栏 ======== */}
      <aside className={clsx(
        'bg-white border-r border-ink-200 flex flex-col transition-all duration-200 ease-in-out overflow-hidden',
        // 桌面：正常流布局
        'relative hidden sm:flex',
        collapsed ? 'sm:w-12' : 'sm:w-72',
        // 移动端：绝对定位抽屉，z-40 覆盖這罩
        'sm:!flex fixed sm:static inset-y-0 left-0 z-40 w-72',
        mobileOpen ? 'flex' : 'hidden sm:flex'
      )}>

        {collapsed ? (
          /* ── 收起状态：图标列 ── */
          <div className="flex flex-col items-center py-2 gap-2 h-full">
            {/* 展开按钮 */}
            <button onClick={() => setCollapsed(false)} title="展开侧边栏"
              className="w-8 h-8 flex items-center justify-center rounded-lg text-ink-500 hover:bg-ink-100 transition-colors mt-1">
              <SidebarOpenIcon />
            </button>
            <div className="w-6 border-t border-ink-200" />
            {/* 新建 */}
            <button onClick={() => setShowCreate(true)} title="新建任务"
              className="w-8 h-8 flex items-center justify-center rounded-lg bg-ink-800 text-white hover:bg-ink-700 transition-colors text-base leading-none">
              +
            </button>
            <div className="flex-1" />
            {/* 用户头像 */}
            <button onClick={() => { setCollapsed(false); setShowUserMenu(true) }} title={user.username}
              className="w-8 h-8 rounded-full bg-ink-800 text-white flex items-center justify-center text-sm font-semibold mb-2">
              {user.username[0].toUpperCase()}
            </button>
          </div>
        ) : (
          /* ── 展开状态：完整侧边栏 ── */
          <>
            {/* Logo 区 */}
            <div className="px-4 py-3 border-b border-ink-200 flex items-center justify-between">
              <div className="min-w-0">
                <h1 className="text-base font-semibold tracking-tight">MathoiAgent</h1>
                <p className="text-[11px] text-ink-400 mt-0.5 whitespace-nowrap">数学建模 AI 工作台</p>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                {user.role === 'admin' && (
                  <IconBtn onClick={() => setShowAdmin(true)} title="后台管理">
                    <ShieldIcon />
                  </IconBtn>
                )}
                <IconBtn onClick={() => setShowModels(true)} title="模型配置">
                  <GearIcon />
                </IconBtn>
                {/* 移动端：关闭抽屉；桌面：收起侧边栏 */}
                <IconBtn
                  onClick={() => { setCollapsed(true); setMobileOpen(false) }}
                  title="收起侧边栏">
                  <SidebarCloseIcon />
                </IconBtn>
              </div>
            </div>

            {/* 新建任务 */}
            <div className="px-3 pt-3 pb-1">
              <button onClick={() => setShowCreate(true)}
                className="w-full flex items-center justify-center gap-1.5 px-3 py-2 bg-ink-800 text-white rounded-lg text-sm hover:bg-ink-700 transition-colors">
                <span className="text-base leading-none">+</span>
                <span>新建任务</span>
              </button>
            </div>

            {/* 任务列表 */}
            <TaskList />

            {/* 用户区 */}
            <div className="border-t border-ink-200 p-3 relative" ref={menuRef}>
              <button onClick={() => setShowUserMenu(v => !v)}
                className="w-full flex items-center gap-2.5 hover:bg-ink-100 rounded-lg p-2 text-left transition-colors">
                <div className="w-8 h-8 rounded-full bg-ink-800 text-white flex items-center justify-center text-sm font-semibold flex-shrink-0">
                  {user.username[0].toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{user.username}</p>
                  <p className="text-[10px] text-ink-400 truncate">
                    {user.role === 'admin' ? '管理员' : '用户'} · {user.email}
                  </p>
                </div>
                <ChevronIcon up={showUserMenu} />
              </button>
              {showUserMenu && (
                <div className="absolute bottom-[60px] left-3 right-3 bg-white border border-ink-200 rounded-lg shadow-lg text-xs overflow-hidden z-10">
                  <button onClick={() => { setShowUserMenu(false); setShowChangePwd(true) }}
                    className="w-full text-left px-3 py-2.5 hover:bg-ink-50 flex items-center gap-2">
                    <KeyIcon /> 修改密码
                  </button>
                  <div className="border-t border-ink-100" />
                  <button onClick={() => { setShowUserMenu(false); logout() }}
                    className="w-full text-left px-3 py-2.5 hover:bg-red-50 text-red-600 flex items-center gap-2">
                    <LogoutIcon /> 注销登录
                  </button>
                </div>
              )}
            </div>
          </>
        )}
      </aside>

      {/* ======== 主内容 ======== */}
      <main className="flex-1 flex flex-col bg-white min-w-0">
        {/* 移动端顶部 header（桌面隐藏） */}
        <div className="sm:hidden flex items-center gap-3 px-4 py-2.5 border-b border-ink-200 bg-white shrink-0 sticky top-0 z-10">
          <button
            onClick={() => setMobileOpen(true)}
            className="w-9 h-9 flex items-center justify-center rounded-lg text-ink-600 hover:bg-ink-100"
            aria-label="打开侧边栏">
            <svg viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
              <path fillRule="evenodd" d="M2 4.75A.75.75 0 0 1 2.75 4h14.5a.75.75 0 0 1 0 1.5H2.75A.75.75 0 0 1 2 4.75ZM2 10a.75.75 0 0 1 .75-.75h14.5a.75.75 0 0 1 0 1.5H2.75A.75.75 0 0 1 2 10Zm0 5.25a.75.75 0 0 1 .75-.75h14.5a.75.75 0 0 1 0 1.5H2.75a.75.75 0 0 1-.75-.75Z" clipRule="evenodd" />
            </svg>
          </button>
          <h1 className="text-sm font-semibold tracking-tight flex-1 truncate">
            {current ? current.title : 'MathoiAgent'}
          </h1>
          <button onClick={() => setShowCreate(true)}
            className="w-9 h-9 flex items-center justify-center rounded-lg bg-ink-800 text-white text-lg leading-none">
            +
          </button>
        </div>

        {!current ? (
          <Empty onNew={() => setShowCreate(true)} />
        ) : (
          <>
            {/* 任务头部 */}
            <header className="px-3 sm:px-5 py-2 sm:py-3 border-b border-ink-200 flex items-center gap-2 sm:gap-3 min-w-0">
              {/* 标题区：桌面可见，移动端已有 sticky header 显示 */}
              <div className="flex-1 min-w-0 hidden sm:block">
                <div className="flex items-center gap-2">
                  <h2 className="text-base font-semibold truncate">{current.title}</h2>
                  <StateBadge state={current.state} />
                </div>
                <p className="text-[11px] text-ink-400 mt-0.5 font-mono truncate">
                  #{current.task_id}
                  {current.phase ? ` · ${current.phase}` : ''}
                </p>
              </div>
              {/* 移动端也需 flex-1 占位使按钮靠右 */}
              <div className="flex-1 sm:hidden" />

              {/* 操作按钮 */}
              <div className="flex items-center gap-1.5 shrink-0">
                {/* WS 连接状态 */}
                {current && <WsBadge status={wsStatus} />}
                {/* 运行计时 */}
                {current?.state === 'running' && runSecs > 0 && (
                  <span className="text-[11px] text-ink-400 font-mono">{fmtDuration(runSecs)}</span>
                )}
                {/* 中断 Kernel（仅运行中，停止死循环） */}
                {current?.state === 'running' && (
                  <button
                    title="中断当前代码执行（停止死循环）"
                    onClick={async () => {
                      const msg = await interruptCurrent()
                      setInterruptMsg(msg)
                      setTimeout(() => setInterruptMsg(''), 3000)
                    }}
                    className="text-xs px-2.5 py-1.5 text-orange-600 hover:bg-orange-50 rounded-lg transition-colors border border-orange-200">
                    ■ 中断
                  </button>
                )}
                {/* 取消（仅运行中/暂停/等待时） */}
                {['running', 'paused', 'awaiting_hitl', 'pending'].includes(current.state) && (
                  <button onClick={async () => {
                    try { await api.cancel(current.task_id) } catch {}
                    setTimeout(() => useStore.getState().refreshCurrent(), 400)
                  }}
                    className="text-xs px-2.5 py-1.5 text-red-600 hover:bg-red-50 rounded-lg transition-colors border border-red-200">
                    取消运行
                  </button>
                )}
                {/* 中断提示 */}
                {interruptMsg && (
                  <span className="text-[11px] text-orange-600 bg-orange-50 px-2 py-1 rounded">{interruptMsg}</span>
                )}
                {/* 删除任务 */}
                {!confirmDelete ? (
                  <button onClick={() => setConfirmDelete(true)}
                    title="删除任务（含工作区文件）"
                    className="text-xs px-2.5 py-1.5 text-ink-500 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors border border-ink-200 flex items-center gap-1">
                    <TrashIcon sm /> 删除
                  </button>
                ) : (
                  <div className="flex items-center gap-1">
                    <span className="text-xs text-red-600">确认删除？</span>
                    <button onClick={() => setConfirmDelete(false)}
                      className="text-xs px-2 py-1 text-ink-500 hover:bg-ink-100 rounded">取消</button>
                    <button onClick={doDeleteCurrent} disabled={deleting}
                      className="text-xs px-2.5 py-1 bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50">
                      {deleting ? '…' : '删除'}
                    </button>
                  </div>
                )}
                <Tabs tab={tab} setTab={setTab} />
              </div>
            </header>

            <section className="flex-1 overflow-hidden">
              {tab === 'trace' && <TraceTimeline />}
              {tab === 'files' && <FilesPanel />}
            </section>
          </>
        )}
      </main>

      {current && <HITLPanel />}

      {/* ======== 弹窗 ======== */}
      {showCreate && <CreateTask onClose={() => setShowCreate(false)} />}
      {showModels && (
        <ModelConfigModal onClose={() => setShowModels(false)} isAdmin={user.role === 'admin'} />
      )}
      {showAdmin && user.role === 'admin' && <AdminPanel onClose={() => setShowAdmin(false)} />}
      {showChangePwd && <ChangePwdModal onClose={() => setShowChangePwd(false)} />}
    </div>
  )
}

// ----------------------------------------------------------------
// 空状态
// ----------------------------------------------------------------
function Empty({ onNew }: { onNew: () => void }) {
  return (
    <div className="h-full flex flex-col items-center justify-center text-ink-400 gap-3">
      <svg viewBox="0 0 64 64" fill="none" stroke="currentColor" strokeWidth="1.5"
        className="w-16 h-16 opacity-20">
        <rect x="8" y="8" width="48" height="48" rx="6" />
        <path strokeLinecap="round" d="M20 24h24M20 32h16M20 40h10" />
      </svg>
      <div className="text-center">
        <p className="text-sm font-medium text-ink-600">选择或新建任务</p>
        <p className="text-xs text-ink-400 mt-1">左侧列表选择已有任务，或点击下方新建</p>
      </div>
      <button onClick={onNew}
        className="mt-1 px-4 py-2 bg-ink-800 text-white text-sm rounded-lg hover:bg-ink-700 transition-colors">
        新建任务
      </button>
    </div>
  )
}

// ----------------------------------------------------------------
// Tab 导航
// ----------------------------------------------------------------
function Tabs({ tab, setTab }: { tab: string; setTab: (t: any) => void }) {
  const items: [string, string][] = [['trace', '追踪'], ['files', '产物']]
  return (
    <div className="flex bg-ink-100 rounded-lg p-0.5 text-xs">
      {items.map(([k, v]) => (
        <button key={k} onClick={() => setTab(k)}
          className={clsx('px-3 py-1.5 rounded-md transition-colors',
            tab === k ? 'bg-white shadow-sm font-medium' : 'text-ink-500 hover:text-ink-700')}>
          {v}
        </button>
      ))}
    </div>
  )
}

// ----------------------------------------------------------------
// StateBadge
// ----------------------------------------------------------------
function StateBadge({ state }: { state: string }) {
  const map: Record<string, string> = {
    running:       'bg-blue-100 text-blue-700',
    paused:        'bg-yellow-100 text-yellow-700',
    awaiting_hitl: 'bg-orange-100 text-orange-700',
    completed:     'bg-emerald-100 text-emerald-700',
    failed:        'bg-red-100 text-red-700',
    cancelled:     'bg-ink-200 text-ink-600',
    pending:       'bg-ink-100 text-ink-500',
  }
  const label: Record<string, string> = {
    running: '运行中', paused: '暂停', awaiting_hitl: '等待确认',
    completed: '已完成', failed: '失败', cancelled: '已取消', pending: '待处理',
  }
  return (
    <span className={clsx('px-1.5 py-0.5 rounded text-[10px] font-medium shrink-0',
      map[state] || 'bg-ink-100 text-ink-500')}>
      {label[state] || state}
    </span>
  )
}

// ----------------------------------------------------------------
// WsBadge —— WebSocket 连接状态指示
// ----------------------------------------------------------------
function WsBadge({ status }: { status: WsStatus }) {
  const cfg: Record<WsStatus, { dot: string; text: string }> = {
    connected:    { dot: 'bg-emerald-400', text: '已连接' },
    connecting:   { dot: 'bg-blue-400 animate-pulse', text: '连接中' },
    reconnecting: { dot: 'bg-yellow-400 animate-pulse', text: '重连中' },
    closed:       { dot: 'bg-ink-300', text: '已断开' },
  }
  const { dot, text } = cfg[status]
  return (
    <span className="flex items-center gap-1 text-[10px] text-ink-500" title={`实时连接：${text}`}>
      <span className={clsx('w-1.5 h-1.5 rounded-full', dot)} />
      {text}
    </span>
  )
}

// ----------------------------------------------------------------
// ModelConfig 弹窗 —— 用户和管理员都可编辑自己的配置
// ----------------------------------------------------------------
function ModelConfigModal({ onClose, isAdmin }: { onClose: () => void; isAdmin: boolean }) {
  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-2xl w-[600px] max-h-[85vh] flex flex-col">
        <div className="px-5 py-3 border-b border-ink-200 flex items-center justify-between">
          <h3 className="font-semibold text-sm">模型配置</h3>
          <button onClick={onClose} className="text-ink-400 hover:text-ink-800 text-lg leading-none">✕</button>
        </div>
        <div className="flex-1 overflow-auto p-5">
          {/* 所有用户都可以编辑自己的配置 */}
          <ModelConfig />
        </div>
      </div>
    </div>
  )
}

// ----------------------------------------------------------------
// 修改密码弹窗
// ----------------------------------------------------------------
function ChangePwdModal({ onClose }: { onClose: () => void }) {
  const [oldp, setOldp] = useState('')
  const [newp, setNewp] = useState('')
  const [newp2, setNewp2] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [ok, setOk] = useState(false)

  const submit = async () => {
    setErr('')
    if (newp.length < 6) { setErr('新密码至少 6 位'); return }
    if (newp !== newp2)  { setErr('两次输入不一致'); return }
    setLoading(true)
    try {
      await api.changePassword(oldp, newp)
      setOk(true)
      setTimeout(onClose, 1200)
    } catch (e: any) {
      setErr(e?.message || '修改失败')
    } finally { setLoading(false) }
  }

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-2xl w-[360px]">
        <div className="px-5 py-3 border-b border-ink-200 flex items-center justify-between">
          <h3 className="font-semibold text-sm">修改密码</h3>
          <button onClick={onClose} className="text-ink-400 hover:text-ink-800">✕</button>
        </div>
        <div className="p-5 space-y-3 text-xs">
          {ok ? (
            <p className="text-center text-emerald-600 py-4">✓ 密码已修改</p>
          ) : (
            <>
              <label className="block">
                <span className="text-ink-500">原密码</span>
                <input type="password" value={oldp} onChange={e => setOldp(e.target.value)}
                  className="mt-1 w-full px-3 py-2 border border-ink-200 rounded focus:outline-none focus:border-ink-500" />
              </label>
              <label className="block">
                <span className="text-ink-500">新密码（至少 6 位）</span>
                <input type="password" value={newp} onChange={e => setNewp(e.target.value)}
                  className="mt-1 w-full px-3 py-2 border border-ink-200 rounded focus:outline-none focus:border-ink-500" />
              </label>
              <label className="block">
                <span className="text-ink-500">确认新密码</span>
                <input type="password" value={newp2} onChange={e => setNewp2(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && submit()}
                  className="mt-1 w-full px-3 py-2 border border-ink-200 rounded focus:outline-none focus:border-ink-500" />
              </label>
              {err && <p className="text-red-600">{err}</p>}
              <div className="flex justify-end gap-2 pt-1">
                <button onClick={onClose} className="px-3 py-1.5 text-ink-500 hover:text-ink-800">取消</button>
                <button onClick={submit} disabled={loading || !oldp || !newp}
                  className="px-4 py-1.5 bg-ink-800 text-white rounded hover:bg-ink-700 disabled:opacity-40">
                  {loading ? '修改中…' : '确认修改'}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ----------------------------------------------------------------
// 图标组件
// ----------------------------------------------------------------
function IconBtn({ onClick, title, children }: { onClick: () => void; title?: string; children: React.ReactNode }) {
  return (
    <button onClick={onClick} title={title}
      className="w-7 h-7 flex items-center justify-center rounded-lg text-ink-500 hover:text-ink-800 hover:bg-ink-100 transition-colors">
      {children}
    </button>
  )
}
function ShieldIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" className="w-4 h-4">
      <path fillRule="evenodd" d="M8 1a.75.75 0 0 1 .374.099l5.25 3.045A.75.75 0 0 1 14 4.793V8c0 2.565-1.9 4.83-4.458 5.738a.75.75 0 0 1-.084.025.748.748 0 0 1-.458 0 .75.75 0 0 1-.084-.025C6.9 12.83 5 10.565 5 8V4.793a.75.75 0 0 1 .376-.649L8 1Zm-.75 8.28 3.22-3.22-.53-.53L7.5 8.47 6.31 7.28l-.53.53 1.47 1.47Z" clipRule="evenodd" />
    </svg>
  )
}
function GearIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" className="w-4 h-4">
      <path fillRule="evenodd" d="M6.5 1.75a.75.75 0 0 1 .75-.75h1.5a.75.75 0 0 1 .75.75V2.5h.546a.25.25 0 0 1 .177.073l1.327 1.327a.25.25 0 0 1 .073.177V5.25h.75a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-.75.75h-.75v1.173a.25.25 0 0 1-.073.177L10.223 10.927a.25.25 0 0 1-.177.073H9.5v.75a.75.75 0 0 1-.75.75h-1.5a.75.75 0 0 1-.75-.75V11h-.546a.25.25 0 0 1-.177-.073L4.45 9.6a.25.25 0 0 1-.073-.177V8H3.627a.75.75 0 0 1-.75-.75v-1.5a.75.75 0 0 1 .75-.75H4.377V3.827a.25.25 0 0 1 .073-.177L5.777 2.323A.25.25 0 0 1 5.954 2.25H6.5V1.75ZM8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5Z" clipRule="evenodd" />
    </svg>
  )
}
function ChevronIcon({ up }: { up: boolean }) {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor"
      className={clsx('w-3.5 h-3.5 text-ink-400 transition-transform', up && 'rotate-180')}>
      <path fillRule="evenodd" d="M4.22 6.22a.75.75 0 0 1 1.06 0L8 8.94l2.72-2.72a.75.75 0 1 1 1.06 1.06l-3.25 3.25a.75.75 0 0 1-1.06 0L4.22 7.28a.75.75 0 0 1 0-1.06Z" clipRule="evenodd" />
    </svg>
  )
}
function KeyIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5 text-ink-400">
      <path fillRule="evenodd" d="M5 4a3 3 0 1 0 0 6 3 3 0 0 0 0-6ZM.5 7A4.5 4.5 0 0 1 9.212 4.5h4.038a.75.75 0 0 1 .53.22l1.5 1.5a.75.75 0 0 1 0 1.06l-1.5 1.5a.75.75 0 0 1-1.06 0L12 8.56l-.72.72a.75.75 0 0 1-1.06 0l-.5-.5-.5.5a.75.75 0 0 1-.53.22H9.21A4.5 4.5 0 0 1 .5 7ZM5 6.5a.5.5 0 1 1 0 1 .5.5 0 0 1 0-1Z" clipRule="evenodd" />
    </svg>
  )
}
function LogoutIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5">
      <path fillRule="evenodd" d="M2 2.75C2 1.784 2.784 1 3.75 1h5.5c.966 0 1.75.784 1.75 1.75v2.5a.75.75 0 0 1-1.5 0v-2.5a.25.25 0 0 0-.25-.25h-5.5a.25.25 0 0 0-.25.25v10.5c0 .138.112.25.25.25h5.5a.25.25 0 0 0 .25-.25v-2.5a.75.75 0 0 1 1.5 0v2.5A1.75 1.75 0 0 1 9.25 15h-5.5A1.75 1.75 0 0 1 2 13.25Zm9.47.47a.75.75 0 0 1 1.06 0l3.25 3.25a.75.75 0 0 1 0 1.06l-3.25 3.25a.75.75 0 1 1-1.06-1.06L13.69 8 11.47 5.78a.75.75 0 0 1 0-1.06Z" clipRule="evenodd" />
      <path fillRule="evenodd" d="M5.75 7.25a.75.75 0 0 0 0 1.5h8.5a.75.75 0 0 0 0-1.5h-8.5Z" clipRule="evenodd" />
    </svg>
  )
}
function SidebarCloseIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" className="w-4 h-4">
      <path fillRule="evenodd" d="M2.75 2a.75.75 0 0 0-.75.75v10.5c0 .414.336.75.75.75H5a.75.75 0 0 0 0-1.5H3.5v-9H5A.75.75 0 0 0 5 2H2.75ZM8.03 5.22a.75.75 0 0 1 0 1.06L7.06 7.25H12a.75.75 0 0 1 0 1.5H7.06l.97.97a.75.75 0 1 1-1.06 1.06l-2.25-2.25a.75.75 0 0 1 0-1.06l2.25-2.25a.75.75 0 0 1 1.06 0Z" clipRule="evenodd" />
    </svg>
  )
}
function SidebarOpenIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" className="w-4 h-4">
      <path fillRule="evenodd" d="M2.75 2a.75.75 0 0 0-.75.75v10.5c0 .414.336.75.75.75H5a.75.75 0 0 0 0-1.5H3.5v-9H5A.75.75 0 0 0 5 2H2.75ZM9.47 5.22a.75.75 0 0 0 0 1.06L10.44 7.25H6a.75.75 0 0 0 0 1.5h4.44l-.97.97a.75.75 0 1 0 1.06 1.06l2.25-2.25a.75.75 0 0 0 0-1.06L10.53 5.22a.75.75 0 0 0-1.06 0Z" clipRule="evenodd" />
    </svg>
  )
}
function TrashIcon({ sm }: { sm?: boolean }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor"
      className={sm ? 'w-3 h-3' : 'w-3.5 h-3.5'}>
      <path fillRule="evenodd" d="M5 3.25V4H2.75a.75.75 0 0 0 0 1.5h.3l.815 8.15A1.5 1.5 0 0 0 5.357 15h5.285a1.5 1.5 0 0 0 1.493-1.35l.815-8.15h.3a.75.75 0 0 0 0-1.5H11v-.75A2.25 2.25 0 0 0 8.75 1h-1.5A2.25 2.25 0 0 0 5 3.25Zm2.25-.75a.75.75 0 0 0-.75.75V4h3v-.75a.75.75 0 0 0-.75-.75h-1.5ZM6.05 6a.75.75 0 0 1 .787.713l.275 5.5a.75.75 0 0 1-1.498.075l-.275-5.5A.75.75 0 0 1 6.05 6Zm3.9 0a.75.75 0 0 1 .712.787l-.275 5.5a.75.75 0 0 1-1.498-.075l.275-5.5a.75.75 0 0 1 .786-.711Z" clipRule="evenodd" />
    </svg>
  )
}
