import { useEffect, useState } from 'react'
import { api, AdminTask, AdminUser, ModelUsage, Overview, Stats, SystemSettings, TaskFileStat, UserFileStat, UserUsage } from '../api'
import { AdminModelConfig } from './ModelConfig'

type Tab = 'overview' | 'users' | 'tasks' | 'models' | 'usage' | 'files' | 'settings'

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
              ['settings', '设置']
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


// ---------- 系统设置 ----------
function SettingsTab() {
  const [data, setData] = useState<SystemSettings | null>(null)
  const [email, setEmail] = useState('')
  const [quota, setQuota] = useState('0')
  const [savingEmail, setSavingEmail] = useState(false)
  const [savingQuota, setSavingQuota] = useState(false)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)

  const reload = async () => {
    try {
      const s = await api.adminGetSettings()
      setData(s)
      setEmail(s.openalex_email || '')
      setQuota(String(s.daily_token_quota ?? 0))
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
