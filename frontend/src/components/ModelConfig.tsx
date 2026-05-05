import { useEffect, useRef, useState } from 'react'
import { api, AgentCfg, MyModelsView } from '../api'

const AGENTS = ['default', 'modeler', 'coder', 'writer']
const AGENT_LABEL: Record<string, string> = {
  default: '默认', modeler: 'Modeler', coder: 'Coder', writer: 'Writer',
}
const AGENT_DESC: Record<string, string> = {
  default: '未配置 Agent 的兜底', modeler: '数学建模分析', coder: '代码生成执行', writer: '报告撰写',
}

// ================================================================
// 共用：模型下拉（从 Base URL 拉取）
// ================================================================
interface ModelSelectProps {
  value: string
  onChange: (v: string) => void
  baseUrl: string
  apiKey: string
  agent?: string
  placeholder?: string
}

function ModelSelect({ value, onChange, baseUrl, apiKey, agent = 'default', placeholder }: ModelSelectProps) {
  const [models, setModels] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const prevUrl = useRef('')

  const fetch = async () => {
    if (!baseUrl) return
    setLoading(true); setErr('')
    try {
      const list = await api.listProviderModels(baseUrl, apiKey, agent)
      setModels(list)
      prevUrl.current = baseUrl
    } catch (e: any) {
      setErr(e?.message || '拉取失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <div className="flex gap-1">
        {models.length > 0 ? (
          <select value={value} onChange={e => onChange(e.target.value)}
            className="flex-1 px-2 py-1.5 border border-ink-200 rounded bg-white text-xs focus:outline-none focus:border-ink-500">
            <option value="">— 选择模型 —</option>
            {models.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        ) : (
          <input value={value} onChange={e => onChange(e.target.value)}
            placeholder={placeholder || 'deepseek-chat / gpt-4o / ...'}
            className="flex-1 px-2 py-1.5 border border-ink-200 rounded text-xs focus:outline-none focus:border-ink-500" />
        )}
        <button onClick={fetch} disabled={!baseUrl || loading} title="从 Base URL 拉取模型列表"
          className="px-2.5 py-1.5 bg-ink-100 hover:bg-ink-200 rounded text-xs disabled:opacity-40 whitespace-nowrap border border-ink-200">
          {loading ? '…' : '拉取'}
        </button>
      </div>
      {err && <p className="text-[10px] text-red-500 mt-0.5">{err}</p>}
    </div>
  )
}

// ================================================================
// 共用：Agent 表单（用户 / 管理员复用）
// ================================================================
interface FormState {
  backend: string; model: string; base_url: string; api_key: string
  temperature: number; max_tokens: number
  price_prompt_per_1k?: number; price_completion_per_1k?: number
}

interface AgentFormProps {
  agent: string
  state: FormState
  onChange: (s: FormState) => void
  onSave: () => void
  saving: boolean
  hasKey?: boolean
  showPrice?: boolean
  effectiveModel?: string
  effectiveIsDefault?: boolean
}

function AgentForm({ agent, state, onChange, onSave, saving, hasKey, showPrice, effectiveModel, effectiveIsDefault }: AgentFormProps) {
  const set = (k: keyof FormState, v: any) => onChange({ ...state, [k]: v })

  return (
    <div className="space-y-3 text-xs">
      {/* 生效提示 */}
      {effectiveModel && (
        <div className="flex items-center gap-2 px-3 py-2 bg-ink-50 rounded border border-ink-200 text-[11px]">
          <span className="text-ink-400">当前生效</span>
          <code className="font-mono text-ink-700 flex-1">{effectiveModel}</code>
          <span className={`px-1.5 py-0.5 rounded text-[10px] ${effectiveIsDefault ? 'bg-ink-200 text-ink-600' : 'bg-blue-100 text-blue-700'}`}>
            {effectiveIsDefault ? '默认' : '自定义'}
          </span>
        </div>
      )}

      {/* Backend + Temperature */}
      <div className="flex gap-3">
        <div className="flex-1">
          <p className="text-ink-500 mb-1">Backend</p>
          <select value={state.backend} onChange={e => set('backend', e.target.value)}
            className="w-full px-2 py-1.5 border border-ink-200 rounded bg-white focus:outline-none focus:border-ink-500">
            <option value="openai">openai — OpenAI 兼容（推荐）</option>
            <option value="litellm">litellm — Claude / Gemini / Azure 等</option>
          </select>
        </div>
        <div className="w-28">
          <p className="text-ink-500 mb-1">Temperature</p>
          <input type="number" min={0} max={2} step={0.05} value={state.temperature}
            onChange={e => set('temperature', parseFloat(e.target.value) || 0)}
            className="w-full px-2 py-1.5 border border-ink-200 rounded focus:outline-none focus:border-ink-500" />
        </div>
      </div>

      {/* Base URL */}
      <div>
        <p className="text-ink-500 mb-1">Base URL</p>
        <input value={state.base_url} onChange={e => set('base_url', e.target.value)}
          placeholder="https://api.deepseek.com/v1"
          className="w-full px-2 py-1.5 border border-ink-200 rounded focus:outline-none focus:border-ink-500" />
      </div>

      {/* Model（下拉 + 拉取） */}
      <div>
        <p className="text-ink-500 mb-1">Model</p>
        <ModelSelect value={state.model} onChange={v => set('model', v)}
          baseUrl={state.base_url} apiKey={state.api_key} agent={agent} />
      </div>

      {/* API Key */}
      <div>
        <p className="text-ink-500 mb-1">API Key</p>
        <input type="password" value={state.api_key} onChange={e => set('api_key', e.target.value)}
          placeholder={hasKey ? '已保存（留空则不改动）' : 'sk-...'}
          className="w-full px-2 py-1.5 border border-ink-200 rounded focus:outline-none focus:border-ink-500" />
      </div>

      {/* Max Tokens */}
      <div>
        <p className="text-ink-500 mb-1">Max Tokens
          <span className="ml-1 text-[10px] text-ink-400">（0 = 不限制，由模型默认）</span>
        </p>
        <input type="number" min={0} step={256} value={state.max_tokens}
          onChange={e => set('max_tokens', parseInt(e.target.value) || 0)}
          placeholder="0"
          className="w-full px-2 py-1.5 border border-ink-200 rounded focus:outline-none focus:border-ink-500" />
      </div>

      {/* 计费单价（管理员专用） */}
      {showPrice && (
        <div className="flex gap-3">
          <div className="flex-1">
            <p className="text-ink-500 mb-1">$/1K prompt</p>
            <input type="number" min={0} step={0.0001} value={state.price_prompt_per_1k ?? 0}
              onChange={e => set('price_prompt_per_1k', parseFloat(e.target.value) || 0)}
              className="w-full px-2 py-1.5 border border-ink-200 rounded focus:outline-none focus:border-ink-500" />
          </div>
          <div className="flex-1">
            <p className="text-ink-500 mb-1">$/1K completion</p>
            <input type="number" min={0} step={0.0001} value={state.price_completion_per_1k ?? 0}
              onChange={e => set('price_completion_per_1k', parseFloat(e.target.value) || 0)}
              className="w-full px-2 py-1.5 border border-ink-200 rounded focus:outline-none focus:border-ink-500" />
          </div>
        </div>
      )}

      <button onClick={onSave} disabled={saving}
        className="w-full py-2 bg-ink-800 text-white rounded hover:bg-ink-700 disabled:opacity-40 font-medium text-xs">
        {saving ? '保存中…' : '保存'}
      </button>
    </div>
  )
}

// ================================================================
// Agent Tab 导航
// ================================================================
function AgentTabs({ active, onSelect }: { active: string; onSelect: (a: string) => void }) {
  return (
    <div className="flex border-b border-ink-200 mb-4">
      {AGENTS.map(a => (
        <button key={a} onClick={() => onSelect(a)}
          className={`px-4 py-2 text-xs font-medium border-b-2 transition-colors ${
            active === a
              ? 'border-ink-800 text-ink-800'
              : 'border-transparent text-ink-400 hover:text-ink-600'
          }`}>
          {AGENT_LABEL[a]}
          <span className="block text-[10px] font-normal opacity-60">{AGENT_DESC[a]}</span>
        </button>
      ))}
    </div>
  )
}

// ================================================================
// 用户侧模型配置
// ================================================================
export function ModelConfig({ embedded = false, readonly = false }: { embedded?: boolean; readonly?: boolean }) {
  const [view, setView] = useState<MyModelsView | null>(null)
  const [activeAgent, setActiveAgent] = useState('default')
  const [drafts, setDrafts] = useState<Record<string, FormState>>({})
  const [saving, setSaving] = useState('')
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)

  const load = async () => {
    const v = await api.getMyModels()
    setView(v)
    const d: Record<string, FormState> = {}
    AGENTS.forEach(a => {
      const mine = v.mine[a]
      d[a] = {
        backend:     mine?.backend     || v.defaults[a]?.backend     || 'openai',
        model:       mine?.model       || '',
        base_url:    mine?.base_url    || '',
        api_key:     '',
        temperature: mine?.temperature ?? 0.2,
        max_tokens:  mine?.max_tokens  ?? 0,
      }
    })
    setDrafts(d)
  }
  useEffect(() => { load() }, [])

  const toggle = async (useDefault: boolean) => {
    await api.toggleDefault(useDefault); await load()
  }

  const save = async () => {
    const d = drafts[activeAgent]
    if (!d) return
    setSaving(activeAgent); setMsg(null)
    try {
      await api.updateMyModel({ agent: activeAgent, backend: d.backend, model: d.model, base_url: d.base_url, api_key: d.api_key || '', temperature: d.temperature, max_tokens: d.max_tokens || undefined })
      setMsg({ text: `${AGENT_LABEL[activeAgent]} 已保存`, ok: true })
      await load()
    } catch (e: any) {
      setMsg({ text: e?.message || '保存失败', ok: false })
    } finally { setSaving('') }
  }

  if (!view) return <div className="flex items-center justify-center h-32 text-xs text-ink-400">加载中…</div>

  const mode = view.use_default_model ? 'default' : 'custom'
  const draft = drafts[activeAgent]
  const eff = view.effective[activeAgent]

  return (
    <div className={embedded ? 'text-xs' : 'text-xs p-1'}>
      {/* 模型来源切换 */}
      {!readonly && (
        <div className="flex items-center gap-2 mb-5 p-3 bg-ink-50 rounded border border-ink-200">
          <div className="flex bg-white rounded border border-ink-200 overflow-hidden text-xs">
            <button onClick={() => toggle(true)}
              className={`px-3 py-1.5 transition-colors ${mode === 'default' ? 'bg-ink-800 text-white font-medium' : 'text-ink-500 hover:text-ink-700'}`}>
              使用管理员默认
            </button>
            <button onClick={() => toggle(false)}
              className={`px-3 py-1.5 transition-colors ${mode === 'custom' ? 'bg-ink-800 text-white font-medium' : 'text-ink-500 hover:text-ink-700'}`}>
              自定义
            </button>
          </div>
          <p className="text-[11px] text-ink-400 flex-1">
            {mode === 'default'
              ? '使用管理员配置的全局模型，用量计入「默认模型」统计。'
              : '自定义各 Agent 模型。未填写的 Agent 自动回退到全局默认。'}
          </p>
        </div>
      )}

      {/* Agent Tab */}
      <AgentTabs active={activeAgent} onSelect={setActiveAgent} />

      {/* 表单 */}
      {draft && (
        readonly ? (
          // 只读模式：展示生效配置
          <div className="space-y-2 text-[11px] text-ink-600">
            <Row label="Backend"     value={eff?.backend || '—'} />
            <Row label="Model"       value={eff?.model || '未配置'} />
            <Row label="Base URL"    value={eff?.base_url || '—'} />
            <Row label="API Key"     value={eff?.has_api_key ? '✓ 已配置' : '✗ 未配置'} />
            <Row label="Temperature" value={String(eff?.temperature ?? '—')} />
            <Row label="来源"        value={eff?.is_default ? '全局默认' : '用户自定义'} />
          </div>
        ) : mode === 'custom' ? (
          <AgentForm
            agent={activeAgent}
            state={draft}
            onChange={s => setDrafts(prev => ({ ...prev, [activeAgent]: s }))}
            onSave={save}
            saving={saving === activeAgent}
            hasKey={!!view.mine[activeAgent]?.has_api_key}
            effectiveModel={eff?.model}
            effectiveIsDefault={eff?.is_default}
          />
        ) : (
          // default 模式：只读展示全局配置
          <div className="space-y-2 text-[11px] text-ink-600">
            <p className="text-[11px] text-ink-400 mb-3">当前使用全局默认配置（只读）</p>
            <Row label="Backend"     value={view.defaults[activeAgent]?.backend || '—'} />
            <Row label="Model"       value={view.defaults[activeAgent]?.model || '未配置'} />
            <Row label="Base URL"    value={view.defaults[activeAgent]?.base_url || '—'} />
            <Row label="API Key"     value={view.defaults[activeAgent]?.has_api_key ? '✓ 已配置' : '✗ 未配置'} />
            <Row label="Temperature" value={String(view.defaults[activeAgent]?.temperature ?? '—')} />
          </div>
        )
      )}

      {msg && (
        <p className={`mt-3 text-[11px] ${msg.ok ? 'text-emerald-700' : 'text-red-600'}`}>{msg.text}</p>
      )}
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 rounded bg-ink-50">
      <span className="text-ink-400 w-24 shrink-0">{label}</span>
      <code className="text-ink-700 flex-1 break-all">{value}</code>
    </div>
  )
}

// ================================================================
// 管理员侧：全局默认模型配置
// ================================================================
export function AdminModelConfig() {
  const [view, setView] = useState<{ agents: string[]; defaults: Record<string, AgentCfg> } | null>(null)
  const [activeAgent, setActiveAgent] = useState('default')
  const [drafts, setDrafts] = useState<Record<string, FormState>>({})
  const [saving, setSaving] = useState('')
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)

  const load = async () => {
    const v = await api.adminGetDefaults()
    setView(v)
    const d: Record<string, FormState> = {}
    AGENTS.forEach(a => {
      const m = v.defaults[a]
      d[a] = {
        backend:                m?.backend || 'openai',
        model:                  m?.model || '',
        base_url:               m?.base_url || '',
        api_key:                '',
        temperature:            m?.temperature ?? 0.2,
        max_tokens:             m?.max_tokens ?? 0,
        price_prompt_per_1k:    m?.price_prompt_per_1k ?? 0,
        price_completion_per_1k: m?.price_completion_per_1k ?? 0,
      }
    })
    setDrafts(d)
  }
  useEffect(() => { load() }, [])

  const save = async () => {
    const d = drafts[activeAgent]
    if (!d) return
    setSaving(activeAgent); setMsg(null)
    try {
      await api.adminUpdateDefault({
        agent: activeAgent, backend: d.backend, model: d.model, base_url: d.base_url,
        api_key: d.api_key || '', temperature: d.temperature,
        max_tokens: d.max_tokens || undefined,
        price_prompt_per_1k: d.price_prompt_per_1k,
        price_completion_per_1k: d.price_completion_per_1k,
      })
      setMsg({ text: `${AGENT_LABEL[activeAgent]} 已保存`, ok: true })
      await load()
    } catch (e: any) {
      setMsg({ text: e?.message || '失败', ok: false })
    } finally { setSaving('') }
  }

  if (!view) return <div className="flex items-center justify-center h-32 text-xs text-ink-400">加载中…</div>

  const draft = drafts[activeAgent]
  const cur   = view.defaults[activeAgent]

  return (
    <div className="p-5 text-xs">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-sm font-semibold">全局默认模型</h3>
        {cur && (
          <span className="text-[11px] text-ink-400">
            当前：<code className="text-ink-600">{cur.model || '未配置'}</code>
            {cur.has_api_key ? ' · key✓' : ' · key✗'}
          </span>
        )}
      </div>
      <p className="text-[11px] text-ink-400 mb-4">
        未自定义模型的用户使用此配置，计费单价用于 Token 成本统计。
      </p>

      <AgentTabs active={activeAgent} onSelect={setActiveAgent} />

      {draft && (
        <AgentForm
          agent={activeAgent}
          state={draft}
          onChange={s => setDrafts(prev => ({ ...prev, [activeAgent]: s }))}
          onSave={save}
          saving={saving === activeAgent}
          hasKey={!!cur?.has_api_key}
          showPrice
        />
      )}

      {msg && (
        <p className={`mt-3 text-[11px] ${msg.ok ? 'text-emerald-700' : 'text-red-600'}`}>{msg.text}</p>
      )}
    </div>
  )
}
