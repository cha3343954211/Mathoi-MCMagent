# MathoiAgent

> 数学建模 AI 全自动工作台 · 仿照 [MathModelAgent](https://github.com/jihe520/MathModelAgent) 重制并增强
>
> *More than code · 道驭术，术辅道*

## 一、定位

输入赛题 + 数据，自动产出可直接提交的数学建模论文。多 Agent 协同，全程透明可追踪、可介入、可断点续跑。

## 二、特性

### 基础能力（对齐原项目）

- **多 Agent 协同**：Modeler（建模）/ Coder（编程执行）/ Writer（写作）三角分工
- **本地 Jupyter Kernel 沙箱**：实时执行 Python 数据分析代码，回传结果与图表
- **完整论文产出**：Markdown 中间产物 → docx / pdf 模板化输出
- **任务全周期管理**：FastAPI + WebSocket 实时推送 + Redis 任务持久化

### 增强项（差异化）

1. **多模型协同**：每个 Agent 独立配置模型与供应商
   - 例：Modeler 用强推理模型（o1 / DeepSeek-R1），Coder 用 Claude 3.5 Sonnet，Writer 用 GPT-4o
2. **可视化追踪面板**：思考链 / 工具调用 / 代码执行流时间线，类 LangSmith 体验
3. **断点续跑 + HITL（人在环路）**：任意节点暂停、修改输入、回滚重跑、人工审查

### LLM 接入（双轨制）

- **默认轨**：OpenAI Chat Completions 兼容协议（DeepSeek / Qwen / Kimi / Ollama / vLLM ……）
- **扩展轨**：可启用 LiteLLM 后端，覆盖 Anthropic / Gemini / Bedrock 等 100+ 供应商

## 三、技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.11 · FastAPI · uvicorn · pydantic-settings |
| 任务 | Redis（持久化）+ asyncio 内存调度 |
| 沙箱 | jupyter_client 本地 Kernel |
| LLM | openai SDK / litellm（可选） |
| 前端 | Vite · React 18 · TypeScript · Tailwind CSS · Zustand |
| 文档 | python-docx · pandoc（可选） |

## 四、目录

```
MathoiAgent/
├── backend/             # FastAPI 后端
│   ├── app/
│   │   ├── api/         # 路由
│   │   ├── core/        # 配置 / 事件总线 / 日志
│   │   ├── llm/         # LLM 适配层（双轨）
│   │   ├── agents/      # 多 Agent 实现
│   │   ├── sandbox/     # Jupyter Kernel 沙箱
│   │   ├── tools/       # 工具集
│   │   ├── workflow/    # Orchestrator 编排
│   │   ├── tasks/       # 任务管理 + HITL
│   │   ├── exporters/   # 论文导出
│   │   └── main.py
│   ├── pyproject.toml
│   └── .env.example
├── frontend/            # React + Vite
│   ├── src/
│   │   ├── pages/
│   │   ├── components/
│   │   ├── store/
│   │   └── api/
│   └── package.json
├── docs/                # 设计文档
├── docker-compose.yml
└── README.md
```

## 五、快速开始

### 本地开发

```powershell
# 后端
cd backend
uv venv ; .\.venv\Scripts\Activate.ps1
uv pip install -e .
copy .env.example .env   # 编辑填入你的模型配置
python -m app.main

# 前端
cd ..\frontend
pnpm i
pnpm dev
```

打开 <http://localhost:5173> 即可使用。

### Docker

```powershell
docker compose up -d
```

## 六、模型配置示例

`backend/.env`：

```ini
# 全局默认（OpenAI 兼容）
LLM_BACKEND=openai            # openai | litellm
DEFAULT_BASE_URL=https://api.deepseek.com/v1
DEFAULT_API_KEY=sk-xxx
DEFAULT_MODEL=deepseek-chat

# 多模型协同：按 Agent 覆盖
MODELER_MODEL=deepseek-reasoner
CODER_MODEL=claude-3-5-sonnet-20241022
CODER_BASE_URL=https://api.anthropic.com
CODER_API_KEY=sk-ant-xxx
WRITER_MODEL=gpt-4o
```

## 七、License

MIT
