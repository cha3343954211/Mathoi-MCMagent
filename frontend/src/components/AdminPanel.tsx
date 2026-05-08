import { useEffect, useState } from 'react'
import { api, AdminTask, AdminUser, ModelUsage, Overview, SearchConfig, Stats, SystemSettings, TaskFileStat, UserFileStat, UserUsage } from '../api'
import { AdminModelConfig } from './ModelConfig'

type Tab = 'overview' | 'users' | 'tasks' | 'models' | 'usage' | 'files' | 'settings' | 'template' | 'search'

export function AdminPanel({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('overview')

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center">
      <div className="bg-white rounded-xl shadow-2xl w-[1040px] h-[85vh] flex flex-col">
        <header className="px-5 py-3 border-b border-ink-200 flex items-center gap-3">
          <h2 className="font-semibold">后台管理</h2>
          <div className="flex bg-ink-100 rounded p-0.5 text-xs ml-3">
            {([
              ['overview', '概览'], ['users', '用户'], ['tasks', '任务'],
              ['models', '默认模型'], ['usage', '用量'], ['files', '文件管理'],
              ['settings', '设置'], ['template', '论文模板'], ['search', '联网搜索'],
            ] as [Tab, string][]).map(([k, v]) => (
              <button key={k} onClick={() => setTab(k)}
                className={`px-3 py-1 rounded ${tab === k ? 'bg-white shadow-sm' : 'text-ink-500'}`}>{v}</button>
            ))}
          </div>
          <div className="flex-1" />
          <button onClick={onClose} className="text-ink-400 hover:text-ink-800">✕</button>
        </header>
        <div className="flex-1 overflow-auto scrollbar-thin">
          {tab === 'overview' && <OverviewTab />}
          {tab === 'users' && <UsersTab />}
          {tab === 'tasks' && <TasksTab />}
          {tab === 'models' && <AdminModelConfig />}
          {tab === 'usage' && <UsageTab />}
          {tab === 'files' && <FilesTab />}
          {tab === 'settings' && <SettingsTab />}
          {tab === 'template' && <PaperTemplateTab />}
          {tab === 'search' && <SearchConfigTab />}
        </div>
      </div>
    </div>
  )
}

// ---------- 概览 ----------
function OverviewTab() {
  const [s, setS] = useState<Stats | null>(null)
  useEffect(() => {
    const reload = () => api.adminStats().then(setS).catch(() => {})
    reload(); const t = setInterval(reload, 5000); return () => clearInterval(t)
  }, [])
  if (!s) return <p className="p-6 text-xs text-ink-400">加载中…</p>

  return (
    <div className="p-6 grid grid-cols-4 gap-4">
      <Card label="用户总数" value={s.users} hint={`活跃 ${s.active_users}`} />
      <Card label="任务总数" value={s.tasks} hint={`运行中 ${s.running_in_memory}`} />
      <Card label="LLM 调用" value={s.usage.total.calls} hint={`失败 ${s.usage.failed_calls}`} />
      <Card label="Token 消耗" value={fmt(s.usage.total.total_tokens)} hint={`默认模型 ${fmt(s.usage.default_model.total_tokens)}`} />
      <Card label="估算成本" value={`$${s.usage.total.cost_usd.toFixed(4)}`} hint={`默认 $${s.usage.default_model.cost_usd.toFixed(4)}`} />
      <Card label="服务时间" value={s.uptime_hint} small />

      <div className="col-span-4 border border-ink-200 rounded p-4">
        <h3 className="text-sm font-semibold mb-3">任务状态分布</h3>
        <div className="flex flex-wrap gap-2">
          {Object.entries(s.tasks_by_state).map(([k, v]) => (
            <div key={k} className="px-3 py-1.5 bg-ink-100 rounded text-xs">
              <span className="font-medium">{k}</span>
              <span className="ml-2 text-ink-500">{v}</span>
            </div>
          ))}
          {Object.keys(s.tasks_by_state).length === 0 && <p className="text-xs text-ink-400">暂无任务</p>}
        </div>
      </div>
    </div>
  )
}

function Card({ label, value, hint, small }: any) {
  return (
    <div className="border border-ink-200 rounded p-4">
      <p className="text-xs text-ink-500">{label}</p>
      <p className={small ? 'text-base mt-1 font-mono' : 'text-2xl mt-1 font-semibold'}>{value}</p>
      {hint && <p className="text-[11px] text-ink-400 mt-1">{hint}</p>}
    </div>
  )
}

const fmt = (n: number) => n >= 1000 ? (n / 1000).toFixed(1) + 'K' : String(n)

