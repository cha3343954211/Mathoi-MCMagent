import { useEffect, useRef, useState } from 'react'
import { api, AgentCfg, ModelPreset, MyModelsView } from '../api'

const AGENTS = ['default', 'coordinator', 'modeler', 'coder', 'writer']
const AGENT_LABEL: Record<string, string> = {
  default: '默认', coordinator: 'Coordinator', modeler: 'Modeler', coder: 'Coder', writer: 'Writer',
}
const AGENT_DESC: Record<string, string> = {
  default: '未配置 Agent 的兜底',
  coordinator: '问题结构化',
  modeler: '数学建模分析',
  coder: '代码生成执行',
  writer: '报告撰写',
}
const PRESET_AGENT_LABELS: Record<string, string> = {
  all: '全部 Agent', ...AGENT_LABEL,
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

// ================================================================
// ValidateKeyBtn —— 实时验证 API Key 可用性
// ================================================================
function ValidateKeyBtn({ model, apiKey, baseUrl }: { model: string; apiKey: string; baseUrl: string }) {
  const [status, setStatus] = useState<'idle' | 'loading' | 'ok' | 'fail'>('idle')
  const [msg, setMsg]       = useState('')

  const validate = async () => {
    if (!model || !apiKey) { setMsg('请先填写 Model 和 API Key'); setStatus('fail'); return }
    setStatus('loading'); setMsg('')
    try {
      const r = await api.validateApiKey(model, apiKey, baseUrl || undefined)
      setStatus(r.valid ? 'ok' : 'fail')
      setMsg(r.message)
    } catch (e: any) {
      setStatus('fail'); setMsg(e?.message || '请求失败')
    }
    setTimeout(() => setStatus('idle'), 8000)
  }

  return (
    <div className="mt-1.5 flex items-center gap-2">
      <button type="button" onClick={validate} disabled={status === 'loading' || !model || !apiKey}
        className="text-[11px] px-2.5 py-1 rounded border border-ink-200 text-ink-600 hover:bg-ink-50 disabled:opacity-40">
        {status === 'loading' ? '验证中…' : '验证连通性'}
      </button>
      {msg && (
        <span className={`text-[11px] ${status === 'ok' ? 'text-emerald-600' : 'text-red-500'}`}>{msg}</span>
      )}
    </div>
  )
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

      {/* API Key + 验证 */}
      <div>
        <p className="text-ink-500 mb-1">API Key</p>
        <input type="password" value={state.api_key} onChange={e => set('api_key', e.target.value)}
          placeholder={hasKey ? '已保存（留空则不改动）' : 'sk-...'}
          className="w-full px-2 py-1.5 border border-ink-200 rounded focus:outline-none focus:border-ink-500" />
        <ValidateKeyBtn model={state.model} apiKey={state.api_key} baseUrl={state.base_url} />
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
type ModelMode = 'default' | 'custom'

export function ModelConfig({ embedded = false, readonly = false }: { embedded?: boolean; readonly?: boolean }) {
  const [view, setView] = useState<MyModelsView | null>(null)
  const [activeAgent, setActiveAgent] = useState('default')
  const [drafts, setDrafts] = useState<Record<string, FormState>>({})
  const [saving, setSaving] = useState('')
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)
  // 预设列表（全部 agent 一次性加载）
  const [presets, setPresets] = useState<ModelPreset[]>([])
  // 每个 agent 当前选中的 preset_id
  const [selectedPresets, setSelectedPresets] = useState<Record<string, number | null>>({})
  // 每个 agent 的模式
  const [modes, setModes] = useState<Record<string, ModelMode>>({})

  const load = async () => {
    const [v, pr] = await Promise.all([
      api.getMyModels(),
      api.getAvailablePresets('all').catch(() => ({ presets: [] })),
    ])
    setView(v)
    setPresets(pr.presets)

    const d: Record<string, FormState> = {}
    const sp: Record<string, number | null> = {}
    const md: Record<string, ModelMode> = {}
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
      // 模式：有预设选择时用预设，否则按 use_default_model
      const mineAny = mine as any
      sp[a] = mineAny?.selected_preset_id ?? null
      md[a] = v.use_default_model ? 'default' : 'custom'
    })
    setDrafts(d)
    setSelectedPresets(sp)
    setModes(md)
  }
  useEffect(() => { load() }, [])

  const switchMode = async (agent: string, mode: ModelMode) => {
    setMsg(null)
    // 切换 default/custom 时清除该 agent 的预设选择
    await api.selectPreset(agent, null)
    await api.toggleDefault(mode === 'default')
    setSelectedPresets(prev => ({ ...prev, [agent]: null }))
    setModes(prev => ({ ...prev, [agent]: mode }))
    await load()
  }

  const applyPreset = async (agent: string, presetId: number | null) => {
    setMsg(null)
    setSaving(agent)
    try {
      await api.selectPreset(agent, presetId)
      setSelectedPresets(prev => ({ ...prev, [agent]: presetId }))
      setMsg({ text: presetId ? '预设已选择' : '预设已清除', ok: true })
      await load()
    } catch (e: any) {
      setMsg({ text: e?.message || '操作失败', ok: false })
    } finally { setSaving('') }
  }

  const save = async () => {
    const d = drafts[activeAgent]
    if (!d) return
    setSaving(activeAgent); setMsg(null)
    try {
      await api.updateMyModel({
        agent: activeAgent, backend: d.backend, model: d.model,
        base_url: d.base_url, api_key: d.api_key || '',
        temperature: d.temperature, max_tokens: d.max_tokens || undefined,
      })
      setMsg({ text: `${AGENT_LABEL[activeAgent]} 已保存`, ok: true })
      await load()
    } catch (e: any) {
      setMsg({ text: e?.message || '保存失败', ok: false })
    } finally { setSaving('') }
  }

  if (!view) return <div className="flex items-center justify-center h-32 text-xs text-ink-400">加载中…</div>

  const mode = modes[activeAgent] || 'default'
  const selectedPresetId = selectedPresets[activeAgent] ?? null
  const draft = drafts[activeAgent]
  const eff = view.effective[activeAgent]
  // 支持逗号分隔多值，如 'modeler,coder'
  const agentPresets = presets.filter(p => {
    const agents = p.agent.split(',').map(a => a.trim())
    return agents.includes('all') || agents.includes(activeAgent)
  })

  return (
    <div className={embedded ? 'text-xs' : 'text-xs p-1'}>
      {/* Agent Tab */}
      <AgentTabs active={activeAgent} onSelect={setActiveAgent} />

      {/* 内容区 */}
      {readonly ? (
        <div className="space-y-2 text-[11px] text-ink-600">
          <Row label="Backend"     value={eff?.backend || '—'} />
          <Row label="Model"       value={eff?.model || '未配置'} />
          <Row label="Base URL"    value={eff?.base_url || '—'} />
          <Row label="API Key"     value={eff?.has_api_key ? '✓ 已配置' : '✗ 未配置'} />
          <Row label="Temperature" value={String(eff?.temperature ?? '—')} />
          <Row label="来源"        value={eff?.is_default ? '全局默认' : '用户自定义'} />
        </div>
      ) : (
        <>
          {/* 预设列表（直接可点选，无需先切 tab） */}
          {agentPresets.length > 0 && (
            <PresetSelector
              presets={agentPresets}
              selectedId={selectedPresetId}
              onSelect={id => applyPreset(activeAgent, id)}
              saving={saving === activeAgent}
            />
          )}

          {/* 无预设时：自定义配置（可折叠） */}
          {selectedPresetId === null && (
            <details className={agentPresets.length > 0 ? 'mt-4' : 'mt-2'}
              open={mode === 'custom'}>
              <summary
                onClick={e => {
                  e.preventDefault()
                  switchMode(activeAgent, mode === 'custom' ? 'default' : 'custom')
                }}
                className="cursor-pointer select-none flex items-center gap-2 text-[11px] text-ink-500 hover:text-ink-700 list-none py-1">
                <span className={`inline-block w-3 text-center transition-transform ${mode === 'custom' ? 'rotate-90' : ''}`}>▶</span>
                {mode === 'custom' ? '收起自定义配置' : '展开自定义配置（使用自己的 API Key）'}
              </summary>
              {mode === 'custom' && draft && (
                <div className="mt-2">
                  <AgentForm
                    agent={activeAgent} state={draft}
                    onChange={s => setDrafts(prev => ({ ...prev, [activeAgent]: s }))}
                    onSave={save} saving={saving === activeAgent}
                    hasKey={!!view.mine[activeAgent]?.has_api_key}
                    effectiveModel={eff?.model} effectiveIsDefault={eff?.is_default}
                  />
                </div>
              )}
            </details>
          )}
        </>
      )}

      {msg && (
        <p className={`mt-3 text-[11px] ${msg.ok ? 'text-emerald-700' : 'text-red-600'}`}>{msg.text}</p>
      )}
    </div>
  )
}

