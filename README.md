# Android Agent

Android Agent 由 Python/FastAPI 服务端和 Android 客户端组成。App 可初始化设备连接、创建隔离的 Android 项目，并通过 Agent 修改和构建项目。第一阶段的完整范围见 `MVP_SPEC.md`。

## 第一阶段能力

- SQLite 持久化项目任务、事件、Token usage、改动摘要和构建产物。
- 同一项目串行执行，可请求停止，服务重启后中断任务会标记失败。
- Agent 必须执行 `assembleDebug`，成功任务保留任务级 APK 和构建日志。
- 手机端支持连接/项目、多 Conversation、任务流、审批、steer/follow_up/pause/resume/cancel、Diff/Checkpoint 恢复、构建日志与 APK 下载安装分享；WebSocket 优先并在断线后游标轮询。

## 多对话（Cursor 式）

每个 Android 项目下可开多个独立 **Conversation（对话）**，各自保留 Agent 上下文：

- `GET/POST /api/projects/{id}/conversations` — 列表 / 新建
- `GET/PATCH/DELETE /api/conversations/{id}` — 详情 / 改标题 / 归档
- `POST /api/conversations/{id}/ask` — 在该对话中提问（多轮连续）
- 同一项目同时只跑一个 turn（workspace 锁）；未调用 `assembleDebug` 的追问也可成功
- 旧版 `POST /api/projects/{id}/ask` 仍可用，内部自动挂到默认对话

## Conversation Event 模型

每个 Agent 轮次写入 `conversation_turns`，轮次内的消息、模型响应、工具调用和结果按严格递增的 `seq` 追加到 `conversation_events`。新会话上下文以这些不可变事件为权威来源，可重建 OpenAI-compatible 或 Anthropic 消息，并保留完整工具调用链。

主要规范事件包括：

- 消息与工具：`user_message`、`assistant_message`、`tool_call`、`tool_result`
- 生命周期：`turn_started`、`turn_completed`、`turn_failed`、`turn_canceled`、`turn_interrupted`
- 运行信息：`usage`、`provider_switch`、`model_switch`、`changes`
- 审批：`approval_required`、`approval_resolved`
- 上下文：`context_checkpoint`、可见的 `system_note` / `recovery_note`

旧数据库中的 `conversations.turns_json` 会在启动时幂等迁移为规范事件；字段仍保留，仅用于迁移和旧客户端的最终问答投影。`task_events` 继续承担 UI 日志、流式 delta 和 WebSocket 推送，`conversation_events` 则承担持久化、跨轮上下文和恢复，两者职责不同。

规范事件支持游标查询：

```http
GET /api/conversations/{conversation_id}/events?after_seq=0&limit=200&context_only=false
Authorization: Bearer <token>
```

`limit` 范围为 1-500，结果按 `seq` 升序返回，并提供 `next_after_seq` 与 `has_more`。接口严格校验 Conversation 所属用户并过滤凭证字段。

当较早历史超过 200 个新增事件或约 120,000 字符时，服务会追加结构化 `context_checkpoint`，提取用户意图、最终结果、工具成败和改动文件，同时保留最近 4 个 Turn 的完整事件。checkpoint 只改变模型上下文边界，不删除数据库事件；任务内 compact 仍作为单次请求超限时的最后保护。

服务重启时，未完成工具会先得到 `service_interrupted` 合成失败结果，再创建新的恢复 Task/Turn 自动继续模型推理，最多连续恢复 3 次。只读工具允许重新调用；如果模型再次请求中断前相同的 `write_file`、`str_replace` 或 `run_gradle`，必须重新经过 `recovery_tool_replay` 审批。下载工具仍沿用每次下载必审的规则。

规范事件写入、历史读取、Job/WebSocket 输出和事件查询 API 会识别并脱敏 Bearer Token、JWT、常见 API Key 前缀、URL 用户信息以及 `api_key=...` 等自由文本形式。结构化凭证字段继续拒绝写入。自由文本检测属于防泄漏保护而非密码保险库，无法保证识别所有私有密钥格式。

当前已支持跨 Conversation 的可控项目记忆（候选审批 + 本地 FTS 检索）。通用多实例消息队列仍未实现；自动恢复队列覆盖服务启动时发现的中断 Agent Task。

## 设备初始化与目录隔离

App 首次使用时填写服务器地址并点击“初始化设备连接”。服务端会生成唯一的 `user_id` 和随机访问 Token：

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

启动后：

- 可视化操作台：`http://127.0.0.1:8000/ui/`（打开即自动连接本机 `local` 用户，无需注册）
- API 文档：`http://127.0.0.1:8000/docs`

手机连接时使用电脑的局域网 IP，例如 `http://192.168.1.100:8000`。无 Token 时 API 同样默认使用 `local` 用户；需要隔离时再走注册或配置 Token。

## 网络搜索（Tavily）

在 `config.yaml` 配置后，Agent 可调用 `web_search`：

```yaml
tavily_api_key: "tvly-你的密钥"
```