// ---------- 用户 ----------
function UsersTab() {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ username: '', email: '', password: '', role: 'user' })
  const [detailId, setDetailId] = useState<number | null>(null)

  const reload = () => api.adminUsers().then(setUsers).catch(() => {})
  useEffect(() => { reload() }, [])

  const create = async () => {
    try {
      await api.adminCreateUser(form)
      setCreating(false); setForm({ username: '', email: '', password: '', role: 'user' })
      reload()
    } catch (e: any) { alert(e?.message || '创建失败') }
  }
  const patch = async (u: AdminUser, patch: any) => {
    await api.adminUpdateUser(u.id, patch); reload()
  }
  const reset = async (u: AdminUser) => {
    const pwd = prompt(`为 ${u.username} 设置新密码（至少 6 位）`)
    if (!pwd || pwd.length < 6) return
    await api.adminUpdateUser(u.id, { password: pwd })
    alert('已重置')
  }
  const del = async (u: AdminUser) => {
    if (!confirm(`确定删除用户 "${u.username}" ？`)) return
    try { await api.adminDeleteUser(u.id); reload() }
    catch (e: any) { alert(e?.message || '删除失败') }
  }

  return (
    <div className="p-5">
      <div className="flex items-center mb-3">
        <h3 className="text-sm font-semibold">用户列表（{users.length}）</h3>
        <div className="flex-1" />
        <button onClick={() => setCreating(v => !v)}
          className="text-xs px-3 py-1.5 bg-ink-800 text-white rounded hover:bg-ink-700">
          {creating ? '取消' : '+ 新建用户'}
        </button>
      </div>

      {creating && (
        <div className="mb-3 p-3 border border-ink-200 rounded grid grid-cols-4 gap-2 text-xs">
          <input placeholder="username" value={form.username}
            onChange={e => setForm({ ...form, username: e.target.value })}
            className="px-2 py-1 border border-ink-200 rounded" />
          <input placeholder="email" type="email" value={form.email}
            onChange={e => setForm({ ...form, email: e.target.value })}
            className="px-2 py-1 border border-ink-200 rounded" />
          <input placeholder="password" type="password" value={form.password}
            onChange={e => setForm({ ...form, password: e.target.value })}
            className="px-2 py-1 border border-ink-200 rounded" />
          <div className="flex gap-1">
            <select value={form.role} onChange={e => setForm({ ...form, role: e.target.value })}
              className="flex-1 px-2 py-1 border border-ink-200 rounded">
              <option value="user">user</option>
              <option value="pro">pro</option>
              <option value="admin">admin</option>
            </select>
            <button onClick={create} className="px-3 bg-ink-800 text-white rounded hover:bg-ink-700">创建</button>
          </div>
        </div>
      )}

      <table className="w-full text-xs">
        <thead className="text-ink-500">
          <tr className="border-b border-ink-200">
            <th className="text-left py-2 px-2">ID</th>
            <th className="text-left py-2 px-2">用户名</th>
            <th className="text-left py-2 px-2">邮箱</th>
            <th className="text-left py-2 px-2">角色</th>
            <th className="text-left py-2 px-2">状态</th>
            <th className="text-left py-2 px-2">默认模型</th>
            <th className="text-left py-2 px-2">任务</th>
            <th className="text-right py-2 px-2">操作</th>
          </tr>
        </thead>
        <tbody>
          {users.map(u => (
            <tr key={u.id} className="border-b border-ink-100 hover:bg-ink-50">
              <td className="py-2 px-2 text-ink-400">{u.id}</td>
              <td className="py-2 px-2 font-medium">{u.username}</td>
              <td className="py-2 px-2 text-ink-500">{u.email}</td>
              <td className="py-2 px-2">
                <select value={u.role} onChange={e => patch(u, { role: e.target.value })}
                  className="text-xs border border-ink-200 rounded px-1">
                  <option value="user">user</option>
                  <option value="pro">pro</option>
                  <option value="admin">admin</option>
                </select>
              </td>
              <td className="py-2 px-2">
                <button onClick={() => patch(u, { is_active: !u.is_active })}
                  className={`px-1.5 py-0.5 rounded text-[10px] ${u.is_active ? 'bg-green-100 text-green-700' : 'bg-ink-200 text-ink-500'}`}>
                  {u.is_active ? '启用' : '禁用'}
                </button>
              </td>
              <td className="py-2 px-2">
                <button onClick={() => patch(u, { use_default_model: !u.use_default_model })}
                  className={`px-1.5 py-0.5 rounded text-[10px] ${u.use_default_model ? 'bg-blue-100 text-blue-700' : 'bg-ink-100 text-ink-500'}`}>
                  {u.use_default_model ? '用默认' : '自定义'}
                </button>
              </td>
              <td className="py-2 px-2">{u.task_count}</td>
              <td className="py-2 px-2 text-right">
                <button onClick={() => setDetailId(u.id)} className="text-blue-600 hover:underline mr-2">用量</button>
                <button onClick={() => reset(u)} className="text-blue-600 hover:underline mr-2">重置密码</button>
                <button onClick={() => del(u)} className="text-red-600 hover:underline">删除</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {detailId !== null && <UserUsageDialog userId={detailId} onClose={() => setDetailId(null)} />}
    </div>
  )
}

// ---------- 任务 ----------
function TasksTab() {
  const [tasks, setTasks] = useState<AdminTask[]>([])
  const reload = () => api.adminTasks().then(setTasks).catch(() => {})
  useEffect(() => { reload() }, [])
  const del = async (t: AdminTask) => {
    if (!confirm(`删除任务 ${t.task_id} ？`)) return
    await api.adminDeleteTask(t.task_id); reload()
  }
  return (
    <div className="p-5">
      <h3 className="text-sm font-semibold mb-3">所有任务（{tasks.length}）</h3>
      <table className="w-full text-xs">
        <thead className="text-ink-500">
          <tr className="border-b border-ink-200">
            <th className="text-left py-2 px-2">Task ID</th>
            <th className="text-left py-2 px-2">用户</th>
            <th className="text-left py-2 px-2">标题</th>
            <th className="text-left py-2 px-2">状态</th>
            <th className="text-left py-2 px-2">阶段</th>
            <th className="text-left py-2 px-2">更新时间</th>
            <th className="text-right py-2 px-2">操作</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map(t => (
            <tr key={t.task_id} className="border-b border-ink-100 hover:bg-ink-50">
              <td className="py-2 px-2 font-mono text-[10px] text-ink-400">{t.task_id}</td>
              <td className="py-2 px-2">{t.username}</td>
              <td className="py-2 px-2 font-medium truncate max-w-[300px]">{t.title}</td>
              <td className="py-2 px-2">{t.state}</td>
              <td className="py-2 px-2 text-ink-500">{t.phase || '-'}</td>
              <td className="py-2 px-2 text-ink-400">{new Date(t.updated_at * 1000).toLocaleString()}</td>
              <td className="py-2 px-2 text-right">
                <button onClick={() => del(t)} className="text-red-600 hover:underline">删除</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------- 用量 ----------
function UsageTab() {
  const [ov, setOv] = useState<Overview | null>(null)
  const [byUser, setByUser] = useState<UserUsage[]>([])
  const [byModel, setByModel] = useState<ModelUsage[]>([])
  const [detailId, setDetailId] = useState<number | null>(null)

  const reload = () => {
    api.adminUsageOverview().then(setOv).catch(() => {})
    api.adminUsageByUser().then(setByUser).catch(() => {})
    api.adminUsageByModel().then(setByModel).catch(() => {})
  }
  useEffect(() => { reload(); const t = setInterval(reload, 8000); return () => clearInterval(t) }, [])

  if (!ov) return <p className="p-6 text-xs text-ink-400">加载中…</p>

  return (
    <div className="p-5 space-y-5">
      <div className="grid grid-cols-3 gap-3">
        <Card label="总调用数" value={ov.total.calls} hint={`失败 ${ov.failed_calls} · 默认模型 ${ov.default_model.calls} 次`} />
        <Card label="总 Tokens" value={fmt(ov.total.total_tokens)}
          hint={`↑${fmt(ov.total.prompt_tokens)} ↓${fmt(ov.total.completion_tokens)} · 默认模型 ${fmt(ov.default_model.total_tokens)}`} />
        <Card label="总成本 (USD)" value={`$${ov.total.cost_usd.toFixed(4)}`}
          hint={`默认模型 $${ov.default_model.cost_usd.toFixed(4)} · 占比 ${ov.total.total_tokens > 0 ? (ov.default_model.total_tokens * 100 / ov.total.total_tokens).toFixed(1) + '%' : '—'}`} />
      </div>

      <div>
        <h3 className="text-sm font-semibold mb-2">按用户统计</h3>
        <table className="w-full text-xs">
          <thead className="text-ink-500">
            <tr className="border-b border-ink-200">
              <th className="text-left py-2 px-2">用户</th>
              <th className="text-right py-2 px-2">调用</th>
              <th className="text-right py-2 px-2">Prompt</th>
              <th className="text-right py-2 px-2">Completion</th>
              <th className="text-right py-2 px-2">总 Tokens</th>
              <th className="text-right py-2 px-2">默认模型 Tokens</th>
              <th className="text-right py-2 px-2">成本</th>
              <th className="text-right py-2 px-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {byUser.map(u => (
              <tr key={u.user_id} className="border-b border-ink-100 hover:bg-ink-50">
                <td className="py-2 px-2 font-medium">{u.username}
                  <span className="text-ink-400 ml-1">({u.email})</span></td>
                <td className="py-2 px-2 text-right">{u.calls}</td>
                <td className="py-2 px-2 text-right text-ink-500">{fmt(u.prompt_tokens)}</td>
                <td className="py-2 px-2 text-right text-ink-500">{fmt(u.completion_tokens)}</td>
                <td className="py-2 px-2 text-right font-medium">{fmt(u.total_tokens)}</td>
                <td className="py-2 px-2 text-right text-blue-700">{fmt(u.default_tokens)}</td>
                <td className="py-2 px-2 text-right">${u.cost_usd.toFixed(4)}</td>
                <td className="py-2 px-2 text-right">
                  <button onClick={() => setDetailId(u.user_id)} className="text-blue-600 hover:underline">明细</button>
                </td>
              </tr>
            ))}
            {byUser.length === 0 && <tr><td colSpan={8} className="py-4 text-center text-ink-400">暂无记录</td></tr>}
          </tbody>
        </table>
      </div>

      <div>
        <h3 className="text-sm font-semibold mb-2">按模型统计</h3>
        <table className="w-full text-xs">
          <thead className="text-ink-500">
            <tr className="border-b border-ink-200">
              <th className="text-left py-2 px-2">模型</th>
              <th className="text-left py-2 px-2">Backend</th>
              <th className="text-left py-2 px-2">来源</th>
              <th className="text-right py-2 px-2">调用</th>
              <th className="text-right py-2 px-2">总 Tokens</th>
              <th className="text-right py-2 px-2">成本</th>
            </tr>
          </thead>
          <tbody>
            {byModel.map((m, i) => (
              <tr key={i} className="border-b border-ink-100 hover:bg-ink-50">
                <td className="py-2 px-2 font-mono">{m.model || '—'}</td>
                <td className="py-2 px-2 text-ink-500">{m.backend}</td>
                <td className="py-2 px-2">
                  <span className={`px-1.5 py-0.5 rounded text-[10px] ${m.is_default ? 'bg-blue-100 text-blue-700' : 'bg-ink-100 text-ink-500'}`}>
                    {m.is_default ? '默认' : '自定义'}
                  </span>
                </td>
                <td className="py-2 px-2 text-right">{m.calls}</td>
                <td className="py-2 px-2 text-right font-medium">{fmt(m.total_tokens)}</td>
                <td className="py-2 px-2 text-right">${m.cost_usd.toFixed(4)}</td>
              </tr>
            ))}
            {byModel.length === 0 && <tr><td colSpan={6} className="py-4 text-center text-ink-400">暂无记录</td></tr>}
          </tbody>
        </table>
      </div>

      {detailId !== null && <UserUsageDialog userId={detailId} onClose={() => setDetailId(null)} />}
    </div>
  )
}

// ---------- 文件管理 ----------
function FilesTab() {
  const [users, setUsers] = useState<UserFileStat[]>([])
  const [detailId, setDetailId] = useState<number | null>(null)
  const [gcResult, setGcResult] = useState<{ removed: string[]; freed_bytes: number } | null>(null)
  const [gcLoading, setGcLoading] = useState(false)

  const reload = () => api.adminFileUsers().then(setUsers).catch(() => {})
  useEffect(() => { reload() }, [])

  const runGc = async () => {
    if (!confirm('扫描并删除所有孤儿工作区目录（无对应任务记录）？')) return
    setGcLoading(true)
    try {
      const r = await api.adminGcFiles()
      setGcResult({ removed: r.removed, freed_bytes: r.freed_bytes })
      reload()
    } catch (e: any) { alert(e?.message || 'GC 失败') }
    finally { setGcLoading(false) }
  }

  const fmtSize = (b: number) => b >= 1048576 ? (b / 1048576).toFixed(1) + ' MB'
    : b >= 1024 ? (b / 1024).toFixed(1) + ' KB' : b + ' B'

  return (
    <div className="p-5 space-y-4">
      <div className="flex items-center gap-3 mb-1">
        <h3 className="text-sm font-semibold">用户文件统计</h3>
        <div className="flex-1" />
        {gcResult && (
          <span className="text-xs text-green-700 bg-green-50 px-2 py-1 rounded">
            GC 完成：清理 {gcResult.removed.length} 个目录，释放 {fmtSize(gcResult.freed_bytes)}
          </span>
        )}
        <button onClick={runGc} disabled={gcLoading}
          className="text-xs px-3 py-1.5 bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50">
          {gcLoading ? '清理中…' : '清理孤儿目录 (GC)'}
        </button>
        <button onClick={reload} className="text-xs px-3 py-1.5 border border-ink-200 rounded hover:bg-ink-50">刷新</button>
      </div>

      <table className="w-full text-xs">
        <thead className="text-ink-500">
          <tr className="border-b border-ink-200">
            <th className="text-left py-2 px-2">用户</th>
            <th className="text-right py-2 px-2">任务数</th>
            <th className="text-right py-2 px-2">文件数</th>
            <th className="text-right py-2 px-2">占用空间</th>
            <th className="text-right py-2 px-2">操作</th>
          </tr>
        </thead>
        <tbody>
          {users.map(u => (
            <tr key={u.user_id} className="border-b border-ink-100 hover:bg-ink-50">
              <td className="py-2 px-2 font-medium">{u.username}</td>
              <td className="py-2 px-2 text-right">{u.task_count}</td>
              <td className="py-2 px-2 text-right">{u.file_count}</td>
              <td className="py-2 px-2 text-right">{fmtSize(u.total_size)}</td>
              <td className="py-2 px-2 text-right">
                <button onClick={() => setDetailId(u.user_id)}
                  className="text-blue-600 hover:underline">查看</button>
              </td>
            </tr>
          ))}
          {users.length === 0 && (
            <tr><td colSpan={5} className="py-4 text-center text-ink-400">暂无数据</td></tr>
          )}
        </tbody>
      </table>

      {detailId !== null && (
        <UserTaskFilesDialog userId={detailId} onClose={() => { setDetailId(null); reload() }} />
      )}
    </div>
  )
}

function UserTaskFilesDialog({ userId, onClose }: { userId: number; onClose: () => void }) {
  const [tasks, setTasks] = useState<TaskFileStat[]>([])
  const [cleaning, setCleaning] = useState<string | null>(null)

  const reload = () => api.adminFileUserTasks(userId).then(setTasks).catch(() => {})
  useEffect(() => { reload() }, [userId])

  const fmtSize = (b: number) => b >= 1048576 ? (b / 1048576).toFixed(1) + ' MB'
    : b >= 1024 ? (b / 1024).toFixed(1) + ' KB' : b + ' B'

  const clean = async (taskId: string) => {
    if (!confirm(`清空任务 ${taskId} 的工作区文件？（任务记录保留）`)) return
    setCleaning(taskId)
    try { await api.adminCleanTaskFiles(taskId); reload() }
    catch (e: any) { alert(e?.message || '清理失败') }
    finally { setCleaning(null) }
  }

  const totalSize = tasks.reduce((s, t) => s + t.total_size, 0)

  return (
    <div className="fixed inset-0 bg-black/40 z-[60] flex items-center justify-center">
      <div className="bg-white rounded-lg shadow-xl w-[760px] max-h-[80vh] flex flex-col">
        <header className="px-5 py-3 border-b border-ink-200 flex items-center">
          <h3 className="font-semibold text-sm">任务文件详情</h3>
          <span className="ml-3 text-xs text-ink-400">共 {tasks.length} 个任务 · {fmtSize(totalSize)}</span>
          <div className="flex-1" />
          <button onClick={onClose} className="text-ink-400 hover:text-ink-800">✕</button>
        </header>
        <div className="p-4 overflow-auto scrollbar-thin">
          <table className="w-full text-xs">
            <thead className="text-ink-500">
              <tr className="border-b border-ink-200">
                <th className="text-left py-1 px-2">Task ID</th>
                <th className="text-left py-1 px-2">标题</th>
                <th className="text-left py-1 px-2">状态</th>
                <th className="text-right py-1 px-2">文件数</th>
                <th className="text-right py-1 px-2">大小</th>
                <th className="text-right py-1 px-2">操作</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map(t => (
                <tr key={t.task_id} className="border-b border-ink-100 hover:bg-ink-50">
                  <td className="py-1.5 px-2 font-mono text-[10px] text-ink-400">{t.task_id}</td>
                  <td className="py-1.5 px-2 max-w-[200px] truncate" title={t.title}>{t.title}</td>
                  <td className="py-1.5 px-2 text-ink-500">{t.state}</td>
                  <td className="py-1.5 px-2 text-right">{t.file_count}</td>
                  <td className="py-1.5 px-2 text-right">{fmtSize(t.total_size)}</td>
                  <td className="py-1.5 px-2 text-right">
                    <button onClick={() => clean(t.task_id)}
                      disabled={cleaning === t.task_id || t.file_count === 0}
                      className="text-red-600 hover:underline disabled:text-ink-300">
                      {cleaning === t.task_id ? '清理中…' : '清空文件'}
                    </button>
                  </td>
                </tr>
              ))}
              {tasks.length === 0 && (
                <tr><td colSpan={6} className="py-4 text-center text-ink-400">暂无任务</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}


// ---------- 单用户用量弹窗 ----------
function UserUsageDialog({ userId, onClose }: { userId: number; onClose: () => void }) {
  const [data, setData] = useState<any>(null)
  useEffect(() => { api.adminUserUsage(userId).then(setData).catch(() => {}) }, [userId])
  return (
    <div className="fixed inset-0 bg-black/40 z-[60] flex items-center justify-center">
      <div className="bg-white rounded-lg shadow-xl w-[720px] max-h-[80vh] flex flex-col">
        <header className="px-5 py-3 border-b border-ink-200 flex items-center">
          <h3 className="font-semibold text-sm">
            用户用量明细 {data?.user ? `· ${data.user.username}` : ''}
          </h3>
          <div className="flex-1" />
          <button onClick={onClose} className="text-ink-400 hover:text-ink-800">✕</button>
        </header>
        <div className="p-4 overflow-auto scrollbar-thin">
          {!data ? <p className="text-xs text-ink-400">加载中…</p> : (
            <>
              <div className="grid grid-cols-4 gap-2 mb-4 text-xs">
                <Card label="调用" value={data.total.calls} />
                <Card label="总 Tokens" value={fmt(data.total.total_tokens)} />
                <Card label="成本" value={`$${data.total.cost_usd.toFixed(4)}`} />
                <Card label="默认模型 Tokens" value={fmt(data.default_model.total_tokens)}
                  hint={`$${data.default_model.cost_usd.toFixed(4)}`} />
              </div>
              <h4 className="text-xs font-semibold mb-2">最近调用（{data.recent.length}）</h4>
              <table className="w-full text-[11px]">
                <thead className="text-ink-500">
                  <tr className="border-b border-ink-200">
                    <th className="text-left py-1 px-1">时间</th>
                    <th className="text-left py-1 px-1">Agent</th>
                    <th className="text-left py-1 px-1">模型</th>
                    <th className="text-left py-1 px-1">来源</th>
                    <th className="text-right py-1 px-1">Tokens</th>
                    <th className="text-right py-1 px-1">成本</th>
                    <th className="text-left py-1 px-1">状态</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent.map((r: any) => (
                    <tr key={r.id} className="border-b border-ink-100">
                      <td className="py-1 px-1 text-ink-400">{new Date(r.created_at * 1000).toLocaleTimeString()}</td>
                      <td className="py-1 px-1">{r.agent}</td>
                      <td className="py-1 px-1 font-mono">{r.model}</td>
                      <td className="py-1 px-1">{r.is_default ? '默认' : '自定义'}</td>
                      <td className="py-1 px-1 text-right">{r.total_tokens}</td>
                      <td className="py-1 px-1 text-right">${r.cost_usd.toFixed(5)}</td>
                      <td className="py-1 px-1">
                        {r.ok ? <span className="text-green-700">ok</span>
                              : <span className="text-red-700" title={r.error}>fail</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      </div>
    </div>
  )
}


// ---------- 论文模板编辑器 ----------
function PaperTemplateTab() {
  const [content, setContent] = useState('')
  const [original, setOriginal] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)

  useEffect(() => {
    api.adminGetPaperTemplate()
      .then(r => { setContent(r.content); setOriginal(r.content) })
      .catch(() => setMsg({ type: 'err', text: '加载失败' }))
      .finally(() => setLoading(false))
  }, [])

  const onSave = async () => {
    setSaving(true); setMsg(null)
    try {
      const r = await api.adminUpdatePaperTemplate(content)
      setOriginal(r.content)
      setMsg({ type: 'ok', text: `已保存（${r.size} 字节）` })
    } catch (e: any) {
      setMsg({ type: 'err', text: e?.message || '保存失败' })
    } finally { setSaving(false) }
  }

  const onReset = () => { setContent(original); setMsg(null) }

  if (loading) return <p className="p-6 text-xs text-ink-400">加载中…</p>

  return (
    <div className="p-5 flex flex-col gap-4 h-full">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">论文章节写作模板</h3>
          <p className="text-xs text-ink-500 mt-1 leading-relaxed">
            TOML 格式，key 对应章节（firstPage / RepeatQues / analysisQues / modelAssumption / symbol / eda / ques1…N / sensitivity_analysis / judge）。
            修改后下次任务立即生效，服务端自动备份 .toml.bak。
          </p>
        </div>
        <div className="flex gap-2 shrink-0">
          <button
            onClick={onReset}
            disabled={content === original}
            className="px-3 py-1.5 border border-ink-200 rounded text-xs text-ink-600 hover:bg-ink-50 disabled:opacity-40">
            撤销修改
          </button>
          <button
            onClick={onSave}
            disabled={saving || content === original || !content.trim()}
            className="px-4 py-1.5 bg-ink-800 text-white rounded text-xs hover:bg-ink-700 disabled:bg-ink-300 disabled:cursor-not-allowed">
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>

      {msg && (
        <p className={`text-xs px-3 py-2 rounded ${msg.type === 'ok' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
          {msg.text}
        </p>
      )}

      <textarea
        value={content}
        onChange={e => setContent(e.target.value)}
        spellCheck={false}
        className="flex-1 font-mono text-[12px] leading-relaxed border border-ink-200 rounded p-3 resize-none focus:outline-none focus:border-ink-400 bg-ink-50/50 min-h-[400px]"
        placeholder="TOML 格式模板内容…"
      />
    </div>
  )
}


// ---------- 系统设置 ----------
function SettingsTab() {
  const [data, setData] = useState<SystemSettings | null>(null)
  const [email, setEmail] = useState('')
  const [quota, setQuota] = useState('0')
  const [uploadFileMb, setUploadFileMb] = useState('100')
  const [uploadTotalMb, setUploadTotalMb] = useState('500')
  const [uploadMaxFiles, setUploadMaxFiles] = useState('20')
  const [savingEmail, setSavingEmail] = useState(false)
  const [savingQuota, setSavingQuota] = useState(false)
  const [savingUpload, setSavingUpload] = useState(false)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)

  const reload = async () => {
    try {
      const s = await api.adminGetSettings()
      setData(s)
      setEmail(s.openalex_email || '')
      setQuota(String(s.daily_token_quota ?? 0))
      setUploadFileMb(String(s.max_upload_file_mb ?? 100))
      setUploadTotalMb(String(s.max_upload_total_mb ?? 500))
      setUploadMaxFiles(String(s.max_upload_files ?? 20))
    } catch (e: any) {
      setMsg({ type: 'err', text: e?.message || '加载失败' })
    }
  }
  useEffect(() => { reload() }, [])

  const onSave = async (clear = false) => {
    setSaving(true); setMsg(null)
    try {
      const next = await api.adminUpdateSettings({ openalex_email: clear ? '' : email.trim() })
      setData(next)
      setEmail(next.openalex_email || '')
      setMsg({ type: 'ok', text: clear ? '已清除 DB 配置' : '保存成功' })
    } catch (e: any) {
      setMsg({ type: 'err', text: e?.message || '保存失败' })
    } finally {
      setSaving(false)
      setTimeout(() => setMsg(null), 3000)
    }
  }

  const onSaveQuota = async () => {
    setSavingQuota(true); setMsg(null)
    try {
      const q = Math.max(0, parseInt(quota, 10) || 0)
      const next = await api.adminUpdateSettings({ daily_token_quota: q })
      setData(next)
      setQuota(String(next.daily_token_quota ?? 0))
      setMsg({ type: 'ok', text: '配额已保存' })
    } catch (e: any) {
      setMsg({ type: 'err', text: e?.message || '保存失败' })
    } finally {
      setSavingQuota(false)
      setTimeout(() => setMsg(null), 3000)
    }
  }

  const onSaveUploadLimits = async () => {
    const fMb = Math.max(1, parseInt(uploadFileMb, 10) || 100)
    const tMb = Math.max(1, parseInt(uploadTotalMb, 10) || 500)
    const nFiles = Math.max(1, parseInt(uploadMaxFiles, 10) || 20)
    setSavingUpload(true); setMsg(null)
    try {
      const next = await api.adminUpdateSettings({
        max_upload_file_mb: fMb,
        max_upload_total_mb: tMb,
        max_upload_files: nFiles,
      })
      setData(next)
      setUploadFileMb(String(next.max_upload_file_mb ?? 100))
      setUploadTotalMb(String(next.max_upload_total_mb ?? 500))
      setUploadMaxFiles(String(next.max_upload_files ?? 20))
      setMsg({ type: 'ok', text: '上传限制已保存' })
    } catch (e: any) {
      setMsg({ type: 'err', text: e?.message || '保存失败' })
    } finally {
      setSavingUpload(false)
      setTimeout(() => setMsg(null), 3000)
    }
  }

  if (!data) return <p className="p-6 text-xs text-ink-400">加载中…</p>

  const sourceLabel: Record<string, { text: string; cls: string }> = {
    db:    { text: '数据库（管理员设置）', cls: 'text-green-700 bg-green-50 border-green-200' },
    env:   { text: '环境变量（.env 兜底）', cls: 'text-blue-700 bg-blue-50 border-blue-200' },
    unset: { text: '未配置（OpenAlex 检索将跳过）', cls: 'text-amber-700 bg-amber-50 border-amber-200' },
  }
  const src = sourceLabel[data.openalex_email_source] ?? sourceLabel.unset

  return (
    <div className="p-6 max-w-2xl space-y-6">
      {/* OpenAlex Email */}
      <section className="border border-ink-200 rounded-lg p-5">
        <header className="flex items-baseline justify-between mb-2">
          <h3 className="text-sm font-semibold">OpenAlex 学术检索 Email</h3>
          <span className={`text-[11px] px-2 py-0.5 border rounded ${src.cls}`}>
            当前生效来源：{src.text}
          </span>
        </header>
        <p className="text-xs text-ink-500 leading-relaxed mb-3">
          Writer 调用 <code className="px-1 py-0.5 bg-ink-100 rounded text-[11px]">search_papers</code> 工具
          时通过该邮箱进入 OpenAlex
          <a className="text-blue-600 hover:underline mx-1"
             href="https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication#the-polite-pool"
             target="_blank" rel="noreferrer">polite pool</a>
          ，享受更稳定的 API 配额（必填，否则跳过文献搜索功能）。
          <br />
          DB 值优先于 <code className="px-1 py-0.5 bg-ink-100 rounded text-[11px]">.env</code> 中的 <code>OPENALEX_EMAIL</code>，
          清除 DB 值后将回退到环境变量。
        </p>

        <div className="flex gap-2">
          <input
            type="email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            placeholder="your@email.com"
            className="flex-1 px-3 py-2 border border-ink-200 rounded text-sm focus:outline-none focus:border-ink-500"
            autoComplete="off"
            spellCheck={false}
          />
          <button
            onClick={() => onSave(false)}
            disabled={saving || !email.trim() || email.trim() === data.openalex_email}
            className="px-4 py-2 bg-ink-800 text-white rounded text-sm hover:bg-ink-700 disabled:bg-ink-300 disabled:cursor-not-allowed">
            {saving ? '保存中…' : '保存'}
          </button>
          {data.openalex_email_source === 'db' && (
            <button
              onClick={() => onSave(true)}
              disabled={saving}
              title="清除数据库中的值，回退到 .env"
              className="px-3 py-2 border border-ink-300 text-ink-700 rounded text-sm hover:bg-ink-50">
              清除
            </button>
          )}
        </div>

        {msg && (
          <p className={`mt-3 text-xs ${msg.type === 'ok' ? 'text-green-700' : 'text-red-700'}`}>
            {msg.text}
          </p>
        )}
      </section>

      {/* 每日 Token 配额 */}
      <section className="border border-ink-200 rounded-lg p-5">
        <header className="flex items-baseline justify-between mb-2">
          <h3 className="text-sm font-semibold">用户每日 Token 配额</h3>
          <span className={`text-[11px] px-2 py-0.5 border rounded ${
            data.daily_token_quota_source === 'db'
              ? 'text-green-700 bg-green-50 border-green-200'
              : 'text-blue-700 bg-blue-50 border-blue-200'
          }`}>
            来源：{data.daily_token_quota_source === 'db' ? '数据库' : '.env / 默认'}
          </span>
        </header>
        <p className="text-xs text-ink-500 leading-relaxed mb-3">
          每位用户每 UTC 日最多消耗的 total_tokens 上限。<code className="px-1 py-0.5 bg-ink-100 rounded text-[11px]">0</code> = 不限制。
          超限后当日 LLM 调用将被拒绝并报错，次日自动解除。
        </p>
        <div className="flex gap-2 items-center">
          <input
            type="number"
            min="0"
            step="10000"
            value={quota}
            onChange={e => setQuota(e.target.value)}
            placeholder="0 = 不限制"
            className="w-40 px-3 py-2 border border-ink-200 rounded text-sm focus:outline-none focus:border-ink-500"
          />
          <span className="text-xs text-ink-400">tokens / 日</span>
          <button
            onClick={onSaveQuota}
            disabled={savingQuota || parseInt(quota, 10) === data.daily_token_quota}
            className="px-4 py-2 bg-ink-800 text-white rounded text-sm hover:bg-ink-700 disabled:bg-ink-300 disabled:cursor-not-allowed">
            {savingQuota ? '保存中…' : '保存'}
          </button>
        </div>
      </section>

      {/* 上传限制 */}
      <section className="border border-ink-200 rounded-lg p-5">
        <header className="flex items-baseline justify-between mb-2">
          <h3 className="text-sm font-semibold">文件上传限制</h3>
          <span className={`text-[11px] px-2 py-0.5 border rounded ${
            data.upload_limits_source === 'db'
              ? 'text-green-700 bg-green-50 border-green-200'
              : 'text-blue-700 bg-blue-50 border-blue-200'
          }`}>
            来源：{data.upload_limits_source === 'db' ? '数据库' : '.env / 默认'}
          </span>
        </header>
        <p className="text-xs text-ink-500 leading-relaxed mb-4">
          控制用户创建任务时允许上传的文件体积与数量上限，超限返回 HTTP 413。
          修改后对新任务立即生效，无需重启服务。
        </p>
        <div className="grid grid-cols-3 gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-ink-500">单文件最大 (MB)</span>
            <input
              type="number" min="1" max="10240" step="1"
              value={uploadFileMb}
              onChange={e => setUploadFileMb(e.target.value)}
              className="px-3 py-2 border border-ink-200 rounded text-sm focus:outline-none focus:border-ink-500"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-ink-500">总量上限 (MB)</span>
            <input
              type="number" min="1" max="102400" step="1"
              value={uploadTotalMb}
              onChange={e => setUploadTotalMb(e.target.value)}
              className="px-3 py-2 border border-ink-200 rounded text-sm focus:outline-none focus:border-ink-500"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-ink-500">最多文件数</span>
            <input
              type="number" min="1" max="500" step="1"
              value={uploadMaxFiles}
              onChange={e => setUploadMaxFiles(e.target.value)}
              className="px-3 py-2 border border-ink-200 rounded text-sm focus:outline-none focus:border-ink-500"
            />
          </label>
        </div>
        <div className="mt-3 flex items-center gap-3">
          <button
            onClick={onSaveUploadLimits}
            disabled={savingUpload || (
              parseInt(uploadFileMb, 10) === data.max_upload_file_mb &&
              parseInt(uploadTotalMb, 10) === data.max_upload_total_mb &&
              parseInt(uploadMaxFiles, 10) === data.max_upload_files
            )}
            className="px-4 py-2 bg-ink-800 text-white rounded text-sm hover:bg-ink-700 disabled:bg-ink-300 disabled:cursor-not-allowed">
            {savingUpload ? '保存中…' : '保存'}
          </button>
          <span className="text-[11px] text-ink-400">
            当前生效：单文件 {data.max_upload_file_mb} MB · 总量 {data.max_upload_total_mb} MB · 最多 {data.max_upload_files} 个文件
          </span>
        </div>
      </section>

      {/* 提示卡 */}
      <section className="border border-ink-200 rounded-lg p-5 bg-ink-50/50">
        <h3 className="text-sm font-semibold mb-2">关于学术文献检索</h3>
        <ul className="text-xs text-ink-600 space-y-1.5 leading-relaxed list-disc list-inside">
          <li>启用后，Writer 在撰写「问题分析」「模型建立与求解」等章节时会自动调用 OpenAlex 搜索相关文献。</li>
          <li>检索结果以 <span className="font-mono">GB/T 7714-2015</span> 格式呈现，可直接用于参考文献章节。</li>
          <li>邮箱仅用于身份标识，OpenAlex 不发送任何邮件，参考其
            <a className="text-blue-600 hover:underline mx-0.5" href="https://docs.openalex.org/" target="_blank" rel="noreferrer">官方文档</a>。
          </li>
          <li>未配置时不会报错，Writer 将跳过文献检索环节继续写作。</li>
        </ul>
      </section>
    </div>
  )
}

// ─────────────────────────── 联网搜索配置 ────────────────────────────────────
function SearchConfigTab() {
  const DEFAULT: SearchConfig = {
    search_provider: 'duckduckgo',
    searxng_base_url: '',
    searxng_timeout: 8,
    search_max_results: 6,
  }
  const [cfg, setCfg] = useState<SearchConfig>(DEFAULT)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; found?: number; error?: string } | null>(null)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    api.adminGetSearchConfig().then(c => { setCfg(c); setDirty(false) }).catch(() => {})
  }, [])

  const update = (patch: Partial<SearchConfig>) => {
    setCfg(prev => ({ ...prev, ...patch }))
    setDirty(true)
    setTestResult(null)
    setMsg('')
  }

  const save = async () => {
    setSaving(true); setMsg('')
    try {
      await api.adminUpdateSearchConfig(cfg)
      setDirty(false)
      setMsg('✓ 已保存（重启后永久生效，当前实例立即生效）')
    } catch (e: any) {
      setMsg(`✗ 保存失败: ${e.message ?? e}`)
    } finally { setSaving(false) }
  }

  const test = async () => {
    setTesting(true); setTestResult(null); setMsg('')
    try {
      const res = await api.adminTestSearch(cfg)
      setTestResult(res)
    } catch (e: any) {
      setTestResult({ ok: false, error: e.message ?? String(e) })
    } finally { setTesting(false) }
  }

  return (
    <div className="p-6 max-w-2xl space-y-6">
      <h3 className="text-sm font-semibold">联网搜索配置</h3>

      {/* 搜索引擎选择 */}
      <section className="space-y-3">
        <label className="text-xs font-medium text-ink-600">搜索引擎</label>
        <div className="flex gap-3">
          {(['duckduckgo', 'searxng'] as const).map(p => (
            <label key={p}
              className={`flex items-center gap-2 px-4 py-2.5 rounded-lg border cursor-pointer text-sm
                ${cfg.search_provider === p
                  ? 'border-blue-500 bg-blue-50 text-blue-700'
                  : 'border-ink-200 text-ink-600 hover:border-ink-300'}`}
            >
              <input
                type="radio" name="provider" value={p}
                checked={cfg.search_provider === p}
                onChange={() => update({ search_provider: p })}
                className="hidden"
              />
              <span className={`w-3 h-3 rounded-full border-2 flex-shrink-0
                ${cfg.search_provider === p ? 'border-blue-500 bg-blue-500' : 'border-ink-300'}`} />
              {p === 'duckduckgo' ? 'DuckDuckGo（免费，无需配置）' : 'SearXNG（自建，推荐）'}
            </label>
          ))}
        </div>
      </section>

      {/* SearXNG 专属配置 */}
      {cfg.search_provider === 'searxng' && (
        <section className="space-y-4 p-4 bg-ink-50 rounded-lg border border-ink-200">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-ink-600">SearXNG 地址</label>
            <input
              type="url"
              value={cfg.searxng_base_url}
              onChange={e => update({ searxng_base_url: e.target.value })}
              placeholder="http://127.0.0.1:8080"
              className="w-full text-sm px-3 py-2 border border-ink-200 rounded-lg focus:outline-none focus:border-blue-400"
            />
            <p className="text-[11px] text-ink-400">服务器本地部署时填 http://127.0.0.1:8080，不含路径</p>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-ink-600">请求超时（秒）</label>
              <input
                type="number" min={1} max={60}
                value={cfg.searxng_timeout}
                onChange={e => update({ searxng_timeout: Number(e.target.value) })}
                className="w-full text-sm px-3 py-2 border border-ink-200 rounded-lg focus:outline-none focus:border-blue-400"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-ink-600">每次返回条数</label>
              <input
                type="number" min={1} max={20}
                value={cfg.search_max_results}
                onChange={e => update({ search_max_results: Number(e.target.value) })}
                className="w-full text-sm px-3 py-2 border border-ink-200 rounded-lg focus:outline-none focus:border-blue-400"
              />
            </div>
          </div>

          {/* 连通性测试 */}
          <div className="flex items-center gap-3">
            <button
              onClick={test} disabled={testing || !cfg.searxng_base_url}
              className="px-4 py-1.5 text-xs rounded-lg border border-ink-300
                         hover:bg-ink-100 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {testing ? '测试中…' : '测试连接'}
            </button>
            {testResult && (
              <span className={`text-xs font-medium ${testResult.ok ? 'text-green-600' : 'text-red-500'}`}>
                {testResult.ok
                  ? `✓ 连通，返回 ${testResult.found} 条结果`
                  : `✗ ${testResult.error}`}
              </span>
            )}
          </div>
        </section>
      )}

      {/* DuckDuckGo 下的返回条数 */}
      {cfg.search_provider === 'duckduckgo' && (
        <section className="space-y-1.5">
          <label className="text-xs font-medium text-ink-600">每次返回条数</label>
          <input
            type="number" min={1} max={10}
            value={cfg.search_max_results}
            onChange={e => update({ search_max_results: Number(e.target.value) })}
            className="w-40 text-sm px-3 py-2 border border-ink-200 rounded-lg focus:outline-none focus:border-blue-400"
          />
        </section>
      )}

      {/* 保存 */}
      <div className="flex items-center gap-4 pt-2">
        <button
          onClick={save} disabled={!dirty || saving}
          className="px-5 py-2 text-sm rounded-lg bg-blue-600 text-white
                     hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {saving ? '保存中…' : '保存配置'}
        </button>
        {msg && <span className={`text-xs ${msg.startsWith('✓') ? 'text-green-600' : 'text-red-500'}`}>{msg}</span>}
      </div>

      {/* 部署说明 */}
      <section className="text-xs text-ink-500 space-y-2 leading-relaxed border-t border-ink-100 pt-4">
        <p className="font-medium text-ink-700">SearXNG Docker 快速部署（Ubuntu 服务器）</p>

        {/* 重要提示：JSON 格式 */}
        <div className="flex gap-2 p-3 bg-amber-50 border border-amber-200 rounded-lg text-amber-800">
          <span className="text-base leading-none mt-0.5">⚠️</span>
          <div>
            <p className="font-medium">必须启用 JSON 格式</p>
            <p className="mt-0.5 text-amber-700">SearXNG 默认只输出 HTML，需在 <code className="bg-amber-100 px-1 rounded">settings.yml</code> 中加入 <code className="bg-amber-100 px-1 rounded">- json</code>，否则 API 调用返回 400 错误。项目已提供预配置文件，推荐使用下方命令。</p>
          </div>
        </div>

        <pre className="bg-ink-50 rounded p-3 overflow-x-auto text-[11px] text-ink-700 leading-loose">{
`# 使用项目内置预配置（已启用 JSON 格式 + 技术搜索引擎）
cd deploy/searxng

# 修改 secret key（重要）
sed -i 's/change-me-to-a-random-string/'$(openssl rand -hex 32)'/' docker-compose.yml

# 启动
docker compose up -d

# 验证 JSON 接口
curl "http://127.0.0.1:8080/search?q=python&format=json" | head -c 200`
        }</pre>
        <p>
          部署完成后在上方填写{' '}
          <code className="bg-ink-100 px-1 rounded">http://127.0.0.1:8080</code>
          ，点击「测试连接」，成功后保存即可。配置持久化到数据库，重启后仍生效。
        </p>
        <p className="text-ink-400">SearXNG 内存占用约 150–300 MB，对 8C8G 服务器影响极小。</p>
      </section>
    </div>
  )
}
