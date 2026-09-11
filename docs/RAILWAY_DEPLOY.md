# Railway 部署指南

这套方案部署一个长期运行的 Android Agent 服务，使用 Railway 提供的 HTTPS 域名和一个持久 Volume。首发保持单副本 SQLite；数据库、项目文件、APK 和 Gradle 缓存全部写入 `/data`。

> Railway 新项目不要再添加 `railway.json` 或 `railway.toml`。Railway 已将 Config as Code 标记为弃用，新服务应使用控制台或 `.railway/railway.ts` Infrastructure as Code。本项目首次部署使用控制台，避免把密钥或错误的项目资源声明提交进仓库。

## 1. 将代码推送到 GitHub

Railway 从 GitHub 构建时只能看到已经推送的提交。确认以下文件位于准备部署的分支：

- `Dockerfile`
- `.dockerignore`
- `deploy/config.railway.yaml`
- `requirements.lock`
- `agent/`、`template/`

不要提交本机的 `config.yaml`、`.env`、模型 Key、SMTP 密码、签名文件、`data/`、`workspaces/` 或 `builds/`。

## 2. 创建 Railway Service

1. 登录 [Railway Dashboard](https://railway.com/dashboard)。
2. 选择 **New Project → Deploy from GitHub repo**。
3. 选择当前仓库和部署分支。
4. Root Directory 保持仓库根目录 `/`。
5. Railway 会自动检测根目录的 `Dockerfile`。构建日志应出现使用 Dockerfile 的提示。

首次镜像构建会下载 Android command-line tools、API 36 和 Build Tools 36.1.0，因此明显慢于普通 Python API；后续会利用构建缓存。

## 3. 必须添加持久 Volume

在项目 Canvas 中右键服务，选择 **Attach Volume**：

- Mount Path：`/data`
- 初始容量：建议至少 10 GB
- 服务副本：保持 `1`

不能跳过这一步。Railway 的普通容器文件系统会随重新部署被替换；没有 Volume 会丢失账号数据库、项目和 APK。Dockerfile 已把以下目录放到该卷中：

```text
/data/data          SQLite 数据库、规则、记忆和索引
/data/workspaces    用户 Android 项目
/data/builds        构建日志和 APK
/data/gradle-cache  Gradle 下载缓存
```

SQLite + Volume 模式不要开启多副本或跨区域部署。需要水平扩容时再迁移到 Railway PostgreSQL、Redis 和对象存储。

应用进程以 UID/GID `10001` 运行。容器入口会先修正已挂载 `/data` Volume 的所有权，再立即通过 `gosu` 降权启动应用，兼容早期由 root 创建的卷和数据库文件。执行工具还要求宿主允许 bubblewrap 使用非特权用户命名空间；若平台不支持，可使用账号与只读文件 API，但进程与 Git 操作会明确失败。具体检查见 [安全执行配置](SECURITY_EXECUTION.md)。

当前 Free/Trial 部署使用 500 MB Volume，`minimum_free_disk_bytes` 因此设置为
`268435456`（256 MiB），仅适合账号、登录和轻量接口测试。实际运行 Android
构建前应升级到至少 5 GB 的 Volume，并把该阈值恢复为 `536870912`（512 MiB）。

## 4. 配置 Variables

打开 Service → **Variables → Raw Editor**，参考 `deploy/railway.env.example` 添加：

```ini
AGENT_PROVIDER=deepseek
AGENT_API_KEY=你的模型供应商Key
AGENT_REGISTRATION_ENABLED=true
AGENT_GUEST_SESSIONS_ENABLED=true
AGENT_EMAIL_VERIFICATION_REQUIRED=false
AGENT_ADMIN_UI_ENABLED=false
```

注意：

- 不需要设置 `PORT`，Railway 会自动注入。
- 游客会话由 `AGENT_GUEST_SESSIONS_ENABLED` 独立控制，无需开放普通账号注册。
- 不要把本机 `config.yaml` 中的真实 Key 提交或复制到 Docker 镜像。
- 选择 Anthropic 时将 `AGENT_PROVIDER=anthropic`，并把相应 Key 放在 `AGENT_API_KEY`。
- Variables 的修改需要在 Railway 中确认并 Deploy 才会生效。

### 首个账号的安全创建方式

如果暂时没有 SMTP：

1. 首次设 `AGENT_REGISTRATION_ENABLED=true`、`AGENT_EMAIL_VERIFICATION_REQUIRED=false`。
2. 部署成功后，在 Android 注册页创建自己的邮箱密码账号。
3. 立刻把 `AGENT_REGISTRATION_ENABLED` 改为 `false` 并重新部署。
4. 已有账号仍可正常使用邮箱密码登录，只是不再允许陌生人注册。

面向多用户开放注册时，不要使用上述临时模式。应设置：

```ini
AGENT_REGISTRATION_ENABLED=true
AGENT_EMAIL_VERIFICATION_REQUIRED=true
AGENT_SMTP_HOST=smtp.example.com
AGENT_SMTP_PORT=587
AGENT_SMTP_USERNAME=android-agent@example.com
AGENT_SMTP_PASSWORD=你的SMTP密码
AGENT_SMTP_FROM=Android Agent <android-agent@example.com>
AGENT_SMTP_STARTTLS=true
```

## 5. 配置健康检查和公网域名

在 Service → Settings 中设置：

- Healthcheck Path：`/healthz`
- Healthcheck Timeout：建议 `300` 秒（首次启动和冷缓存可能较慢）
- Restart Policy：`ON_FAILURE`
- Serverless / Sleep：关闭。任务 Worker 和 WebSocket 需要长期运行。
- Replicas：`1`

然后进入 **Networking → Generate Domain**，Railway 会提供类似：

```text
https://android-agent-production.up.railway.app
```

Railway 会处理 HTTPS，无需在 Railway 容器内运行 Caddy 或开放额外端口。

当前 production 服务已部署到：

```text
https://android-agent-production-c627.up.railway.app
```

Android 与桌面端的发布默认地址已经指向该域名；仍可用
`ANDROID_AGENT_SERVER_URL` 在本地开发或打包时覆盖。

验证：

```bash
curl https://你的Railway域名/healthz
curl -I https://你的Railway域名/docs
```

预期 `/healthz` 返回：

```json
{"status":"ok"}
```

`/api/health` 仍然要求登录 Token，这是正常的；公开探活只暴露服务是否存活，不返回模型、用户或服务器信息。

## 6. 创建账号后测试 API

在 Android 注册首个账号后，用邮箱密码测试登录：

```bash
curl -X POST https://你的Railway域名/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{
    "email":"you@example.com",
    "password":"你的密码",
    "device":{
      "device_id":"railway-check",
      "device_name":"Railway Check",
      "device_type":"cli",
      "platform":"Linux",
      "app_version":"1.0"
    }
  }'
```

不要把返回的 Token 粘贴到聊天、提交到仓库或写入截图。Android 和桌面客户端会在邮箱密码登录后自行安全保存设备 Token。

## 7. 让 Android 和桌面端连接 Railway

### Android

用 Railway HTTPS 域名重新构建：

```bash
cd android-app
ANDROID_AGENT_SERVER_URL=https://你的Railway域名 ./gradlew clean assembleDebug
```

发布包同样设置 `ANDROID_AGENT_SERVER_URL`，并补齐现有 Release 签名环境变量后运行 `assembleRelease verifyReleaseSigning`。

### 桌面端

发布前修改 `desktop/app-config.json`：

```json
{
  "serviceUrl": "https://你的Railway域名"
}
```

重新运行桌面打包流程。普通用户只会看到邮箱和密码，不需要输入 Railway 域名或 Token。

## 8. 日志、备份和更新

- 部署日志：Service → Deployments → View Logs。
- 运行日志：Service → Logs。
- Volume 文件：可用 Railway CLI 的 `railway volume browse /` 或 `railway volume files` 管理。
- 建议开启 Railway Volume Backups，并定期把 `/data/data`、`/data/workspaces`、`/data/builds` 下载到平台外备份。
- GitHub 分支有新提交时 Railway 可自动重新构建；正在运行的 Agent 任务可能在部署切换时中断，因此更新前先确认没有重要任务执行。

## 9. 常见故障

### Application failed to respond

确认部署日志中的启动命令最终包含：

```text
python -m agent serve --host 0.0.0.0 --port <Railway PORT>
```

不要把服务限制到 `127.0.0.1`，也不要手工固定 Railway 端口。

### 重部署后账号或项目消失

Volume 没有挂载到 `/data`，或环境目录被覆盖。检查 Volume Mount Path，并确认日志中的容器使用当前 Dockerfile。

### 注册返回 404

`AGENT_REGISTRATION_ENABLED` 为 `false`。首次注册时临时打开，注册完成后建议关闭。

### 注册返回 503

启用了邮箱验证但 SMTP 未配置完整。检查 `AGENT_SMTP_HOST`、`AGENT_SMTP_FROM` 和 Railway 日志。

### 项目能创建但 APK 构建失败

确认 Railway 使用根目录 Dockerfile；镜像构建阶段应该成功安装 `platforms;android-36` 与 `build-tools;36.1.0`。同时确认 Volume 空间充足。

### 容器内存不足或费用偏高

Android Gradle 构建比普通 FastAPI 服务更耗内存。先把同时活动任务数保持为 3 或更低；仍不足时提高 Railway Service 内存，或以后把构建 Worker 拆成独立服务。
