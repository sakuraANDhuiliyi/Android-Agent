# 云服务器部署指南

本文以 Ubuntu 24.04、域名 `agent.example.com`、Caddy 自动签发 HTTPS 证书为例。首发推荐单机 SQLite；账号、设备会话、项目、构建产物都持久化在这台服务器上。用户在 Android 和桌面端只输入邮箱与密码，服务地址由发布包预先配置。

如果先使用 Railway 托管，直接阅读 [Railway 部署指南](RAILWAY_DEPLOY.md)；Railway 已提供容器、HTTPS 域名和 Volume，不需要执行本文的 Ubuntu、systemd 或 Caddy 步骤。

## 1. 上线前准备

- 一台至少 4 核、8 GB 内存、80 GB SSD 的 Linux 云主机；如果要在云端编译 Android，建议 8 核、16 GB 内存。
- 一个域名，将 `agent.example.com` 的 A/AAAA 记录指向服务器公网 IP。
- 云防火墙只放行 `22`、`80`、`443`；不要公开 `8000`。
- Python 3.10+、Git、JDK 17。云端需要生成 APK 时，还要安装 Android SDK，并为服务用户配置 `ANDROID_SDK_ROOT`。
- DeepSeek 或 Anthropic API Key；生产自助注册还需要 SMTP 邮箱服务。

## 2. 安装服务

在服务器执行（把仓库地址换成自己的）：

```bash
sudo apt update
sudo apt install -y git python3 python3-venv openjdk-17-jdk caddy rsync
sudo useradd --system --create-home --home-dir /var/lib/android-agent --shell /usr/sbin/nologin android-agent
sudo git clone YOUR_REPOSITORY_URL /opt/android-agent
sudo chown -R android-agent:android-agent /opt/android-agent /var/lib/android-agent
sudo -u android-agent python3 -m venv /opt/android-agent/.venv
sudo -u android-agent /opt/android-agent/.venv/bin/pip install --upgrade pip
sudo -u android-agent /opt/android-agent/.venv/bin/pip install -r /opt/android-agent/requirements.txt
sudo -u android-agent mkdir -p /opt/android-agent/workspaces /opt/android-agent/builds /var/lib/android-agent/data
```

如果云端要执行 Android 构建，再安装 Android SDK command-line tools，并在环境文件加入：

```ini
ANDROID_SDK_ROOT=/opt/android-sdk
ANDROID_HOME=/opt/android-sdk
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
```

用 `sdkmanager` 安装项目所需版本（当前 Android 客户端使用 API 36 / Build Tools 36.1.0）：

```bash
sudo -u android-agent /opt/android-sdk/cmdline-tools/latest/bin/sdkmanager \
  "platform-tools" "platforms;android-36" "build-tools;36.1.0"
sudo -u android-agent /opt/android-sdk/cmdline-tools/latest/bin/sdkmanager --licenses
```

## 3. 配置账号、邮件和模型

复制生产配置与环境变量模板：

```bash
sudo cp /opt/android-agent/deploy/config.production.yaml /opt/android-agent/config.yaml
sudo mkdir -p /etc/android-agent
sudo cp /opt/android-agent/deploy/android-agent.env.example /etc/android-agent/android-agent.env
sudo chown root:android-agent /etc/android-agent/android-agent.env
sudo chmod 600 /etc/android-agent/android-agent.env
sudo editor /opt/android-agent/config.yaml
sudo editor /etc/android-agent/android-agent.env
```

至少替换以下值：

- `AGENT_API_KEY`：模型供应商 Key。
- `AGENT_SMTP_*`：发件服务器、账号、密码和发件人。
- `AGENT_ADMIN_TOKEN`：用 `openssl rand -hex 32` 生成，不复用任何其他密钥；确认替换后才把 `AGENT_ADMIN_UI_ENABLED` 改为 `true`。
- `config.yaml` 内的 SMTP 主机、账号、发件人要与环境文件一致，或直接以环境变量为准。

推荐生产设置是 `AGENT_REGISTRATION_ENABLED=true` 和 `AGENT_EMAIL_VERIFICATION_REQUIRED=true`。此时新用户在客户端用邮箱与密码注册，先完成邮件验证码验证，再登录。若只允许管理员邀请用户，可关闭网络注册；已有账号仍可用邮箱密码登录。

旧版 `registration_token` 和用户 Token 配对接口仍为兼容层，但新版 Android/桌面登录界面不再使用它们。

## 4. 配置 systemd 与 HTTPS

安装并启动服务：

```bash
sudo cp /opt/android-agent/deploy/android-agent.service /etc/systemd/system/android-agent.service
sudo systemctl daemon-reload
sudo systemctl enable --now android-agent
sudo systemctl status android-agent --no-pager
sudo journalctl -u android-agent -n 100 --no-pager
```

先在服务器本机确认服务正常。`/docs` 可验证 HTTP 进程；`/api/health` 需要登录后的 Bearer Token：

```bash
curl -I http://127.0.0.1:8000/docs
```

复制 Caddy 配置，将域名替换为自己的域名：

```bash
sudo cp /opt/android-agent/deploy/Caddyfile.example /etc/caddy/Caddyfile
sudo editor /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl -I https://agent.example.com/docs
```

Caddy 会自动申请和续期 TLS 证书，也会自动代理 WebSocket。确认公网 `https://agent.example.com/docs` 可访问后，在云防火墙再次检查 `8000` 未开放。

