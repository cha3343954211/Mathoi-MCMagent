// 统一 API 客户端：token 注入 + 401 拦截 + 完整端点

const TOKEN_KEY = 'mathoi_token'
export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY) || '',
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
}

let onUnauthorized: (() => void) | null = null
export const setUnauthorizedHandler = (fn: () => void) => { onUnauthorized = fn }

async function request<T = any>(
  url: string,
  init: RequestInit = {},
  { auth = true }: { auth?: boolean } = {}
): Promise<T> {
  const headers = new Headers(init.headers || {})
  if (auth) {
    const t = tokenStore.get()
    if (t) headers.set('Authorization', `Bearer ${t}`)
  }
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const r = await fetch(url, { ...init, headers })
  if (r.status === 401) {
    tokenStore.clear()
    onUnauthorized?.()
    throw new Error('未登录或已过期')
  }
  if (!r.ok) {
    const text = await r.text()
    let msg = text
    try { msg = JSON.parse(text).detail || text } catch {}
    throw new Error(msg || `HTTP ${r.status}`)
  }
  if (r.status === 204) return null as any
  return r.json()
}

// ---------- 类型 ----------
export interface User {
  id: number; username: string; email: string; role: string
  is_active: boolean; use_default_model?: boolean
  created_at: number; last_login: number | null
}

export interface Task {
  task_id: string; user_id: number
  title: string; problem: string
  state: string; phase: string; error: string
  work_dir: string; data_files: string[]
  created_at: number; updated_at: number
  hitl_request: { prompt: string; context: any; ts: number } | null
}

export interface TraceEvent {
  type: string; task_id: string; agent: string
  payload: Record<string, any>
  timestamp: number; event_id: string
}

export interface AgentCfg {
  agent: string; backend: string; model: string; base_url: string
  has_api_key: boolean; temperature: number
  max_tokens?: number | null
  price_prompt_per_1k?: number; price_completion_per_1k?: number
  updated_at?: number
}
export interface EffectiveCfg extends AgentCfg { is_default: boolean }

export interface MyModelsView {
  use_default_model: boolean
  agents: string[]
  defaults: Record<string, AgentCfg>
  mine: Record<string, AgentCfg>
  effective: Record<string, EffectiveCfg>
}

export interface AdminModelsView {
  agents: string[]
  defaults: Record<string, AgentCfg>
}

export interface AdminUser extends User {
  use_default_model: boolean; task_count: number
}

export interface AdminTask {
  task_id: string; user_id: number; username: string
  title: string; state: string; phase: string
  created_at: number; updated_at: number
}

export interface UsageSummary {
  calls: number; prompt_tokens: number; completion_tokens: number
  total_tokens: number; cost_usd: number
}
export interface Overview {
  total: UsageSummary; default_model: UsageSummary; failed_calls: number
}
export interface UserUsage {
  user_id: number; username: string; email: string
  calls: number; prompt_tokens: number; completion_tokens: number
  total_tokens: number; default_tokens: number; cost_usd: number
}
export interface ModelUsage {
  model: string; backend: string; is_default: boolean
  calls: number; prompt_tokens: number; completion_tokens: number
  total_tokens: number; cost_usd: number
}

export interface Stats {
  users: number; active_users: number
  tasks: number; tasks_by_state: Record<string, number>
  running_in_memory: number; uptime_hint: string
  usage: Overview
}

export interface AgentUsageStat {
  calls: number; prompt_tokens: number; completion_tokens: number
  total_tokens: number; cost_usd: number; model: string
}

export interface TaskUsageRecord {
  agent: string; model: string; backend: string
  prompt_tokens: number; completion_tokens: number; total_tokens: number
  cost_usd: number; ok: boolean; error: string; created_at: number
}

export interface TaskUsage {
  task_id: string
  total: { calls: number; prompt_tokens: number; completion_tokens: number; total_tokens: number; cost_usd: number }
  by_agent: Record<string, AgentUsageStat>
  records: TaskUsageRecord[]
}

