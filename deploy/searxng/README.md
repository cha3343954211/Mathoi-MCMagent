# SearXNG 部署指南（MathoiAgent 配套）

## 快速部署

```bash
# 1. 进入部署目录
cd /opt/mathoi   # 或你的项目目录
git clone https://github.com/cha3343954211/Mathoi-MCMagent .
cd deploy/searxng

# 2. 修改 secret key（重要！）
sed -i 's/change-me-to-a-random-string/'"$(openssl rand -hex 32)"'/' docker-compose.yml

# 3. 启动
docker compose up -d

# 4. 验证 JSON 接口可用
curl "http://127.0.0.1:8080/search?q=python&format=json" | python3 -m json.tool | head -20
```

## 在 MathoiAgent 管理页配置

1. 登录管理员账户，进入「后台管理」→「联网搜索」
2. 选择 **SearXNG（自建，推荐）**
3. 填写地址：`http://127.0.0.1:8080`
4. 点击「测试连接」，确认返回 `✓ 连通`
5. 点击「保存配置」

配置持久化到数据库，重启服务后仍生效。

## 关键说明

### 为什么需要自定义 settings.yml？

SearXNG 默认 **不启用 JSON 格式**，只支持 HTML 输出。  
MathoiAgent 通过 JSON API 调用 SearXNG，必须在 `settings.yml` 中明确启用：

```yaml
search:
  formats:
    - html
    - json   # ← 必须有这行
```

本目录的 `settings.yml` 已预先配置好，直接使用即可。

### 搜索引擎组合

预配置启用了以下引擎，适合技术文档搜索：

| 引擎 | 用途 |
|---|---|
| DuckDuckGo | 通用搜索 |
| Bing | 技术文档 |
| Google | 通用搜索 |
| StackOverflow | 代码问题 |
| GitHub | 代码仓库 |
| PyPI | Python 包 |

### 资源占用（8C8G 服务器参考）

| 项目 | 占用 |
|---|---|
| 内存 | 150~300 MB |
| CPU | 空闲近 0，搜索时短暂 1~2 核 |
| 磁盘 | < 500 MB |

### 安全注意事项

- 端口绑定 `127.0.0.1:8080`，**不暴露公网**
- 如需远程访问，建议通过 Nginx 反向代理并加认证
- `SEARXNG_SECRET_KEY` 请务必修改为随机字符串

## 更新

```bash
cd deploy/searxng
docker compose pull
docker compose up -d
```
