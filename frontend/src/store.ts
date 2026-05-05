import { create } from 'zustand'
import { api, Task, TraceEvent, User, tokenStore, setUnauthorizedHandler } from './api'

interface State {
  // 认证
  user: User | null
  authReady: boolean
  login: (username: string, password: string) => Promise<void>
  register: (username: string, email: string, password: string) => Promise<void>
  logout: () => void
  bootstrap: () => Promise<void>

  // 任务
  tasks: Task[]
  currentId: string | null
  current: Task | null
  events: TraceEvent[]
  ws: WebSocket | null

  loadTasks: () => Promise<void>
  selectTask: (id: string) => Promise<void>
  closeCurrent: () => void
  refreshCurrent: () => Promise<void>
  appendEvent: (e: TraceEvent) => void
  removeTask: (id: string) => Promise<void>
}

export const useStore = create<State>((set, get) => ({
  user: null,
  authReady: false,
  tasks: [],
  currentId: null,
  current: null,
  events: [],
  ws: null,

  bootstrap: async () => {
    setUnauthorizedHandler(() => {
      set({ user: null, tasks: [], current: null, currentId: null, events: [], ws: null })
    })
    if (!tokenStore.get()) {
      set({ authReady: true })
      return
    }
    try {
      const u = await api.me()
      set({ user: u, authReady: true })
      await get().loadTasks()
    } catch {
      tokenStore.clear()
      set({ authReady: true })
    }
  },

  login: async (username, password) => {
    const r = await api.login(username, password)
    tokenStore.set(r.access_token)
    set({ user: r.user })
    await get().loadTasks()
  },

  register: async (username, email, password) => {
    const r = await api.register(username, email, password)
    tokenStore.set(r.access_token)
    set({ user: r.user })
    await get().loadTasks()
  },

  logout: () => {
    const { ws } = get()
    if (ws) ws.close()
    tokenStore.clear()
    set({ user: null, tasks: [], current: null, currentId: null, events: [], ws: null })
  },

  loadTasks: async () => {
    const tasks = await api.listTasks()
    set({ tasks })
  },

  selectTask: async (id) => {
    const { ws } = get()
    if (ws) ws.close()
    const [task, events] = await Promise.all([api.getTask(id), api.history(id)])
    set({ currentId: id, current: task, events })
    const newWs = api.openWS(id, e => get().appendEvent(e))
    set({ ws: newWs })
  },

  closeCurrent: () => {
    const { ws } = get()
    if (ws) ws.close()
    set({ currentId: null, current: null, events: [], ws: null })
  },

  refreshCurrent: async () => {
    const id = get().currentId
    if (!id) return
    try {
      const t = await api.getTask(id)
      set({ current: t })
      // 同步任务列表里的状态
      set(s => ({ tasks: s.tasks.map(x => x.task_id === t.task_id ? t : x) }))
    } catch {}
  },

  appendEvent: (e) => {
    set(s => {
      const exists = s.events.some(x => x.event_id === e.event_id)
      const events = exists ? s.events : [...s.events, e]
      return { events }
    })
    if (e.type.startsWith('task.') || e.type.startsWith('hitl.') || e.type.startsWith('phase.')) {
      get().refreshCurrent()
    }
  },

  removeTask: async (id) => {
    await api.deleteTask(id)
    if (get().currentId === id) get().closeCurrent()
    await get().loadTasks()
  }
}))