也可使用环境变量 `TAVILY_API_KEY`。Key 申请：https://tavily.com

## 文件下载（需用户确认）

Agent 可调用 `download_file` 将 http/https 资源保存到工程内（推荐 `downloads/`）。
**每次下载都会暂停并弹出确认框，默认拒绝；只有你点「允许下载」后才会真正开始下载。**

## 桌面端（Electron + Monaco）

Cursor 式三栏桌面 IDE：左侧文件树 / 搜索 / 对话 / 任务，中间 Monaco + Diff Editor，右侧 Agent 对话 / Plan / 工具 / 审批，底部集成 xterm.js 终端、问题、输出和构建日志。支持对话管理、上下文 chip、断线重连、审批面板、checkpoint 恢复和响应式窄窗口。

需本机已安装 Node.js 18+。

```bash
cd desktop
npm install
npm start
```

> Electron 本体约 100MB+，默认从 GitHub 下载，国内常会长时间无进度。本目录已配置 `.npmrc` 使用 npmmirror 镜像；若仍慢，可手动执行：
> `export ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/`

- 默认尝试打开仓库下的 `workspaces/`（若存在）
- 支持打开文件夹 / 文件、多标签编辑、保存（⌘S / Ctrl+S）、未保存关闭确认
- 快捷键与菜单：新建、打开、另存为

## 验证与构建

```bash
# Python（含 Eval / 安全审计 / 故障注入 / 性能预算）
python3 -m pytest tests -q

# Desktop
cd desktop && npm run check && npm run test:unit && npm run test:screenshot

# Android
cd android-app && ./gradlew testDebugUnitTest assembleDebug

# 发布门禁（敏感信息扫描 + git diff --check + 上述客户端）
python3 scripts/release_check.py

# 离线 Eval 套件（16 场景，确定性 fake，无付费模型）
PYTHONPATH=. python3 -c "from evals import run_all_evals; print(sum(r.passed for r in run_all_evals()))"
```

手机端 Debug APK 位于 `android-app/app/build/outputs/apk/debug/app-debug.apk`。有模拟器时可另行执行 `./gradlew connectedDebugAndroidTest`（可选 smoke）。

架构说明见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 权限模式与 Workspace trust

- 运行模式：`ask` / `workspace` / `read_only`（见 `agent/permissions.py`）。
- 项目级 MCP 需用户明确 trust 后才会注册工具。
- Rules / Skills / Hooks / MCP 配置均受路径沙箱约束；符号链接越界不会被加载。

## Git、Checkpoint 与恢复

- Checkpoint 是内容寻址快照，**不是** Git commit。
- Dirty workspace 恢复冲突时返回 `error=conflict`，不会静默覆盖。
- 服务重启会为未完成工具写入 `service_interrupted`，并自动排队恢复 Turn（最多 3 次）。

## 队列、Rules/Skills/MCP/Hooks、Subagent、记忆

- 任务队列：SQLite lease claim + worker heartbeat；同项目主写锁串行。
- Rules / Skills：预算注入 system prompt；恶意越界文件被拒绝。
- MCP / Hooks：stdio MCP + 崩溃重连；Hook 不能削弱硬拒绝。
- Subagent：explore / reviewer / test_runner / implementer；worktree 由服务端分配路径。
- 项目记忆：候选 → 用户批准 → 检索注入；与 Conversation checkpoint 分离。详见阶段十八。

## 数据备份与迁移

```bash
./scripts/backup_data.sh                 # 打包 data/ workspaces/ builds/
python3 scripts/migrate_db.py --backup   # 幂等 schema 确保 + 可选备份
python3 scripts/scan_secrets.py          # 敏感信息扫描
```

迁移到云服务器时持久化：

```text
data/
workspaces/
builds/
```

可通过 `AGENT_DATA_DIR` 把账号与任务库放到独立磁盘。生产环境应使用 HTTPS，并为 `/api/register` 限流。

## 桌面打包与 Android 发布

**Desktop（Electron）**

```bash
cd desktop
npm install
npm start          # 开发
# 发布可用 electron-packager / electron-builder（按目标平台安装对应工具后打包）
# 产物勿提交仓库；分发前运行 npm run check && npm run test:unit
```

**Android APK**

```bash
cd android-app
./gradlew assembleRelease   # 需本机 keystore / 签名配置
# 或调试包：
./gradlew assembleDebug
# 输出：app/build/outputs/apk/debug/app-debug.apk
```

## 安全边界与已知限制

- 路径沙箱、命令 argv 隔离、下载 SSRF 基础防护、日志/事件脱敏、用户 IDOR 隔离。
- 已知限制：密钥自由文本检测非完备；审批超时下限 30s；索引/记忆为单机 SQLite；真实 Gradle 构建依赖本机 SDK；Eval 中构建步骤为 mock。

## 迁移到云服务器

代码不依赖本机账号系统。迁移时复制项目代码，并持久化 `data/`、`workspaces/`、`builds/`。若以后需要多实例部署，可保持 API 不变，将 `UserStore` 的 SQLite 实现替换为云数据库。