// ---------- API ----------
export const api = {
  // 认证
  login: (username: string, password: string) =>
    request<{ access_token: string; user: User }>('/api/auth/login', {
      method: 'POST', body: JSON.stringify({ username, password })
    }, { auth: false }),
  register: (username: string, email: string, password: string) =>
    request<{ access_token: string; user: User }>('/api/auth/register', {
      method: 'POST', body: JSON.stringify({ username, email, password })
    }, { auth: false }),
  me: () => request<User>('/api/auth/me'),
  changePassword: (old_password: string, new_password: string) =>
    request<{ ok: boolean }>('/api/auth/change-password', {
      method: 'POST', body: JSON.stringify({ old_password, new_password })
    }),

  // 任务
  listTasks: () => request<Task[]>('/api/tasks'),
  getTask: (id: string) => request<Task>(`/api/tasks/${id}`),
  history: (id: string) => request<TraceEvent[]>(`/api/tasks/${id}/events`),
  pause: (id: string) => request<any>(`/api/tasks/${id}/pause`, { method: 'POST' }),
  resume: (id: string) => request<any>(`/api/tasks/${id}/resume`, { method: 'POST' }),
  cancel: (id: string) => request<any>(`/api/tasks/${id}/cancel`, { method: 'POST' }),
  deleteTask: (id: string) => request<any>(`/api/tasks/${id}`, { method: 'DELETE' }),
  hitl: (id: string, body: any) => request<any>(`/api/tasks/${id}/hitl`, {
    method: 'POST', body: JSON.stringify(body)
  }),
  createTask: (title: string, problem: string, files: File[]) => {
    const fd = new FormData()
    fd.append('title', title); fd.append('problem', problem)
    files.forEach(f => fd.append('files', f))
    return request<Task>('/api/tasks', { method: 'POST', body: fd })
  },
  taskUsage: (id: string) => request<TaskUsage>(`/api/tasks/${id}/usage`),
  files: (id: string) =>
    request<{ work_dir: string; files: { name: string; size: number }[] }>(`/api/tasks/${id}/files`),
  fileUrl: (id: string, name: string) => {
    const t = tokenStore.get()
    return `/api/tasks/${id}/files/${encodeURIComponent(name)}${t ? `?token=${encodeURIComponent(t)}` : ''}`
  },
  archiveUrl: (id: string) => {
    const t = tokenStore.get()
    return `/api/tasks/${id}/archive${t ? `?token=${encodeURIComponent(t)}` : ''}`
  },
  exportPdfUrl: (id: string) => {
    const t = tokenStore.get()
    return `/api/tasks/${id}/export/pdf${t ? `?token=${encodeURIComponent(t)}` : ''}`
  },
  exportIpynbUrl: (id: string, path: string) => {
    const t = tokenStore.get()
    return `/api/tasks/${id}/files/${encodeURIComponent(path)}/as/ipynb${t ? `?token=${encodeURIComponent(t)}` : ''}`
  },

  // 模型（用户视角）
  listProviderModels: (base_url: string, api_key = '', agent = 'default') =>
    request<string[]>(`/api/models/list?base_url=${encodeURIComponent(base_url)}&api_key=${encodeURIComponent(api_key)}&agent=${encodeURIComponent(agent)}`),
  getMyModels: () => request<MyModelsView>('/api/models'),
  toggleDefault: (use_default_model: boolean) =>
    request<any>('/api/models/toggle', {
      method: 'POST', body: JSON.stringify({ use_default_model })
    }),
  updateMyModel: (body: Partial<AgentCfg> & { agent: string; api_key?: string }) =>
    request<any>('/api/models/mine', { method: 'POST', body: JSON.stringify(body) }),

  // Admin
  adminUsers: () => request<AdminUser[]>('/api/admin/users'),
  adminCreateUser: (body: any) => request<AdminUser>('/api/admin/users', {
    method: 'POST', body: JSON.stringify(body)
  }),
  adminUpdateUser: (id: number, body: any) => request<AdminUser>(`/api/admin/users/${id}`, {
    method: 'PATCH', body: JSON.stringify(body)
  }),
  adminDeleteUser: (id: number) => request<any>(`/api/admin/users/${id}`, { method: 'DELETE' }),
  adminTasks: () => request<AdminTask[]>('/api/admin/tasks'),
  adminDeleteTask: (id: string) => request<any>(`/api/admin/tasks/${id}`, { method: 'DELETE' }),
  adminStats: () => request<Stats>('/api/admin/stats'),
  adminGetDefaults: () => request<AdminModelsView>('/api/admin/models'),
  adminUpdateDefault: (body: any) =>
    request<any>('/api/admin/models', { method: 'POST', body: JSON.stringify(body) }),
  adminUsageOverview: () => request<Overview>('/api/admin/usage/overview'),
  adminUsageByUser: () => request<UserUsage[]>('/api/admin/usage/by-user'),
  adminUsageByModel: () => request<ModelUsage[]>('/api/admin/usage/by-model'),
  adminUserUsage: (id: number) =>
    request<{ user: any; total: UsageSummary; default_model: any; recent: any[] }>(`/api/admin/users/${id}/usage`),

  // WebSocket
  openWS: (id: string, onEvent: (e: TraceEvent) => void) => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const t = tokenStore.get()
    const ws = new WebSocket(`${proto}://${location.host}/api/ws/tasks/${id}?token=${encodeURIComponent(t)}`)
    ws.onmessage = ev => { try { onEvent(JSON.parse(ev.data)) } catch {} }
    return ws
  }
}
