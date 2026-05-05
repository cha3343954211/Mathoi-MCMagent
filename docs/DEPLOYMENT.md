# 部署指南

## 一、本地开发（SQLite，零配置）

```powershell
# 后端
.\start-backend.ps1
# 前端
.\start-frontend.ps1
```

打开 <http://localhost:5173>，用默认账号 `admin / admin123` 登录，立即修改密码。

## 二、Docker Compose（含 PostgreSQL）

```powershell
# 编辑 backend/.env，至少填入：
#   DEFAULT_API_KEY、JWT_SECRET（务必改为长随机串）
#   DEFAULT_ADMIN_PASSWORD（生产环境必改）
docker compose up -d
```

服务清单：
- `postgres:5432`：用户/任务持久化
- `redis:6379`：可选缓存
- `backend:8000`：FastAPI
- `frontend:5173`：React 静态站

## 三、上云部署（推荐方案）

### 方案 A：单机 VPS + Docker Compose

适合 1-50 用户，最低 2C4G。

1. 准备：阿里云/腾讯云/Hetzner 等任意 VPS，装 Docker
2. 克隆仓库 → 编辑 `backend/.env`
3. `docker compose up -d`
4. 反向代理（Caddy 推荐）：

```
your.domain.com {
    reverse_proxy /api/* backend:8000
    reverse_proxy /ws/*  backend:8000
    reverse_proxy frontend:80
}
```

### 方案 B：分布式（Kubernetes + 托管 Postgres）

适合多租户、高并发。

| 组件 | 推荐 |
|---|---|
| 数据库 | 阿里云 RDS / AWS RDS / Supabase Postgres |
| Redis | 阿里云 Redis / Upstash |
| 工作区 | NFS / 对象存储挂载（保持 `workspace_dir` 跨 Pod 共享） |
| 后端 | Deployment + HPA，多副本（注意 task_manager 内存态需重构为 Redis） |
| 前端 | 静态站托管（Vercel / Cloudflare Pages） |

> ⚠️ 当前 `task_manager` 是进程内内存态，多副本时需要：
> - 把 `_pause_events` / `_hitl_events` 迁移到 Redis Pub/Sub
> - WebSocket 用粘性会话或迁移到 Redis Streams
> - 工作区持久卷必须跨 Pod 共享

## 四、生产环境检查表

### 安全
- [ ] `JWT_SECRET` 改为 ≥ 32 位随机串：`python -c "import secrets;print(secrets.token_urlsafe(48))"`
- [ ] 默认 admin 密码立即修改
- [ ] `ALLOW_REGISTER=false`（仅管理员创建账号）
- [ ] `CORS_ORIGINS` 限制为实际域名
- [ ] HTTPS 强制（Caddy/Nginx + Let's Encrypt）
- [ ] LLM API Key 走 Secret 管理，不要写死 `.env`

### 数据
- [ ] PostgreSQL 开启自动备份
- [ ] `workspace/` 定期归档（旧任务可清理）
- [ ] 配额：每用户最大任务数、每任务最大文件大小（待开发）

### 监控
- [ ] 后端日志接入 ELK / Loki
- [ ] LLM 调用统计（token / 费用）
- [ ] 沙箱执行超时与失败告警

## 五、扩展计费与多租户

后续可在 `User` 表加：
```python
quota_max_tasks: int = 10
quota_used_tokens: int = 0
plan: str = "free" | "pro" | "team"
billing_account_id: str
```

中间件层在 `create_task` 前校验配额，在 LLM 调用后累加 token。

## 六、数据迁移：SQLite → PostgreSQL

```powershell
# 1. 启动 postgres，创建空库 mathoi
# 2. 用 pgloader 一键迁移
docker run --rm -v ${PWD}/backend:/data dimitri/pgloader \
    pgloader sqlite:///data/mathoi.db postgresql://mathoi:mathoi@host:5432/mathoi
# 3. 改 .env 的 DATABASE_URL，重启后端
```

或最简单：从空库重新建账号，不迁移历史。