管理后台位于 `https://agent.example.com/admin/`。它使用独立管理 Token；强烈建议再用 VPN、Cloudflare Access 或 Caddy 的 IP 白名单限制 `/admin/*` 与 `/api/admin/*`，不要只依赖一个公网密码。

## 5. 将服务地址写入 Android 发布包

Android 登录页不再允许用户修改服务器。构建时通过环境变量或 Gradle 属性写入地址：

```bash
cd android-app
ANDROID_AGENT_SERVER_URL=https://agent.example.com ./gradlew clean assembleRelease
```

正式签名还需设置：

```bash
export ANDROID_AGENT_KEYSTORE=/secure/path/release.jks
export ANDROID_AGENT_KEYSTORE_PASSWORD='replace-me'
export ANDROID_AGENT_KEY_ALIAS='release'
export ANDROID_AGENT_KEY_PASSWORD='replace-me'
ANDROID_AGENT_SERVER_URL=https://agent.example.com ./gradlew clean assembleRelease verifyReleaseSigning
```

也可在私有 `~/.gradle/gradle.properties` 中设置 `agentServerUrl=https://agent.example.com`。不要把签名密码提交到仓库。远程地址必须使用 HTTPS；Debug 构建仅为本机/模拟器调试保留明文 HTTP 能力。

## 6. 将服务地址写入桌面发布包

发布前编辑 `desktop/app-config.json`：

```json
{
  "serviceUrl": "https://agent.example.com"
}
```

然后按现有签名流程打包：

```bash
cd desktop
npm ci
npm run check
npm run test:unit
npm run dist:mac
```

开发或端到端测试可临时用 `ANDROID_AGENT_SERVER_URL` 覆盖 JSON；远程服务必须是 HTTPS，只有 `localhost`、`127.0.0.1`、`::1` 可用 HTTP。用户看不到服务地址和 Token，登录成功后的设备 Token 由 Electron `safeStorage` 保存。

## 7. 验证邮箱密码完整链路

上线后按顺序验证：

1. Android 注册一个新邮箱并收到验证码。
2. 验证邮箱，用邮箱和密码登录 Android。
3. 在桌面端用同一邮箱和密码登录，确认看到同一账号的项目。
4. 在账号设备页确认出现 Android 与 Desktop 两个设备会话。
5. 创建项目、发起任务、查看事件流并下载 APK。
6. 重启 `android-agent`，确认账号、项目和任务仍在。
7. 撤销一个设备会话，确认该设备需要重新登录。

快速检查日志：

```bash
sudo journalctl -u android-agent -f
sudo journalctl -u caddy -f
```

## 8. 更新、备份与恢复

每次更新前先备份，并让服务短暂停写：

```bash
sudo systemctl stop android-agent
sudo -u android-agent /opt/android-agent/.venv/bin/python \
  /opt/android-agent/scripts/migrate_db.py --backup --data-dir /var/lib/android-agent/data
sudo rsync -a /var/lib/android-agent/data/ /backup/android-agent/data/
sudo rsync -a /opt/android-agent/workspaces/ /backup/android-agent/workspaces/
sudo rsync -a /opt/android-agent/builds/ /backup/android-agent/builds/
sudo -u android-agent git -C /opt/android-agent pull --ff-only
sudo -u android-agent /opt/android-agent/.venv/bin/pip install -r /opt/android-agent/requirements.txt
sudo systemctl start android-agent
```

数据库、`workspaces/`、`builds/` 必须作为同一恢复点备份。建议每天增量备份、每周做一次异机快照，并实际演练恢复。

## 9. 何时切换 PostgreSQL / Redis / 对象存储

单机先保持：

```yaml
deployment_mode: sqlite
artifact_backend: local
```

需要多实例或更高可用时再切换：

| `deployment_mode` | 持久状态 | Ticket / 限流 | 必需配置 |
|---|---|---|---|
| `sqlite` | SQLite | SQLite | 无 |
| `postgres` | PostgreSQL | 共享 SQL | `AGENT_DATABASE_URL` |
| `hybrid` | PostgreSQL | Redis | `AGENT_DATABASE_URL`、`AGENT_REDIS_URL` |

对象存储模式还需 `AGENT_ARTIFACT_BACKEND=object` 与 `AGENT_OBJECT_STORE_URL=s3://...`。切换前使用：

```bash
python3 scripts/migrate_sqlite_to_postgres.py --data-dir /var/lib/android-agent/data
python3 scripts/migrate_sqlite_to_postgres.py --data-dir /var/lib/android-agent/data --apply --backup
```

核对迁移生成的 `pg-migration/manifest.json` 中表计数、`row_total` 和 `aggregate_hash` 后再切流量。配置缺失时服务会拒绝启动，而不是静默降级。

## 10. 常见故障

- 手机“连接失败”：先用手机浏览器打开 `https://你的域名/docs`；打不开通常是 DNS、防火墙或证书问题。
- 注册返回 503：SMTP 未配置完整，检查 `AGENT_SMTP_HOST`、`AGENT_SMTP_FROM` 和服务日志。
- 登录成功但任务失败：通常是 `AGENT_API_KEY` 无效、模型名不支持或服务器无法访问模型供应商。
- 云端构建失败：检查 JDK 17、`ANDROID_SDK_ROOT`、SDK licenses 和服务用户对 `workspaces/`、`builds/` 的写权限。
- 桌面端仍连旧地址：确认重新打包时 `desktop/app-config.json` 已修改；开发环境检查是否残留 `ANDROID_AGENT_SERVER_URL`。
- Android 仍连旧地址：使用目标地址重新构建 APK；服务地址变化后客户端会自动清理旧 Token 并要求重新登录。
