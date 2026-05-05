# 使用指南

## 一、环境准备

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | 3.11+ | 后端运行时 |
| Node.js | 20+ | 前端构建 |
| pnpm | 9+ | 前端包管理（推荐） |
| Redis | 7+ | 任务持久化（可选） |
| pandoc | 3.x | 论文导出 docx 高保真（可选） |

## 二、本地启动

### Windows 一键脚本

```powershell
# 后端
.\start-backend.ps1

# 前端（另开一个 PowerShell）
.\start-frontend.ps1
```

首次运行 `start-backend.ps1` 会自动复制 `.env.example` 到 `.env` 并停止；编辑 `backend\.env` 填入你的 LLM API Key 后重新执行。

### 手动启动

```powershell
# 后端
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m ipykernel install --user --name python3
copy .env.example .env  # 编辑填入 API Key
python -m app.main

# 前端
cd ..\frontend
pnpm install
pnpm dev
```

打开 <http://localhost:5173>。

## 三、第一次使用

1. 点击 **⚙ 模型配置**：
   - 后端选 `openai` 兼容协议
   - 在 `default` 段填入：base_url + api_key + model（任选一家：DeepSeek / OpenAI / Kimi / Qwen 等）
   - 可选：为 Modeler / Coder / Writer 单独配置不同模型
2. 点击 **+ 新建任务**：
   - 标题 + 完整赛题文本
   - 上传数据文件（csv / xlsx）
3. 等待 Modeler 输出方案 → **HITL 面板**审查（通过 / 改后通过 / 重做）
4. 观察 Coder 执行代码、生成图表
5. Writer 完成论文，进入 **产物** 标签页查看 `paper.md` 与 `paper.docx`

## 四、模型协同推荐

| Agent | 推荐模型 | 理由 |
|---|---|---|
| Modeler | `o1-preview` / `deepseek-reasoner` / `claude-3.7-sonnet-thinking` | 强推理 |
| Coder | `claude-3-5-sonnet` / `deepseek-chat` / `qwen2.5-coder-32b` | 代码能力 |
| Writer | `gpt-4o` / `claude-3.5-sonnet` | 中文写作流畅 |

## 五、Docker 部署

```powershell
docker compose up -d
```

服务：
- 前端：<http://localhost:5173>
- 后端：<http://localhost:8000>
- Redis：localhost:6379

## 六、常见问题

**Q：执行代码时报 ModuleNotFoundError**
A：进入 backend 虚拟环境后，按需 `pip install pandas seaborn statsmodels scikit-learn` 等。

**Q：中文图表乱码**
A：Coder 的 system prompt 已包含字体配置；如仍乱码，请安装系统中文字体（SimHei / Noto Sans CJK）。

**Q：如何接入 Anthropic / Gemini？**
A：在模型配置页将后端切到 `litellm`，model 填 `anthropic/claude-3-5-sonnet-20241022` / `gemini/gemini-1.5-pro`，base_url 留空，填入对应 API Key。

**Q：HITL 不想介入怎么办？**
A：直接在 HITL 面板点 **通过** 即可。后续可在 `orchestrator.py` 中条件性跳过。