// ================================================================
// 用户侧预设选择器
// ================================================================
function PresetCard({
  p, selectedId, saving, onSelect,
}: {
  p: ModelPreset; selectedId: number | null; saving: boolean
  onSelect: (id: number | null) => void
}) {
  const isSelected = selectedId === p.id
  const isAutoDefault = p.is_default && selectedId === null  // 自动生效中（未显式选择）
  return (
    <div
      onClick={() => !saving && onSelect(isSelected ? null : p.id)}
      className={`flex items-start gap-3 px-3 py-2.5 rounded border cursor-pointer transition-colors ${
        isSelected
          ? 'border-blue-500 bg-blue-50'
          : isAutoDefault
          ? 'border-amber-300 bg-amber-50 hover:border-amber-400'
          : 'border-ink-200 bg-white hover:border-ink-400'
      }`}
    >
      {/* 选中指示圆 */}
      <div className="w-3 h-3 rounded-full border-2 flex items-center justify-center flex-shrink-0 mt-0.5"
        style={{ borderColor: isSelected ? '#3b82f6' : isAutoDefault ? '#f59e0b' : '#9ca3af' }}>
        {isSelected   && <div className="w-1.5 h-1.5 rounded-full bg-blue-500" />}
        {isAutoDefault && <div className="w-1.5 h-1.5 rounded-full bg-amber-500" />}
      </div>
      {/* 内容 */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-semibold text-ink-800">{p.name}</span>
          {isAutoDefault && (
            <span className="px-1.5 py-0.5 bg-amber-200 text-amber-800 text-[10px] rounded font-medium">
              ★ 自动生效中
            </span>
          )}
          {p.is_default && !isAutoDefault && (
            <span className="px-1.5 py-0.5 bg-amber-100 text-amber-700 text-[10px] rounded">★ 默认</span>
          )}
          {p.agent.split(',').map(a => a.trim()).filter(Boolean).map(a => (
            <span key={a} className="px-1.5 py-0.5 bg-ink-100 text-ink-500 text-[10px] rounded">
              {a === 'all' ? '通用' : (AGENT_LABEL[a] || a)}
            </span>
          ))}
          {p.has_api_key && (
            <span className="px-1.5 py-0.5 bg-emerald-100 text-emerald-700 text-[10px] rounded">key✓</span>
          )}
        </div>
        <div className="flex items-center gap-3 mt-1 text-[11px] text-ink-500">
          <code>{p.model}</code>
          <span>·</span>
          <span>temp {p.temperature}</span>
          {p.description && <><span>·</span><span className="truncate max-w-[160px]">{p.description}</span></>}
        </div>
        {isAutoDefault && (
          <p className="mt-1 text-[10px] text-amber-600">
            点击可显式锁定此预设；或展开下方自定义配置覆盖。
          </p>
        )}
      </div>
    </div>
  )
}

function PresetSelector({
  presets, selectedId, onSelect, saving,
}: {
  presets: ModelPreset[]
  selectedId: number | null
  onSelect: (id: number | null) => void
  saving: boolean
}) {
  const defaultPreset = presets.find(p => p.is_default)
  const otherPresets  = presets.filter(p => !p.is_default)

  if (presets.length === 0) {
    return (
      <div className="py-6 text-center text-[11px] text-ink-400">
        管理员尚未配置预设，将使用 env 兜底配置。
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {/* 默认预设卡片：始终置顶，未选时显示"自动生效中" */}
      {defaultPreset && (
        <PresetCard p={defaultPreset} selectedId={selectedId} saving={saving} onSelect={onSelect} />
      )}

      {/* 其他可选预设 */}
      {otherPresets.length > 0 && (
        <>
          {defaultPreset && (
            <div className="flex items-center gap-2 py-1">
              <div className="flex-1 h-px bg-ink-100" />
              <span className="text-[10px] text-ink-400">其他预设</span>
              <div className="flex-1 h-px bg-ink-100" />
            </div>
          )}
          {otherPresets.map(p => (
            <PresetCard key={p.id} p={p} selectedId={selectedId} saving={saving} onSelect={onSelect} />
          ))}
        </>
      )}

      {selectedId !== null && (
        <p className="text-[10px] text-ink-400 text-center">再次点击已选预设可取消</p>
      )}
      {saving && <p className="text-[10px] text-ink-400 text-center">保存中…</p>}
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
// 适用 Agent 多选组件
// ================================================================
const ALL_PRESET_AGENTS = ['all', 'default', 'coordinator', 'modeler', 'coder', 'writer']

function AgentMultiSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  // 解析逗号分隔字符串为 Set
  const selected = new Set(value.split(',').map(a => a.trim()).filter(Boolean))

  const toggle = (a: string) => {
    const next = new Set(selected)
    if (a === 'all') {
      // 选 all → 清除其他
      onChange('all')
      return
    }
    next.delete('all')
    if (next.has(a)) {
      next.delete(a)
      if (next.size === 0) next.add('all')
    } else {
      next.add(a)
    }
    onChange([...next].join(','))
  }

  return (
    <div>
      <p className="text-ink-500 mb-1.5">适用 Agent
        <span className="ml-1 text-[10px] text-ink-400">（可多选；选"全部"则所有 Agent 均可见）</span>
      </p>
      <div className="flex flex-wrap gap-1.5">
        {ALL_PRESET_AGENTS.map(a => {
          const isOn = selected.has(a)
          return (
            <button
              key={a} type="button"
              onClick={() => toggle(a)}
              className={`px-2.5 py-1 rounded border text-[11px] font-medium transition-colors ${
                isOn
                  ? 'bg-ink-800 text-white border-ink-800'
                  : 'bg-white text-ink-500 border-ink-200 hover:border-ink-400'
              }`}>
              {PRESET_AGENT_LABELS[a] || a}
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ================================================================
// 管理员侧：仅预设管理（默认预设即替代原全局默认配置）
// ================================================================
export function AdminModelConfig() {
  return (
    <div className="p-5 text-xs">
      <AdminPresetsPanel />
    </div>
  )
}

// ── 预设管理面板 ──────────────────────────────────────────────────
const EMPTY_PRESET_FORM = (): PresetFormState => ({
  name: '', description: '', agent: 'all',
  backend: 'openai', model: '', base_url: '', api_key: '',
  temperature: 0.2, max_tokens: 0,
  price_prompt_per_1k: 0, price_completion_per_1k: 0,
  sort_order: 0, is_active: true, pro_only: false,
})

interface PresetFormState {
  name: string; description: string; agent: string
  backend: string; model: string; base_url: string; api_key: string
  temperature: number; max_tokens: number
  price_prompt_per_1k: number; price_completion_per_1k: number
  sort_order: number; is_active: boolean; pro_only: boolean
}

function AdminPresetsPanel() {
  const [presets, setPresets] = useState<ModelPreset[]>([])
  const [filterAgent, setFilterAgent] = useState('all')
  const [editId, setEditId] = useState<number | null>(null)   // null = 新建
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<PresetFormState>(EMPTY_PRESET_FORM())
  const [saving, setSaving] = useState(false)
  const [settingDefault, setSettingDefault] = useState<number | null>(null)
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)
  // 拖拽排序状态
  const [dragIdx, setDragIdx] = useState<number | null>(null)
  const [dragOverIdx, setDragOverIdx] = useState<number | null>(null)

  const load = async () => {
    const v = await api.adminGetPresets()
    setPresets(v.presets)
  }
  useEffect(() => { load() }, [])

  const openNew = () => {
    setEditId(null)
    setForm(EMPTY_PRESET_FORM())
    setMsg(null)
    setShowForm(true)
  }

  const openEdit = (p: ModelPreset) => {
    setEditId(p.id)
    setForm({
      name: p.name, description: p.description, agent: p.agent,
      backend: p.backend, model: p.model, base_url: p.base_url, api_key: '',
      temperature: p.temperature, max_tokens: p.max_tokens ?? 0,
      price_prompt_per_1k: p.price_prompt_per_1k ?? 0,
      price_completion_per_1k: p.price_completion_per_1k ?? 0,
      sort_order: p.sort_order ?? 0, is_active: p.is_active ?? true,
      pro_only: p.pro_only ?? false,
    })
    setMsg(null)
    setShowForm(true)
  }

  const savePreset = async () => {
    if (!form.name.trim() || !form.model.trim()) {
      setMsg({ text: '名称和模型不能为空', ok: false }); return
    }
    setSaving(true); setMsg(null)
    try {
      const body = {
        name: form.name, description: form.description, agent: form.agent,
        backend: form.backend, model: form.model, base_url: form.base_url,
        api_key: form.api_key, temperature: form.temperature,
        max_tokens: form.max_tokens || undefined,
        price_prompt_per_1k: form.price_prompt_per_1k,
        price_completion_per_1k: form.price_completion_per_1k,
        sort_order: form.sort_order, is_active: form.is_active,
        pro_only: form.pro_only,
      }
      if (editId === null) {
        await api.adminCreatePreset(body)
        setMsg({ text: '预设已创建', ok: true })
      } else {
        await api.adminUpdatePreset(editId, body)
        setMsg({ text: '预设已更新', ok: true })
      }
      await load()
      setShowForm(false)
    } catch (e: any) {
      setMsg({ text: e?.message || '保存失败', ok: false })
    } finally { setSaving(false) }
  }

  const toggleActive = async (p: ModelPreset) => {
    try {
      await api.adminUpdatePreset(p.id, { is_active: !p.is_active })
      await load()
    } catch (e: any) {
      setMsg({ text: e?.message || '操作失败', ok: false })
    }
  }

  const setAsDefault = async (p: ModelPreset) => {
    setSettingDefault(p.id)
    try {
      await api.adminSetDefaultPreset(p.id)
      setMsg({ text: `「${p.name}」已设为默认预设`, ok: true })
      await load()
    } catch (e: any) {
      setMsg({ text: e?.message || '操作失败', ok: false })
    } finally { setSettingDefault(null) }
  }

  const deletePreset = async (p: ModelPreset) => {
    if (!confirm(`确认删除预设「${p.name}」？此操作不可撤销。`)) return
    try {
      await api.adminDeletePreset(p.id)
      await load()
    } catch (e: any) {
      setMsg({ text: e?.message || '删除失败', ok: false })
    }
  }

  const handleDrop = async (toIdx: number) => {
    if (dragIdx === null || dragIdx === toIdx) {
      setDragIdx(null); setDragOverIdx(null); return
    }
    // 在当前过滤视图中做重排（对全量 presets 也要同步）
    const next = [...filtered]
    const [moved] = next.splice(dragIdx, 1)
    next.splice(toIdx, 0, moved)
    // 重新赋 sort_order（步进 10，留余量）
    const reordered = next.map((p, i) => ({ ...p, sort_order: i * 10 }))
    // 乐观更新：把重排结果合并回 presets
    setPresets(prev => {
      const map = new Map(reordered.map(p => [p.id, p]))
      return prev.map(p => map.get(p.id) ?? p)
        .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0))
    })
    setDragIdx(null); setDragOverIdx(null)
    try {
      await api.adminReorderPresets(reordered.map(p => ({ id: p.id, sort_order: p.sort_order })))
    } catch {
      await load()  // 失败则从服务端重新加载
    }
  }

  const set = (k: keyof PresetFormState, v: any) => setForm(prev => ({ ...prev, [k]: v }))

  const filtered = filterAgent === 'all'
    ? presets
    : presets.filter(p => {
        const agents = p.agent.split(',').map(a => a.trim())
        return agents.includes('all') || agents.includes(filterAgent)
      })

  return (
    <div>
      {/* 工具栏 */}
      <div className="flex items-center gap-3 mb-4">
        <p className="text-[11px] text-ink-400 flex-1">
          配置命名预设供用户选择；标记为「默认」的预设自动作为兜底配置（替代原全局默认）。
        </p>
        {/* 过滤 */}
        <select value={filterAgent} onChange={e => setFilterAgent(e.target.value)}
          className="px-2 py-1.5 border border-ink-200 rounded text-[11px] bg-white focus:outline-none">
          {(['all', ...AGENTS] as string[]).map(a => (
            <option key={a} value={a}>{PRESET_AGENT_LABELS[a] || a}</option>
          ))}
        </select>
        <button onClick={openNew}
          className="px-3 py-1.5 bg-ink-800 text-white rounded hover:bg-ink-700 text-xs font-medium">
          + 新建预设
        </button>
      </div>

      {/* 预设列表 */}
      {filtered.length === 0 ? (
        <div className="text-center py-12 text-[11px] text-ink-400">
          暂无预设，点击「新建预设」添加。
        </div>
      ) : (
        <div className="space-y-1">
          {filtered.map((p, idx) => (
            <div key={p.id}
              draggable
              onDragStart={e => { setDragIdx(idx); e.dataTransfer.effectAllowed = 'move' }}
              onDragOver={e => { e.preventDefault(); setDragOverIdx(idx) }}
              onDrop={e => { e.preventDefault(); handleDrop(idx) }}
              onDragEnd={() => { setDragIdx(null); setDragOverIdx(null) }}
              className={`flex items-center gap-3 px-3 py-3 rounded border transition-colors cursor-default
                ${dragOverIdx === idx && dragIdx !== idx ? 'border-t-2 border-t-blue-500' : ''}
                ${dragIdx === idx ? 'opacity-40' : ''}
                ${p.is_default ? 'border-amber-300 bg-amber-50' :
                  p.is_active  ? 'border-ink-200 bg-white' : 'border-ink-100 bg-ink-50 opacity-60'
                }`}>
              {/* Drag handle */}
              <span className="text-ink-300 hover:text-ink-500 cursor-grab active:cursor-grabbing select-none text-base shrink-0"
                title="拖拽排序">⠿</span>
              {/* 主信息 */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-semibold text-ink-800">{p.name}</span>
                  {p.pro_only && (
                    <span className="px-1.5 py-0.5 bg-purple-100 text-purple-700 text-[10px] rounded font-medium">
                      Pro
                    </span>
                  )}
                  {p.is_default && (
                    <span className="px-1.5 py-0.5 bg-amber-100 text-amber-700 text-[10px] rounded font-medium">
                      ★ 默认
                    </span>
                  )}
                  {/* 多 agent 标签 */}
                  {p.agent.split(',').map(a => a.trim()).filter(Boolean).map(a => (
                    <span key={a} className="px-1.5 py-0.5 bg-ink-100 text-ink-500 text-[10px] rounded">
                      {PRESET_AGENT_LABELS[a] || a}
                    </span>
                  ))}
                  <span className="px-1.5 py-0.5 bg-blue-50 text-blue-600 text-[10px] rounded">
                    {p.backend}
                  </span>
                  {!p.is_active && (
                    <span className="px-1.5 py-0.5 bg-orange-50 text-orange-500 text-[10px] rounded">
                      已禁用
                    </span>
                  )}
                  {p.has_api_key && (
                    <span className="px-1.5 py-0.5 bg-emerald-50 text-emerald-600 text-[10px] rounded">key✓</span>
                  )}
                </div>
                <div className="flex items-center gap-3 mt-0.5 text-[11px] text-ink-500">
                  <code className="text-ink-700">{p.model}</code>
                  <span>temp {p.temperature}</span>
                  {p.max_tokens ? <span>max_tokens {p.max_tokens}</span> : null}
                  {p.price_prompt_per_1k ? <span>${p.price_prompt_per_1k}/1K↑</span> : null}
                  {p.description && <span className="truncate max-w-[200px]">{p.description}</span>}
                </div>
              </div>
              {/* 操作 */}
              <div className="flex items-center gap-1 shrink-0">
                {!p.is_default && (
                  <button onClick={() => setAsDefault(p)} disabled={settingDefault === p.id}
                    className="px-2.5 py-1 text-[11px] rounded border border-amber-200 text-amber-600 hover:bg-amber-50 bg-white disabled:opacity-40">
                    {settingDefault === p.id ? '…' : '设为默认'}
                  </button>
                )}
                <button onClick={() => openEdit(p)}
                  className="px-2.5 py-1 text-[11px] rounded border border-ink-200 hover:border-ink-400 bg-white">
                  编辑
                </button>
                <button onClick={() => toggleActive(p)}
                  className="px-2.5 py-1 text-[11px] rounded border border-ink-200 hover:border-ink-400 bg-white">
                  {p.is_active ? '禁用' : '启用'}
                </button>
                <button onClick={() => deletePreset(p)}
                  className="px-2.5 py-1 text-[11px] rounded border border-red-200 text-red-500 hover:bg-red-50 bg-white">
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {msg && !showForm && (
        <p className={`mt-3 text-[11px] ${msg.ok ? 'text-emerald-700' : 'text-red-600'}`}>{msg.text}</p>
      )}

      {/* 新建 / 编辑表单（内联展开） */}
      {showForm && (
        <div className="mt-4 border border-ink-300 rounded-lg p-4 bg-ink-50 space-y-3">
          <div className="flex items-center justify-between mb-2">
            <h4 className="font-semibold text-ink-800">{editId === null ? '新建预设' : '编辑预设'}</h4>
            <button onClick={() => setShowForm(false)} className="text-ink-400 hover:text-ink-700 text-lg leading-none">✕</button>
          </div>

          {/* 名称 */}
          <div>
            <p className="text-ink-500 mb-1">预设名称 *</p>
            <input value={form.name} onChange={e => set('name', e.target.value)}
              placeholder="DeepSeek Chat / GPT-4o / Claude 3.5..."
              className="w-full px-2 py-1.5 border border-ink-200 rounded focus:outline-none focus:border-ink-500" />
          </div>

          {/* 适用 Agent 多选 */}
          <AgentMultiSelect value={form.agent} onChange={v => set('agent', v)} />

          {/* Backend & Temperature */}
          <div className="flex gap-3">
            <div className="flex-1">
              <p className="text-ink-500 mb-1">Backend</p>
              <select value={form.backend} onChange={e => set('backend', e.target.value)}
                className="w-full px-2 py-1.5 border border-ink-200 rounded bg-white focus:outline-none focus:border-ink-500">
                <option value="openai">openai — OpenAI 兼容（推荐）</option>
                <option value="litellm">litellm — Claude / Gemini / Azure 等</option>
              </select>
            </div>
            <div className="w-28">
              <p className="text-ink-500 mb-1">Temperature</p>
              <input type="number" min={0} max={2} step={0.05} value={form.temperature}
                onChange={e => set('temperature', parseFloat(e.target.value) || 0)}
                className="w-full px-2 py-1.5 border border-ink-200 rounded focus:outline-none focus:border-ink-500" />
            </div>
          </div>

          {/* Base URL */}
          <div>
            <p className="text-ink-500 mb-1">Base URL</p>
            <input value={form.base_url} onChange={e => set('base_url', e.target.value)}
              placeholder="https://api.deepseek.com/v1"
              className="w-full px-2 py-1.5 border border-ink-200 rounded focus:outline-none focus:border-ink-500" />
          </div>

          {/* Model */}
          <div>
            <p className="text-ink-500 mb-1">Model *</p>
            <ModelSelect value={form.model} onChange={v => set('model', v)}
              baseUrl={form.base_url} apiKey={form.api_key} agent={form.agent} />
          </div>

          {/* API Key */}
          <div>
            <p className="text-ink-500 mb-1">API Key
              {editId !== null && <span className="ml-1 text-[10px] text-ink-400">（留空则不修改）</span>}
            </p>
            <input type="password" value={form.api_key} onChange={e => set('api_key', e.target.value)}
              placeholder="sk-..."
              className="w-full px-2 py-1.5 border border-ink-200 rounded focus:outline-none focus:border-ink-500" />
          </div>

          {/* Max Tokens & Price */}
          <div className="flex gap-3">
            <div className="w-32">
              <p className="text-ink-500 mb-1">Max Tokens <span className="text-[10px]">（0=不限）</span></p>
              <input type="number" min={0} step={256} value={form.max_tokens}
                onChange={e => set('max_tokens', parseInt(e.target.value) || 0)}
                className="w-full px-2 py-1.5 border border-ink-200 rounded focus:outline-none focus:border-ink-500" />
            </div>
            <div className="flex-1">
              <p className="text-ink-500 mb-1">$/1K prompt</p>
              <input type="number" min={0} step={0.0001} value={form.price_prompt_per_1k}
                onChange={e => set('price_prompt_per_1k', parseFloat(e.target.value) || 0)}
                className="w-full px-2 py-1.5 border border-ink-200 rounded focus:outline-none focus:border-ink-500" />
            </div>
            <div className="flex-1">
              <p className="text-ink-500 mb-1">$/1K completion</p>
              <input type="number" min={0} step={0.0001} value={form.price_completion_per_1k}
                onChange={e => set('price_completion_per_1k', parseFloat(e.target.value) || 0)}
                className="w-full px-2 py-1.5 border border-ink-200 rounded focus:outline-none focus:border-ink-500" />
            </div>
          </div>

          {/* Description & Sort & Active */}
          <div className="flex gap-3">
            <div className="flex-1">
              <p className="text-ink-500 mb-1">描述说明</p>
              <input value={form.description} onChange={e => set('description', e.target.value)}
                placeholder="适合建模/推理场景，128K 上下文..."
                className="w-full px-2 py-1.5 border border-ink-200 rounded focus:outline-none focus:border-ink-500" />
            </div>
            <div className="w-20">
              <p className="text-ink-500 mb-1">排序</p>
              <input type="number" min={0} value={form.sort_order}
                onChange={e => set('sort_order', parseInt(e.target.value) || 0)}
                className="w-full px-2 py-1.5 border border-ink-200 rounded focus:outline-none focus:border-ink-500" />
            </div>
            <div className="flex flex-col items-center justify-end pb-0.5">
              <p className="text-ink-500 mb-1">启用</p>
              <input type="checkbox" checked={form.is_active}
                onChange={e => set('is_active', e.target.checked)}
                className="w-4 h-4 accent-ink-800 cursor-pointer" />
            </div>
            <div className="flex flex-col items-center justify-end pb-0.5">
              <p className="text-ink-500 mb-1 text-center">仅Pro+</p>
              <input type="checkbox" checked={form.pro_only}
                onChange={e => set('pro_only', e.target.checked)}
                className="w-4 h-4 accent-purple-600 cursor-pointer" />
            </div>
          </div>

          {msg && (
            <p className={`text-[11px] ${msg.ok ? 'text-emerald-700' : 'text-red-600'}`}>{msg.text}</p>
          )}

          <div className="flex gap-2 pt-1">
            <button onClick={savePreset} disabled={saving}
              className="flex-1 py-2 bg-ink-800 text-white rounded hover:bg-ink-700 disabled:opacity-40 font-medium text-xs">
              {saving ? '保存中…' : (editId === null ? '创建预设' : '保存修改')}
            </button>
            <button onClick={() => setShowForm(false)}
              className="px-4 py-2 border border-ink-200 rounded text-xs hover:bg-ink-50">
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
