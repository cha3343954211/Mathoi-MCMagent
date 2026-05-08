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
  hitl_request: { prompt: string; context: any; ts: number; deadline?: number } | null
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

export interface ModelPreset {
  id: number
  name: string
  description: string
  agent: string          // 'all' | 'default' | 'coordinator' | 'modeler' | 'coder' | 'writer'
  backend: string
  model: string
  base_url: string
  has_api_key: boolean
  temperature: number
  max_tokens: number | null
  price_prompt_per_1k?: number
  price_completion_per_1k?: number
  is_active?: boolean
  is_default?: boolean
  pro_only?: boolean
  sort_order?: number
  created_at?: number
}

export interface UserFileStat {
  user_id: number
  username: string
  task_count: number
  file_count: number
  total_size: number
}

export interface TaskFileStat {
  task_id: string
  title: string
  state: string
  file_count: number
  total_size: number
  work_dir: string
}

export interface AdminPresetsView {
  presets: ModelPreset[]
  agents: string[]
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

export interface SystemSettings {
  openalex_email: string
  /** 来源：'db' | 'env' | 'unset'，仅展示用 */
  openalex_email_source: string
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
  register: (username: string, password: string, email?: string) =>
    request<{ access_token: string; user: User }>('/api/auth/register', {
      method: 'POST', body: JSON.stringify({ username, password, ...(email ? { email } : {}) })
    }, { auth: false }),
  health: () => request<any>('/api/health', {}, { auth: false }),
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
  interrupt: (id: string) => request<{ ok: boolean; message: string }>(`/api/tasks/${id}/interrupt`, { method: 'POST' }),
  retryTask: (id: string) => request<any>(`/api/tasks/${id}/retry`, { method: 'POST' }),
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
  exportDocxUrl: (id: string) => {
    const t = tokenStore.get()
    return `/api/tasks/${id}/export/docx${t ? `?token=${encodeURIComponent(t)}` : ''}`
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
  validateApiKey: (model: string, api_key: string, base_url?: string, backend = 'openai') =>
    request<{ valid: boolean; message: string }>('/api/models/validate', {
      method: 'POST', body: JSON.stringify({ model, api_key, base_url, backend })
    }),

  // 预设连通性测试（服务端用存储密钥，无需前端传 key）
  testPreset: (presetId: number) =>
    request<{ valid: boolean; message: string }>(`/api/models/presets/${presetId}/test`, { method: 'POST' }),

  // 用户获取可选预设
  getAvailablePresets: (agent = 'all') =>
    request<{ presets: ModelPreset[] }>(`/api/models/presets?agent=${encodeURIComponent(agent)}`),
  // 用户选择/清除预设
  selectPreset: (agent: string, preset_id: number | null) =>
    request<any>('/api/models/presets/select', {
      method: 'POST', body: JSON.stringify({ agent, preset_id })
    }),

  // Admin Files
  adminFileUsers: () => request<UserFileStat[]>('/api/admin/files/users'),
  adminFileUserTasks: (userId: number) => request<TaskFileStat[]>(`/api/admin/files/users/${userId}`),
  adminCleanTaskFiles: (taskId: string) => request<any>(`/api/admin/files/tasks/${taskId}`, { method: 'DELETE' }),
  adminGcFiles: () => request<{ ok: boolean; removed: string[]; freed_bytes: number }>('/api/admin/files/gc', { method: 'POST' }),

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
  // Admin preset CRUD
  adminGetPresets: (agent?: string) =>
    request<AdminPresetsView>(`/api/admin/presets${agent ? `?agent=${encodeURIComponent(agent)}` : ''}`),
  adminCreatePreset: (body: Partial<ModelPreset> & { name: string; model: string; api_key?: string }) =>
    request<ModelPreset>('/api/admin/presets', { method: 'POST', body: JSON.stringify(body) }),
  adminUpdatePreset: (id: number, body: Partial<ModelPreset> & { api_key?: string }) =>
    request<ModelPreset>(`/api/admin/presets/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  adminDeletePreset: (id: number) =>
    request<any>(`/api/admin/presets/${id}`, { method: 'DELETE' }),
  adminReorderPresets: (items: { id: number; sort_order: number }[]) =>
    request<any>('/api/admin/presets/reorder', { method: 'PUT', body: JSON.stringify(items) }),
  adminSetDefaultPreset: (id: number) =>
    request<ModelPreset>(`/api/admin/presets/${id}/set-default`, { method: 'POST' }),
  adminUsageOverview: () => request<Overview>('/api/admin/usage/overview'),
  adminUsageByUser: () => request<UserUsage[]>('/api/admin/usage/by-user'),
  adminUsageByModel: () => request<ModelUsage[]>('/api/admin/usage/by-model'),
  adminUserUsage: (id: number) =>
    request<{ user: any; total: UsageSummary; default_model: any; recent: any[] }>(`/api/admin/users/${id}/usage`),

  // 系统设置（OpenAlex email 等）
  adminGetSettings: () =>
    request<SystemSettings>('/api/admin/settings'),
  adminUpdateSettings: (body: Partial<{ openalex_email: string }>) =>
    request<SystemSettings>('/api/admin/settings', {
      method: 'PUT', body: JSON.stringify(body),
    }),

  // WebSocket
  openWS: (id: string, onEvent: (e: TraceEvent) => void, onStatus?: (s: WsStatus) => void) => {
    return new ReconnectingWS(id, onEvent, onStatus)
  }
}

// ---------- WS 状态 ----------
export type WsStatus = 'connecting' | 'connected' | 'reconnecting' | 'closed'

/** 带指数退避自动重连的 WebSocket 封装。 */
export class ReconnectingWS {
  private socket: WebSocket | null = null
  private retries = 0
  private maxRetries = 15
  private closed = false
  private timer: ReturnType<typeof setTimeout> | null = null
  private taskId: string
  private onEvent: (e: TraceEvent) => void
  private onStatus?: (s: WsStatus) => void

  constructor(taskId: string, onEvent: (e: TraceEvent) => void, onStatus?: (s: WsStatus) => void) {
    this.taskId = taskId
    this.onEvent = onEvent
    this.onStatus = onStatus
    this._connect()
  }

  private _connect() {
    if (this.closed) return
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const t = tokenStore.get()
    const url = `${proto}://${location.host}/api/ws/tasks/${this.taskId}?token=${encodeURIComponent(t)}`
    this.onStatus?.(this.retries === 0 ? 'connecting' : 'reconnecting')
    const ws = new WebSocket(url)
    this.socket = ws
    ws.onopen = () => {
      this.retries = 0
      this.onStatus?.('connected')
    }
    ws.onmessage = ev => {
      try {
        const data = JSON.parse(ev.data)
        if (data?.type === 'ping') return  // 过滤心跳
        this.onEvent(data)
      } catch {}
    }
    ws.onclose = ev => {
      if (this.closed) return
      // 4401/4403 认证错误，不重连
      if (ev.code === 4401 || ev.code === 4403) {
        this.closed = true
        this.onStatus?.('closed')
        return
      }
      this.retries++
      if (this.retries > this.maxRetries) {
        this.closed = true
        this.onStatus?.('closed')
        return
      }
      // 指数退避：500ms * 2^retries，最大 30s
      const delay = Math.min(500 * Math.pow(2, this.retries - 1), 30000)
      this.onStatus?.('reconnecting')
      this.timer = setTimeout(() => this._connect(), delay)
    }
    ws.onerror = () => {}  // onclose 会随即触发
  }

  close() {
    this.closed = true
    if (this.timer) clearTimeout(this.timer)
    this.socket?.close()
    this.socket = null
    this.onStatus?.('closed')
  }
}
