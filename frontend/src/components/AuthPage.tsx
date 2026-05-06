import { useCallback, useState } from 'react'
import { useStore } from '../store'

// ---------- 验证码生成 ----------
function genCaptcha(): { question: string; answer: number } {
  const a = Math.floor(Math.random() * 20) + 1
  const b = Math.floor(Math.random() * 20) + 1
  const ops = ['+', '-', '×'] as const
  const op = ops[Math.floor(Math.random() * ops.length)]
  if (op === '+') return { question: `${a} + ${b}`, answer: a + b }
  if (op === '-') {
    const [big, small] = a >= b ? [a, b] : [b, a]
    return { question: `${big} - ${small}`, answer: big - small }
  }
  const x = Math.floor(Math.random() * 9) + 1
  const y = Math.floor(Math.random() * 9) + 1
  return { question: `${x} × ${y}`, answer: x * y }
}

export function AuthPage() {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

  // 验证码状态（仅登录模式）
  const [captcha, setCaptcha] = useState(genCaptcha)
  const [captchaInput, setCaptchaInput] = useState('')
  const refreshCaptcha = useCallback(() => {
    setCaptcha(genCaptcha())
    setCaptchaInput('')
  }, [])

  const { login, register } = useStore()

  const captchaOk = mode === 'register' || String(captcha.answer) === captchaInput.trim()

  const submit = async (e: any) => {
    e.preventDefault()
    if (!captchaOk) { setErr('验证码错误'); return }
    setErr(''); setLoading(true)
    try {
      if (mode === 'login') await login(username, password)
      else await register(username, email, password)
    } catch (e: any) {
      setErr(e?.message || '失败')
      refreshCaptcha()
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-ink-50 to-ink-100">
      <div className="w-[400px] bg-white rounded-xl shadow-xl border border-ink-200 p-8">
        <div className="text-center mb-6">
          <div className="text-4xl mb-2">📐</div>
          <h1 className="text-xl font-semibold tracking-tight">MathoiAgent</h1>
          <p className="text-xs text-ink-400 mt-1">数学建模 AI 全自动工作台</p>
        </div>

        <div className="flex bg-ink-100 rounded p-0.5 text-sm mb-5">
          {(['login', 'register'] as const).map(k => (
            <button
              key={k}
              type="button"
              onClick={() => { setMode(k); setErr(''); refreshCaptcha() }}
              className={`flex-1 py-1.5 rounded ${mode === k ? 'bg-white shadow-sm font-medium' : 'text-ink-500'}`}>
              {k === 'login' ? '登录' : '注册'}
            </button>
          ))}
        </div>

        <form onSubmit={submit} className="space-y-3">
          <Field label="用户名" value={username} onChange={setUsername} placeholder="3-32 位字母/数字/下划线" />
          {mode === 'register' && (
            <Field label="邮箱" type="email" value={email} onChange={setEmail} placeholder="you@example.com" />
          )}
          <Field label="密码" type="password" value={password} onChange={setPassword} placeholder="至少 6 位" />

          {mode === 'login' && (
            <div>
              <span className="text-xs text-ink-500">验证码</span>
              <div className="flex items-center gap-2 mt-1">
                <div className="flex items-center justify-center bg-ink-100 rounded px-4 py-2 font-mono text-sm font-semibold tracking-widest select-none min-w-[100px]">
                  {captcha.question} = ?
                </div>
                <input
                  type="text"
                  inputMode="numeric"
                  value={captchaInput}
                  onChange={e => setCaptchaInput(e.target.value)}
                  placeholder="答案"
                  className={`flex-1 px-3 py-2 border rounded text-sm focus:outline-none ${
                    captchaInput && !captchaOk
                      ? 'border-red-300 focus:border-red-400'
                      : 'border-ink-200 focus:border-ink-500'
                  }`}
                />
                <button
                  type="button"
                  onClick={refreshCaptcha}
                  title="换一题"
                  className="px-2 py-2 border border-ink-200 rounded text-ink-400 hover:text-ink-700 hover:bg-ink-50 text-sm">
                  ↻
                </button>
              </div>
              {captchaInput && !captchaOk && (
                <p className="text-[11px] text-red-500 mt-1 ml-0.5">答案不正确</p>
              )}
            </div>
          )}

          {err && <p className="text-xs text-red-600 bg-red-50 rounded p-2">{err}</p>}

          <button
            type="submit"
            disabled={loading || !username || !password || (mode === 'register' && !email) || (mode === 'login' && !captchaOk)}
            className="w-full py-2 bg-ink-800 text-white rounded text-sm hover:bg-ink-700 disabled:opacity-40">
            {loading ? '处理中…' : (mode === 'login' ? '登录' : '注册并登录')}
          </button>
        </form>
      </div>
    </div>
  )
}

function Field({ label, value, onChange, placeholder, type }: any) {
  return (
    <label className="block">
      <span className="text-xs text-ink-500">{label}</span>
      <input
        type={type || 'text'}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="mt-1 w-full px-3 py-2 border border-ink-200 rounded text-sm focus:outline-none focus:border-ink-500"
      />
    </label>
  )
}
