# 架构设计

## 整体拓扑

```
┌────────────────────────┐         ┌──────────────────────────────┐
│  React + Vite Frontend │ ◀─WS──▶ │  FastAPI Backend             │
│  · 任务面板             │  REST   │  · /api  REST 接口            │
│  · 追踪时间线           │ ◀────▶  │  · /ws/tasks/{id}  事件推送    │
│  · HITL 控制台          │         │  · TaskManager + EventBus    │
│  · 模型配置             │         │  · Workflow Orchestrator     │
└────────────────────────┘         └────┬───────────────┬─────────┘
                                        │               │
                                ┌───────▼───────┐  ┌────▼─────────┐
                                │ Multi-Agent   │  │ Jupyter      │
                                │ - Modeler     │  │ Kernel       │
                                │ - Coder       │  │ Sandbox      │
                                │ - Writer      │  │ (隔离 cwd)    │
                                └───┬───────┬───┘  └──────────────┘
                                    │       │
                          ┌─────────▼──┐  ┌─▼─────────────┐
                          │ LLM 双轨   │  │ Tools         │
                          │ - openai   │  │ - execute_py  │
                          │ - litellm  │  │ - read/write  │
                          └────────────┘  └───────────────┘
```

## 关键决策

### 1. 事件总线驱动一切

所有 Agent / Sandbox / Workflow 行为均发布为标准事件（`EventType` 枚举），前端通过 WebSocket 订阅同一总线。带来三个优势：

- **追踪天生可视化**：所有"思考链/工具调用/代码执行"都是可序列化事件
- **历史可回放**：刷新页面后通过 `/api/tasks/{id}/events` 拉取历史
- **HITL 自然嵌入**：暂停 / 介入 / 续跑都是事件

### 2. 多模型协同

`Settings.agent_config(agent)` 返回该 Agent 专属模型配置，缺省字段回退到全局 default。

支持的 Agent 维度：`modeler` / `coder` / `writer`，可通过 `.env` 或运行时 `POST /api/models` 修改。

### 3. 双轨 LLM 后端

| 后端 | 何时用 |
|---|---|
| `openai` | 默认。任意 OpenAI 兼容端点（DeepSeek/Qwen/Kimi/Ollama/vLLM） |
| `litellm` | 需要 Anthropic / Gemini / Bedrock 等非兼容供应商时 |

切换无需改代码，仅切 `LLM_BACKEND` 即可。

### 4. 本地 Jupyter Kernel 沙箱

每个任务独立启 Kernel，`cwd` 指向 `workspace/{task_id}/`，状态隔离。变量、import 在多次 `execute_python` 调用间保持，符合 ReAct 风格的小步快跑。

绘图统一 `matplotlib Agg`，display_data 经 base64 持久化为 `figure_{N}.png`。

### 5. HITL 实现机制

`TaskManager.request_hitl()` 创建 `asyncio.Event` 阻塞等待；前端通过 `POST /api/tasks/{id}/hitl` 写入响应并 `set()` 释放。当前在 Modeler 输出建模方案后触发，也可在任意阶段调用。

可扩展点：在每个工具调用前可加策略性 HITL（如执行高风险代码前需确认）。

### 6. 断点续跑

`TaskManager.checkpoint()` 在每个阶段完成时记录关键产出，`rollback_to(label)` 截断历史。结合 Redis 持久化（可选）可在重启后恢复。

## 数据流

```
用户提交赛题 + 数据
   │
   ▼
[Modeler] 输出建模方案 (modeling_plan.md)
   │
   ▼
[HITL 审查] approve / edit / redo
   │
   ▼
[Coder] tool-calling 循环
   ├─ execute_python (读数据/建模/绘图)
   ├─ write_file (analysis_report.md)
   └─ ...
   │
   ▼
[Writer] 整合 → paper.md
   │
   ▼
[Exporter] paper.md → paper.docx (pandoc 优先)
```

## 后续可扩展点

- **知识库 RAG**：内置历年赛题 + 经典模型库，向量检索注入 Modeler prompt
- **沙箱可切换**：抽象 SandboxProvider，加 Docker / E2B 实现
- **多模型路由**：按 token 预算 / 任务复杂度动态选择模型
- **协作模式**：多用户共享任务、评论、历史快照对比
