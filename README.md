# Android Agent

Android Agent 由 Python/FastAPI 服务端和 Android 客户端组成。App 可向服务端注册用户、创建隔离的 Android 项目，并通过 Agent 修改和构建项目。

## 用户注册与目录隔离

App 首次使用时填写服务器地址并点击“注册新用户”。服务端会生成唯一的 `user_id` 和随机访问 Token：

- 账号数据库：`data/users.db`（只保存 Token 的 SHA-256 哈希）
- 用户项目：`workspaces/{user_id}/{project_id}`
- 用户构建：`builds/{user_id}/{project_id}`

之后所有 API 请求都通过 `Authorization: Bearer <token>` 确定用户身份。客户端不能通过修改 `user_id` 访问其他用户目录。

> Token 只在注册响应中返回一次。请勿清除 App 数据；丢失 Token 后无法恢复原账号。

## 启动服务

```bash
python3 -m pip install -r requirements.txt
cp config.yaml.example config.yaml
python3 -m agent serve
```

## 迁移到云服务器

代码不依赖本机账号系统。迁移时复制项目代码，并持久化以下三个目录即可：

```text
data/
workspaces/
builds/
```

可通过 `AGENT_DATA_DIR` 把账号数据库放到独立持久化磁盘。生产环境应使用 HTTPS，并在反向代理层为 `/api/register` 添加限流。若以后需要多实例部署，可保持 API 不变，将 `UserStore` 的 SQLite 实现替换为云数据库。
