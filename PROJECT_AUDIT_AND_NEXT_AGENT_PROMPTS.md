# Android Agent 全项目代码审计与下一阶段 Agent 提示词

审计日期：2026-07-30
审计提交：`d3010e83d11a7f26a9d7e45d40ac3680458c0614`
项目路径：`/Users/sakura/Android Agent`

## 1. 审计范围

按照要求，本次没有把根目录 `src/`、`tests/`、`desktop/tests/`、Android
`src/test/` 和 `evals/` 当作待审计产品代码阅读。测试代码仅被执行，用于验证当前状态。

完整阅读的产品代码包括：

- `agent/*.py`：50 个 Python 模块，共 19,649 行。
- `agent/web/`：浏览器管理界面。
- `desktop/src/`：Electron、Monaco、xterm 桌面端。
- `android-app/app/src/main/`：Android 客户端 Kotlin、Manifest、布局和安全配置。
- `template/`：Agent 创建 Android 项目时复制的模板。
- `scripts/`、`setup-env.sh`、`requirements.txt`、`config.yaml.example`。

没有读取或修改真实 API Key，也没有调用真实模型。工作区原有代码未被修改。

## 2. 当前架构结论

当前项目已经不是简单 MVP。它已经具备：

- FastAPI 服务、用户与项目隔离、Conversation Event 权威历史。
- OpenAI-compatible 与 Anthropic 上下文重建。
- 流式任务事件、WebSocket、审批、checkpoint、diff、恢复。
- 文件、Gradle、搜索、下载、进程、MCP、规则、Skill、记忆和子 Agent。
- Android、Electron、浏览器三套客户端。
- SQLite 持久任务、lease、事件分页和部分故障注入测试。

主要问题不是“功能太少”，而是安全边界、任务状态机、工具执行隔离和客户端契约没有随功能
数量同步收紧。以 Cursor、Codex、Claude Code 为目标继续扩功能前，必须先修 P0/P1。否则新增
能力会放大现有宿主机执行风险和任务重复执行风险。

## 3. P0：发布前必须修复

### P0-01 无认证局域网远程代码执行

证据：

- `agent/config.py:252` 默认监听 `0.0.0.0`。
- `agent/api.py:268-279` 没有 Token 时直接映射为 `local` 用户。
- `agent/api.py:260-266` CORS 允许任意来源、任意方法、任意请求头，并允许凭证。
- `agent/api.py:133-139` 和 `agent/api.py:1384-1410` 允许客户端提交任意 `argv`、
  `shell` 和 `env` 创建终端。
- `agent/terminal.py:323-340` 以 Agent 服务进程的宿主机权限启动该进程。

影响：

同一局域网中的任意设备无需注册、无需 Token，即可使用 `local` 身份访问项目，并通过终端
API 在运行 Agent 的 Mac 上执行任意命令。这是完整的宿主机远程代码执行，不只是工作区写入。
浏览器页面还可利用宽松 CORS 对本地服务发起跨站请求。

修复要求：

- 默认只监听 `127.0.0.1`。
- 非回环监听必须强制认证，缺少 Token 一律 401。
- `/api/register` 改为默认关闭，或要求本机显示的一次性配对码。
- CORS 使用明确来源白名单，禁止 `* + credentials`。
- Terminal API 默认关闭；开启时也必须经过独立高风险授权。

### P0-02 生产子 Agent 暴露测试后门，可写宿主机任意路径

证据：

- `agent/subagent_tools.py:43-55` 从模型工具输入中读取未公开的 `fake_result`。
- `agent/tool_runtime.py:32-46` 只检查 required 字段，不拒绝 schema 外字段。
- `agent/subagents.py:321-335` 信任 `fake_result.edits`，只检查字符串中的 `..`。
- 当 `path` 是绝对路径时，`workspace / path` 仍是绝对路径，随后直接 `write_text()`。

影响：

模型或恶意提示可以向 `spawn_subagent` 添加隐藏的 `fake_result` 字段，并借此绕过正常 Agent
工具和权限系统，直接覆盖运行用户可写的任意宿主机文件。

修复要求：

- 从生产代码和生产工具输入中彻底删除 `fake_result`。
- 测试通过依赖注入 fake executor，不通过模型可见工具参数注入。
- 对所有工具执行完整 JSON Schema 校验，并默认 `additionalProperties: false`。
- 所有路径统一使用 `Path.resolve().relative_to(workspace.resolve())` 校验。

## 4. P1：高风险与核心可靠性问题

### P1-01 Terminal 和进程工具没有真正沙箱

`agent/terminal.py:320-340` 与 `agent/processes.py:269-315` 只限制初始 cwd，进程仍可读取
HOME、访问网络、操作工作区外文件并启动子进程。`agent/terminal.py:55-58` 的危险命令正则
只拦截极少数文本形式，Python、重定向、编码命令、`rm -rf ~` 等均可绕过。Terminal API
还绕过 `tool_runtime` 的统一审批。

建议：默认禁用交互终端；把进程执行收敛到统一 broker；最小环境、命令 allowlist、资源限制、
进程组清理、网络策略和每次高风险审批都应在服务端强制实施。

### P1-02 Worker 执行任务时不会续租，可能重复执行

`agent/worker.py:90-110` 只在 worker 主循环顶部 heartbeat；进入同步 `_execute()` 后，
主循环被阻塞。默认 lease 为 300 秒，而 `agent/database.py:968-1044` 会重新领取 lease
过期的 running 任务。长模型调用、Gradle 或审批等待超过 5 分钟时，另一 worker 可重复执行
同一任务和副作用。

建议：每个已领取任务使用独立 heartbeat 定时器；所有状态提交带 claim owner 和 fencing
token；失去 lease 的执行器必须立即停止，且不能再写成功状态。

### P1-03 自动恢复会在重启后主动重新执行模型和工具

`agent/jobs.py:45-56` 在启动恢复后调用 `_schedule_recovery_jobs()`，
`agent/jobs.py:431-545` 自动创建新任务并要求模型继续。这超出了“修复事件链、不自动恢复执行”
的安全边界。用户未发起新请求，系统就可能重新运行只读或有副作用工具。

建议：启动时只把旧 Turn 标记 `interrupted` 并补齐孤立 `tool_result`；自动继续默认关闭。
提供显式“恢复任务”操作，由用户确认后创建新 Turn。恢复审批只做一次并由统一权限层处理。

### P1-04 子 Agent 角色限制没有执行，所谓并行实际上串行

- `agent/subagent_roles.py:75-129` 声明了 `allowed_tools`、`permission_mode` 和
  `system_prompt`。
- `agent/subagents.py:349-372` 调用普通 `run_agent()` 时没有传入这些限制。
- `agent/subagents.py:239-277` 为等待子任务，递归调用同一个单线程 worker 的 `run_once()`。

因此 explore/reviewer 角色仍可获得完整写入、进程和网络工具；多个子 Agent 不是真并行，
还会覆盖单 worker 的 `_running_task` 状态。子 Agent 中间规范工具链也没有完整写入其
Conversation Event，只保存 task event 和最终回答。

建议：角色策略进入 `ToolContext` 并由注册表过滤；子 Agent 使用受限 prompt、工具集和
permission mode；采用有容量上限的 worker pool；父子任务各自 heartbeat 和规范事件链。

### P1-05 工作区路径和索引存在 symlink/前缀逃逸

- `agent/tools.py:85-90` 使用字符串 `startswith()` 判断路径归属。
- `agent/tools.py:191-200` 的旧 `list_dir()` 不执行读目录白名单。
- `agent/repo_index.py:243-290` 使用 `rglob()` 和 `is_file()`，没有拒绝 symlink。
- `agent/context_planner.py:38-47` 直接读取索引返回的路径。

`/workspace/demo-2` 会被误判为位于 `/workspace/demo` 内。工作区中的外部 symlink 也可能
被索引并进入模型上下文。

建议：建立唯一 `SafeWorkspacePath` 组件；使用 `relative_to`/`commonpath`；默认拒绝
symlink 和特殊文件；读取、写入、glob、索引、checkpoint、Skill、Rule 共用同一校验器。

### P1-06 下载工具的 SSRF 防护可被 DNS 绕过

`agent/tools.py:483-519` 只检查 URL 文本和 IP literal，没有解析域名的 A/AAAA 记录。
域名解析到 `127.0.0.1`、局域网、云 metadata 或通过 DNS rebinding 变化时仍可访问。
重定向也只重复文本校验。

建议：每一跳解析并固定公网 IP，阻断 IPv4/IPv6 的 loopback、private、link-local、
reserved 和 metadata；连接必须使用已验证地址并保持正确 Host/SNI；更稳妥的方案是受控
egress proxy。

### P1-07 MCP 会在结果未知时重复有副作用调用

`agent/mcp_manager.py:248-287` 在 timeout/transport error 后重连并自动再次 `call_tool()`。
第一次调用可能已在 MCP 服务端完成，只是响应丢失，第二次会重复删除、发送或修改操作。

建议：工具调用默认 at-most-once；只有明确标记 idempotent 且携带幂等键的调用才能重试。
结果未知时记录 `indeterminate` 并交给用户决定。

### P1-08 MCP 信任没有覆盖实际可执行内容

`agent/mcp_config.py:180-209` 的 trust fingerprint 只哈希 `.android-agent/mcp.json`。
如果配置指向工作区脚本，脚本修改后仍保持 trusted。`agent/mcp_client.py:96-120` 还把
Streamable HTTP 暴露为配置选项，但实现只是 placeholder；stdio 子进程关闭时也未可靠回收
整个进程组。

建议：信任清单包含解析后的 command、args、cwd、可执行文件和脚本哈希；任何变化都撤销
信任。未实现 transport 不进入公共 schema。启动 MCP 使用进程组并完整关闭 stdin/stdout/
stderr。

### P1-09 Worktree 的“密钥清理”会污染待合并分支

`agent/worktrees.py:100-139` 创建 worktree 后删除疑似密钥文件并提交 scrub commit，
`agent/worktrees.py:307-337` 随后可能把该分支 merge 回主分支。如果主仓库追踪了 `.env`、
keystore 或名称含 token/secret 的合法文件，合并 Agent 修改时会顺带删除主分支文件。

建议：worktree 创建过程不得产生业务 commit；通过 sparse checkout、隔离副本、挂载策略或
运行时拒读保护密钥。合并前只选择 Agent 自己的变更集，并显示精确 diff。

### P1-10 Pause/Resume 存在双执行竞态

`agent/database.py:1156-1181` pause 直接把 running 任务改为 paused；resume 又清空 claim
并改回 queued。旧执行线程可能尚未到达检查点，resume 后新 worker 可以领取同一任务，形成
两个执行器。任务状态机也没有 fencing version。

建议：pause 先写 `pause_requested`，执行器在安全点确认 `paused` 并释放 lease；只有确认
paused 后才能 resume。状态迁移使用 compare-and-swap 和版本号。

### P1-11 Compact 会生成非法工具历史

- `agent/compact.py:49-60` 直接截断 `function.arguments`，得到非法 JSON。
- `agent/compact.py:93-115` 逐条删除 OpenAI 消息，可能拆散 assistant tool call 和 tool
  result。
- `agent/compact.py:154-170` 对 Anthropic 也逐条删除，可能破坏 role 和 tool_use/result
  配对。

建议：以“用户消息 + assistant 响应 + 全部工具结果”为不可拆分 group 压缩；arguments 始终
保持合法 JSON；工具输出压缩为结构化摘要，不能制造孤立工具事件。

### P1-12 桌面端与服务端已有三个确定的 API 契约回归

- `desktop/src/agent-api.js:161-165` 发送任务消息时缺少服务端
  `agent/api.py:118-121` 必填的 `message_key`，steer/follow-up 返回 422。
- `desktop/src/agent-api.js:211-213` 请求不存在的
  `/projects/{id}/turns/{turn}/diff`；真实接口是 `agent/api.py:808-823` 的
  `/projects/{id}/diff?turn_id=...`。
- `desktop/src/agent-api.js:204-208` 发送 `{scope}`，服务端
  `agent/api.py:114-115` 只识别 `{path}`，所以“单文件恢复”会变成整 checkpoint 恢复。

建议：生成或共享 OpenAPI client；服务端 schema 设 `extra="forbid"`；加入覆盖每个客户端
操作的契约测试。

### P1-13 Electron renderer 权限过大

`desktop/src/main.js:242-257` 使用 `sandbox:false`；`desktop/src/preload.js:10-20` 向
renderer 暴露通用文件 API；`desktop/src/main.js:451-500` 对 renderer 传来的绝对路径没有
根目录 allowlist。CSP 在 `desktop/src/index.html:7` 允许 `unsafe-inline` 和
`unsafe-eval`，也没有看到统一的 navigation/window-open 拦截。

影响：一旦 renderer 出现 XSS 或依赖注入，攻击者可直接读写宿主机文件。

建议：启用 Chromium sandbox；main process 保存用户批准的 root capability，renderer 只传
相对路径或不可伪造 handle；禁止任意导航和窗口；收紧 CSP；所有服务端文本只用
`textContent` 或可信 sanitizer。

### P1-14 Android 把控制凭证和 APK 安装放在明文链路

- `AndroidManifest.xml:8-15` 允许备份和 HTTP 明文流量。
- `network_security_config.xml:3` 对所有域名允许 cleartext。
- `AgentPrefs.kt:7-16` 用普通 SharedPreferences 保存长期 bearer token。
- `AgentApi.kt:460-466` 把 token 放进 WebSocket URL。
- `AgentApi.kt:625-636` 直接覆盖目标 APK，没有临时文件、摘要或签名验证。
- App 具有 `REQUEST_INSTALL_PACKAGES` 并直接发起安装。

同一网络中的攻击者可窃取 Token、篡改 APK，再诱导用户安装。

建议：生产只允许 HTTPS/WSS并支持证书固定或本地配对证书；Token 使用 Android Keystore
保护；WebSocket 使用短期一次性 ticket；APK 返回 SHA-256 和签名身份，客户端校验后再原子
替换和安装。

### P1-15 删除项目时没有覆盖所有活动资源

`agent/api.py:666-674` 只阻止 queued/running 任务，未阻止 `awaiting_approval`、`paused`
任务，也未检查终端、MCP、worktree。项目目录可能在活跃进程下面被删除。

建议：引入 project lifecycle lock；删除前停止并确认所有任务、终端、MCP 和 worktree，
然后事务性标记 deleting，最后清理文件。

## 5. P2：重要完善项

### P2-01 工具参数无效时不应以空对象继续执行

`agent/loop.py:670-690` 和 `agent/stream.py:243-252` 在 JSON 解析失败时退化为 `{}`。
配合只检查 required 的 runtime，可能让工具以默认参数执行。应记录
`malformed_tool_call`，生成失败 `tool_result`，绝不执行 handler。

### P2-02 Token 在 URL、localStorage 和备份中泄漏

桌面端 `agent-api.js:249-263,307-378` 把 Token 放在 WebSocket query；`state.js:75-120`
把 Token 存 localStorage。Android 同样使用 query token 和普通偏好存储。URL 会进入代理、
访问日志、崩溃报告和截图。应改为一次性 WS ticket，并使用系统安全凭证库。

### P2-03 下载失败会留下空文件或半文件

后端 `agent/tools.py:555-600` 在验证最终响应前打开目标文件，部分 redirect/HTTP/长度失败
直接 return，留下空文件。Android 下载也直接覆盖缓存 APK。应写同目录临时文件，完成大小、
摘要和内容类型校验后 `os.replace()`，所有失败路径清理临时文件。

### P2-04 进程和 Worker 生命周期存在可观察泄漏

- `agent/processes.py:244` 的 `_StreamReader.join()` 不接受 timeout，但
  `processes.py:385-387` 以 timeout 调用。
- 本次测试出现 MCP stdout/stderr `ResourceWarning`。
- 本次测试多次出现已删除临时 DB 上仍运行的 worker。
- Terminal 测试出现 `select()` 收到无效 fd 的 TypeError。

建议：所有后台对象实现显式 `close()`/context manager；FastAPI lifespan 统一 start/stop；
测试 teardown 断言无线程、子进程和打开管道残留。

### P2-05 规范事件、Turn 和 Task 状态不是一个原子提交

`agent/jobs.py` 在多个 SQLite transaction 中分别写 canonical event、Turn status、Task
status。进程在中间崩溃时会出现 completed event + running task 等矛盾。应增加生命周期
Unit of Work，或启动时做完整 reconciliation，并记录修复审计事件。

### P2-06 当前“语义摘要”仍是有损截取

`agent/conversation_summary.py:18-94` 名称是 semantic checkpoint，但实现为规则抽取。
每轮用户文本只保留 800 字、最终回答 1200 字、工具输出 240 字且最多 12 条
（`conversation_summary.py:109-163`）。checkpoint 一旦生效，遗漏的早期约束不再进入模型。

建议：结构化保存目标、约束、决策、未解决事项、文件状态、测试结果和关键工具事实；每条事实
带来源 seq；生成后做一致性验证；允许失效和重建 checkpoint，原始事件永不删除。

### P2-07 `local` Memory scope 会跨项目泄漏

`agent/memory_store.py:260-263,393-398` 查询某项目时包含该用户所有 `scope='local'` 的记录，
不检查这些记录的 project_id。需明确 local 是设备级还是项目级；若是项目本地，必须同时匹配
project_id。

### P2-08 缺少资源配额和滥用控制

`/api/register` 可无限创建用户；prompt、文件写入、终端输入、事件总量、Conversation 数量、
索引和构建并发没有完整 per-user 配额。SQLite/WAL、build log、APK、checkpoint blob 和
worktree 也没有统一保留策略。

建议：请求体上限、速率限制、用户/项目配额、并发预算、磁盘水位、TTL/保留策略和管理员审计。

### P2-09 浏览器前端已经明显落后于主能力

`agent/web/app.js` 仍以项目和轮询为中心，没有 Conversation、规范事件分页、审批、pause、
steer、follow-up、WebSocket 和安全配对的完整体验。它还明确把空 Token 当 local 使用。

建议：决定保留或删除。若保留，应共享 API client 和状态模型；若仅为调试工具，默认只在
loopback 开启并明确标记，不作为正式客户端。

### P2-10 Android 与后端认证行为互相矛盾

后端允许空 Token 作为 local，浏览器和桌面也依赖该行为；Android
`MainActivity.kt:88-100` 却强制“先注册用户”。这正是此前“输入地址后提示先注册”的直接
原因。认证策略修复后，三端必须统一为同一配对流程。

### P2-11 Android 构建与发布配置不一致

`android-app/app/build.gradle.kts:8` 使用 compileSdk 36，但
`android-app/gradle/libs.versions.toml:2` 仍是 AGP 8.3.0，只验证到 compileSdk 34。
release 关闭 minify，versionCode 仍为 1，也没有正式签名、SBOM、依赖锁和 APK provenance。

建议：升级兼容的 AGP/Gradle/Kotlin 组合；增加 dependency verification/lock；区分 debug
明文配置与 release HTTPS 配置；建立签名、版本和发布产物校验。

### P2-12 依赖和供应链不可复现

`requirements.txt` 只有宽泛最小版本，没有 lock/hash；Gradle 同时使用多个第三方镜像；
Electron 依赖虽有 lock，但没有看到打包签名、自动更新签名和 provenance。应增加 Python
锁文件与哈希、Gradle dependency verification、Electron 打包签名和离线可复现构建。

### P2-13 可选事件错误被静默忽略，诊断不足

`agent/jobs.py:1237-1247,1358-1368` 的 hook 错误直接 pass；MCP reader 和多个 cleanup
分支也吞掉异常。即使这些不是 canonical event，也应写结构化日志、计数器或 diagnostic event，
明确区分“可忽略扩展失败”和“任务正确性失败”。

### P2-14 API 返回过多宿主机内部路径

项目状态和 Job DTO 包含 workspace、APK、build log 等绝对路径。认证修复前尤其危险；认证
修复后也没有必要让移动端知道宿主机布局。建议建立 public DTO，只返回资源 ID 和下载端点。

## 6. 验证结果

### Python

命令：

```bash
python3 -m unittest discover -s tests -v
```

结果：

- 共 254 项。
- 251 通过。
- 1 个 failure，2 个 error。
- 三个失败均在 `WorkerApiTests`。
- 单独重跑该类仍失败，不是偶发顺序问题。
- 直接复现得到 `POST /api/projects/{id}/ask -> 503 {"detail":"未配置 LLM API Key"}`。

原因：API 在 `agent/api.py:368-369,503-504` 于 fake `run_agent` 生效前强制要求 API Key，
但 Worker API 测试没有注入 fake Settings。产品拒绝无 Key 本身合理，测试夹具和依赖注入接口
需要同步修正。

测试同时出现：

- 已销毁临时目录后后台 worker 仍访问 SQLite 的日志。
- MCP 管道未关闭的 `ResourceWarning`。
- Terminal reader 对无效 fd 调用 `select()` 的 TypeError。

### Desktop

命令：

```bash
npm run check
npm run test:unit
```

结果：全部通过，包括 `agent-api.test`、`state.test`、`events.test`。但现有测试没有覆盖上文
三个真实 API 契约漂移。

### Android

命令：

```bash
./gradlew testDebugUnitTest assembleDebug --offline
```

结果：未完成。离线缓存缺少 `androidx.coordinatorlayout:1.2.0`。构建同时警告 AGP 8.3.0
不支持 compileSdk 36。为了不访问外网，本次没有改用在线依赖下载。

### 其他

```bash
python3 scripts/scan_secrets.py
git diff --check
```

结果：密钥扫描通过；原有代码无空白错误。

## 7. 推荐实施顺序

1. 安全入口：认证、配对、CORS、Terminal 默认关闭。
2. 工具边界：删除 fake 后门、完整 schema 校验、统一安全路径。
3. 执行隔离：进程 broker、Terminal 沙箱、下载 SSRF。
4. 任务可靠性：lease heartbeat、fencing、pause/resume、手动恢复。
5. 扩展安全：MCP at-most-once、信任清单、worktree 合并。
6. 上下文正确性：原子工具组 compact、结构化 checkpoint、Memory scope。
7. 客户端统一：OpenAPI 契约、凭证、WS ticket、APK 校验。
8. 发布工程：资源配额、生命周期清理、依赖锁、签名和最终审计。

不建议在完成前四项之前继续增加更多可执行工具或自动化副作用。

## 8. 可直接交给 Agent 的分步提示词

下面每个提示词都应在前一阶段完成并提交后单独使用。

### 提示词一：认证与网络安全入口

```text
# 阶段一：认证、配对、CORS 与高风险入口封锁

项目路径：/Users/sakura/Android Agent

请直接阅读代码并实施，不要只输出方案。保留工作区已有改动，不修改 config.yaml 或 API Key，
不调用真实模型或网络。先阅读 PROJECT_AUDIT_AND_NEXT_AGENT_PROMPTS.md 的 P0-01。

目标：
1. 默认 server_host 改为 127.0.0.1。
2. 所有非回环访问必须携带有效 Bearer Token；缺少 Token 不再映射为 local。
3. /api/register 默认关闭，新增显式配置开关和一次性配对码流程。配对码只在本机控制台生成，
   有有效期、单次使用、失败次数限制，不能写入日志或 API 响应列表。
4. CORS 改为配置化精确 allowlist，禁止 wildcard 与 credentials 组合。
5. WebSocket 不再接受长期 Token query。新增短期、单次、绑定 user/resource 的 WS ticket。
6. Terminal API 默认关闭；未开启返回 404。开启时仍要求认证和独立 terminal capability。
7. /api/health 不泄露内部网络、绝对路径或不必要配置细节。
8. /api/register、登录/配对和任务创建增加基础速率限制。

兼容要求：
- Android、desktop、agent/web 更新为统一配对流程。
- 旧 Token 仍可认证，但空 Token 只允许显式 test setting，不允许生产默认。
- 其他用户资源继续统一返回 404。

测试：
- 未认证 LAN HTTP/WS/Terminal 全部拒绝。
- loopback 也不能凭空获得 local 身份。
- 配对码过期、复用、错误次数和并发消费。
- CORS 非白名单来源拒绝。
- WS ticket 只能消费一次且不能跨 job/terminal 使用。
- 全部测试不得访问真实网络或模型。

运行：
python3 -m unittest discover -s tests -v
cd desktop && npm run check && npm run test:unit
cd ../android-app && ./gradlew testDebugUnitTest assembleDebug --offline
git diff --check

最终回复列出威胁模型、配置迁移、接口变化、客户端兼容和测试数量。
```

### 提示词二：工具校验、路径边界与子 Agent 后门

```text
# 阶段二：统一工具输入校验与工作区文件安全

项目路径：/Users/sakura/Android Agent

请实施 PROJECT_AUDIT_AND_NEXT_AGENT_PROMPTS.md 的 P0-02、P1-04、P1-05、P2-01。
不调用真实模型或网络，不提前改 Worker 架构。

要求：
1. 删除生产 spawn_subagent 输入和执行路径中的 fake_result。
2. 测试 fake 通过构造器/函数依赖注入，不得由模型输入控制。
3. 使用成熟 JSON Schema validator 校验所有内置、动态和 MCP 工具输入：
   type、required、enum、array item、长度/数值边界全部生效，默认拒绝额外字段。
4. 无效 JSON 或 schema 不匹配形成明确 tool_result，handler 绝不能执行。
5. 新增 SafeWorkspacePath 单一组件，所有文件读写、list、glob、grep、索引、context planner、
   rule、skill、checkpoint、worktree edit 使用它。
6. 使用 resolve + relative_to/commonpath；拒绝绝对路径、..、NUL、symlink、FIFO/device/socket。
7. 修复 allowlist 的字符串前缀误判，例如 AndroidManifest.xml.bak 和 sibling-prefix。
8. 子 Agent 的 allowed_tools、permission_mode、system_prompt 在服务端真正强制执行。
9. explore/reviewer 必须无法调用写入、进程、网络、MCP 和 spawn_subagent。

测试至少覆盖：
- fake_result 和任意额外字段被拒绝。
- 绝对路径、同名前缀兄弟目录、symlink、嵌套 symlink、特殊文件逃逸。
- repo index 不读取工作区外内容。
- malformed arguments 不执行工具。
- 每个子 Agent 角色的允许/拒绝矩阵。
- 现有 OpenAI/Anthropic 工具链保持兼容。

完成后运行全部 Python、desktop 和可离线 Android 验证，并给出安全回归证据。
```

### 提示词三：进程、Terminal 与网络工具隔离

```text
# 阶段三：进程执行 broker、Terminal 隔离和下载 SSRF 修复

项目路径：/Users/sakura/Android Agent

直接实施，不调用真实网络或模型。先阅读 agent/processes.py、agent/terminal.py、
agent/tool_runtime.py、agent/tools.py、agent/api.py。

目标：
1. 所有进程执行统一经过 ProcessBroker 和 permission engine，Terminal 不得旁路。
2. Terminal 保持默认关闭；开启后每个 session 绑定 user、project、capability、cwd 和资源预算。
3. 禁止 shell=True 和任意 shell 路径；采用 argv allowlist，最小环境不得含 HOME 中的凭证路径。
4. 进程组必须可靠 terminate/kill/wait，关闭全部管道，修复 StreamReader.join(timeout)。
5. 限制 CPU、内存、打开文件数、进程数、输出总字节、运行时长和并发 session。
6. 下载每一跳解析 A/AAAA 并阻断全部非公网地址；测试使用 fake resolver/fake transport。
7. DNS 解析结果必须与实际连接地址绑定，防止 DNS rebinding。
8. 下载写临时文件，完整校验大小、摘要和 content type 后原子替换。
9. 取消、timeout、HTTP error、重定向过多时不留下目标或临时文件。

禁止：
- 不用危险命令文本正则作为主要安全边界。
- 不在测试中访问真实网络。
- 不声称仅限制 cwd 就是沙箱。

测试覆盖命令绕过、子进程清理、输出配额、私网 IPv4/IPv6、DNS rebinding、重定向、
部分下载、取消和原子写入。完成后运行全量测试。
```

### 提示词四：持久 Worker、Lease 与安全恢复

```text
# 阶段四：Worker lease、fencing、状态机和显式恢复

项目路径：/Users/sakura/Android Agent

请直接修改 agent/worker.py、agent/database.py、agent/jobs.py、agent/approvals.py 及相关模块。
不调用真实模型；使用 fake 慢模型和 fake 工具。

目标：
1. 每个 claimed task 在执行期间由独立定时器续租，不依赖被 _execute 阻塞的主循环。
2. claim 时生成单调 fencing token。所有 Task/Turn 终态和关键事件写入都验证 owner + token。
3. lease 丢失后旧执行器停止，不能写 succeeded、不能继续工具。
4. pause 使用 pause_requested -> paused 两阶段；只有执行器确认 paused 后才能 resume。
5. resume 不得与旧执行器并行。
6. 服务启动只修复中断事件链，不自动创建恢复任务、不自动调用模型或工具。
7. 新增显式 resume-interrupted API，必须由用户操作并创建新 Turn。
8. 同一个恢复工具只审批一次；审批决策集中在 tool_runtime。
9. Task、Turn、canonical terminal event 尽量在一个 SQLite transaction 中提交；无法合并的
   状态通过 reconciliation 修复并留下审计事件。
10. FastAPI lifespan 负责 worker start/stop，测试结束无线程访问已删除数据库。

测试：
- 超过 lease 的慢任务不被第二 worker 领取。
- 强制丢 lease 后旧 worker 被 fencing。
- pause/resume 无双执行。
- 重启不自动执行任何工具。
- 显式恢复完整上下文且有副作用工具需用户确认。
- 多 worker、多项目并发和同项目单写者。
- 测试结束没有后台线程、打开数据库或管道。

运行全量测试并报告状态迁移表和故障注入结果。
```

### 提示词五：MCP 与 Worktree 安全语义

```text
# 阶段五：MCP at-most-once、可执行信任与 Worktree 合并安全

项目路径：/Users/sakura/Android Agent

不访问真实 MCP/网络，使用 fake stdio server。直接实施。

MCP 要求：
1. timeout/transport error 后默认不重试 tool call，返回 indeterminate。
2. 只有 schema 明确声明 idempotent 且调用携带稳定 event_key 时允许自动重试。
3. trust fingerprint 覆盖规范化 config、command、args、cwd、可执行文件及引用脚本哈希。
4. 任一内容变化立即撤销 trust。
5. 未实现的 Streamable HTTP 不得出现在可用 transport 中。
6. 子进程使用独立进程组，close 后回收子孙进程和所有管道。
7. MCP 工具也走完整 schema、permission、approval 和 canonical tool event。

Worktree 要求：
1. 创建 worktree 不得删除或提交任何主仓库追踪文件。
2. 密钥隔离通过运行时访问控制或隔离副本完成。
3. 合并只包含 Agent 变更，不能夹带 scrub commit。
4. 合并前检测主分支变化、冲突、secret 文件删除和越权路径。
5. 默认输出可审阅 patch；merge/keep/discard 都幂等。

测试覆盖响应丢失但服务端已执行、脚本变更撤销信任、进程清理、追踪 .env/keystore 不被删除、
主分支并发变化和冲突回滚。运行全部测试。
```

### 提示词六：上下文压缩、Checkpoint 与 Memory 正确性

```text
# 阶段六：Provider 无关的安全上下文压缩与结构化长期状态

项目路径：/Users/sakura/Android Agent

不调用真实模型。保留 conversation_events 为权威来源，原始事件永不删除。

要求：
1. OpenAI/Anthropic compact 以完整交互 group 为单位，不能拆散 tool call/result。
2. function.arguments 在任何压缩后仍是合法 JSON。
3. 结构化 checkpoint 至少包含 goal、constraints、decisions、unresolved、files、tests、
   tool_facts、errors，每项带来源 event seq。
4. checkpoint 生成后运行确定性 validator，发现未知工具 ID、缺失来源或超出覆盖范围则拒绝。
5. checkpoint 支持版本、失效、重建；只使用最后一个有效且连续覆盖的 checkpoint。
6. 当前用户输入不重复，失败和中断工具事实不能被摘要成成功。
7. 明确定义 project/user/local memory scope；项目 local 不能进入其他项目。
8. 记忆提取和召回记录来源、置信度、冲突和用户批准状态。
9. Token 预算按 provider tokenizer 或可替换 estimator，而不是只按字符硬截断。

测试至少覆盖 100+ 轮、多个并行工具、超长 arguments、失败工具、checkpoint 损坏、事实冲突、
跨 Provider 重建和跨项目 memory 隔离。完成后运行全部离线测试。
```

### 提示词七：三端 API 契约与凭证、APK 安全

```text
# 阶段七：OpenAPI 契约统一、客户端凭证与 APK 完整性

项目路径：/Users/sakura/Android Agent

请同时修改 agent/api.py、desktop/src、android-app 和 agent/web。不要只修一个客户端。

要求：
1. 从 FastAPI OpenAPI 生成或维护单一 typed client contract。
2. 修复 desktop message_key、turn diff route、checkpoint path/scope 三个已知漂移。
3. 服务端 Pydantic 请求默认禁止未知字段。
4. Android、desktop、web 使用同一配对、认证、WS ticket 和错误模型。
5. desktop Token 放系统 keychain，不放 localStorage；Android Token 用 Keystore 加密存储。
6. Electron 开启 sandbox；IPC 文件 API 使用 main process 发放的 workspace capability。
7. 禁止 renderer 任意绝对路径访问、外部导航和未知 window.open，收紧 CSP。
8. Android release 禁止 cleartext，关闭凭证备份；debug 明文策略必须显式隔离。
9. APK 下载 API 返回 SHA-256、size、package、version、signing certificate fingerprint。
10. Android 临时下载并校验全部元数据后才允许安装，失败不保留可安装 APK。
11. agent/web 若保留则补齐 Conversation、审批、WebSocket；否则从生产挂载中移除。

测试：
- 每个公开 API 至少有 desktop/Android contract fixture。
- Token 不出现在 URL、日志、localStorage、SharedPreferences、Intent extra。
- Electron renderer 不能读 workspace 外文件。
- APK 篡改、截断、包名不符、签名不符均拒绝。
- 桌面端和 Android 构建、测试全部通过。
```

### 提示词八：配额、可观测性、供应链与最终发布审计

```text
# 阶段八：资源治理、可观测性和发布门禁

项目路径：/Users/sakura/Android Agent

本阶段不新增主要 Agent 功能，完成发布级整理和审计。

要求：
1. 增加 per-user/project 的注册、请求、任务、终端、MCP、索引、存储和构建配额。
2. 对 prompt、JSON body、文件、事件、日志、APK、checkpoint、memory 设置大小与保留策略。
3. 磁盘低水位时拒绝新写任务，不破坏运行任务。
4. 结构化日志包含 request/task/turn/tool correlation ID，但自动脱敏。
5. canonical event 失败、hook 失败、MCP reader 失败和 cleanup 失败均有可查询诊断。
6. public DTO 不返回宿主机绝对路径、连接对象或内部异常。
7. Python 使用带 hash 的锁文件；Gradle 开启 dependency locking/verification；
   Electron 使用 lock、打包签名和更新签名。
8. 对 Android APK、桌面包生成 SBOM、checksum、版本和 provenance。
9. 修复 AGP/compileSdk 兼容，建立 release signing 配置但不提交私钥。
10. release_check 不修改 tracked fixture；测试输出写临时目录或显式 artifact 目录。

最终验证：
python3 -m unittest discover -s tests -v
cd desktop && npm run check && npm run test:unit && npm run test:screenshot
cd ../android-app && ./gradlew testDebugUnitTest assembleDebug --offline
python3 scripts/scan_secrets.py
git diff --check
git status --short

最终回复必须包含测试通过数量、残余风险、威胁模型、依赖清单、发布产物摘要和回滚步骤。
```

## 9. 当前最推荐的下一步

P0、P1 和本报告列出的 14 项 P2 已在 2026-07-30 的加固批次中实施。后续不应重复执行
“提示词一”到“提示词八”，应按下面的状态证据和新提示词继续。

## 10. P2 修复状态与证据

| 项目 | 状态 | 关键实现 |
|---|---|---|
| P2-01 | 已修复 | 非法/非对象工具参数生成 `malformed_tool_call` 和失败 `tool_result`，不执行 handler |
| P2-02 | 已修复 | 单次 WS ticket；桌面 `safeStorage`；Android Keystore；调试 Web `sessionStorage` |
| P2-03 | 已修复 | 服务端与 Android 均临时下载，校验类型、大小、摘要后原子替换，失败清理 |
| P2-04 | 已修复 | FastAPI lifespan 统一关闭 worker、Terminal、MCP；后台 reader 支持有界关闭和诊断 |
| P2-05 | 已修复 | Turn/Task/终态事件同事务提交；启动对账以不可变终态事件为权威 |
| P2-06 | 已修复 | checkpoint v2 分类事实、来源 seq、引用验证、失效事件；不安全时保留原始历史 |
| P2-07 | 已修复 | `local` Memory 必须带 `project_id` 且仅同项目可见 |
| P2-08 | 已修复 | 请求/注册限流、对象与并发配额、磁盘水位、事件和构建产物保留 |
| P2-09 | 已修复 | 浏览器端降级为 loopback-only 调试台，非 loopback/关闭开关时不挂载 |
| P2-10 | 已修复 | Android、Desktop、Web 统一 `/api/pair`，`/api/register` 仅兼容旧客户端 |
| P2-11 | 已修复 | AGP 8.10.1、Gradle 8.11.1、Kotlin 2.1.20、JDK 17、release shrink/sign/version 注入 |
| P2-12 | 已修复 | Python hash lock、npm lock/audit、Gradle lock/verification、桌面强制签名、安全更新、发布清单 |
| P2-13 | 已修复 | Hook/MCP/cleanup 可选失败写脱敏 diagnostics，关键规范事件仍失败关闭 |
| P2-14 | 已修复 | Project/Job public DTO 只返回资源 URL，不返回 workspace/APK/log/claim 宿主路径 |

## 11. 下一阶段完整提示词

### 提示词九：多实例资源治理

```text
# 阶段九：把单进程治理升级为多实例一致治理

项目路径：/Users/sakura/Android Agent

先阅读 agent/governance.py、agent/ws_tickets.py、agent/database.py、agent/worker.py 和
agent/api.py。直接实施，不调用真实模型或真实网络。

目标：
1. 将 WS ticket 改为 SQLite 或 Redis 等跨进程、原子消费、带 TTL 的实现。
2. 将注册/请求限流和活动资源配额改为跨进程一致计数。
3. ticket、配额和 lease 使用服务端时间并覆盖时钟跳变。
4. 保留当前 API、单机 SQLite 默认部署和客户端兼容。
5. 增加两个 API 实例并发消费、过期清理、故障重启和用户隔离测试。
6. 不引入自动重放有副作用工具，不把 Bearer Token 放入 URL 或持久日志。

完成后运行全部 Python、Desktop、Android 验证，并说明单机和多实例两种部署语义。
```

### 提示词十：Checkpoint 质量评估与可选模型摘要

```text
# 阶段十：可验证的语义摘要质量闭环

项目路径：/Users/sakura/Android Agent

先阅读 agent/conversation_summary.py、agent/conversation_context.py、agent/compact.py 和
相关 fake 集成测试。不得调用付费/真实模型，先以 fake provider 完成。

目标：
1. 保留 deterministic v2 作为可靠基线。
2. 定义 provider-independent 的结构化摘要 schema 和版本迁移。
3. 可选模型摘要必须逐条引用 source_seq，验证工具 ID、文件、约束和未解决事项。
4. 验证失败时追加 invalidated 事件并自动回退原始事件，不覆盖旧 checkpoint。
5. 建立长历史 eval：约束召回、工具链完整率、事实幻觉率、token 节省率。
6. 支持重建 checkpoint，但不删除原始 conversation_events。
7. 当前 Prompt 不重复，OpenAI/Anthropic 两种投影语义一致。

完成后给出 fake eval 指标、失败样例和启用阈值，不以“看起来更短”作为通过标准。
```

### 提示词十一：可信发布 CI

```text
# 阶段十一：签名、SBOM、provenance 与发布 CI

项目路径：/Users/sakura/Android Agent

阅读 scripts/release_check.py、scripts/generate_release_manifest.py、
desktop/package.json、android-app 构建文件和三套锁文件。不得提交任何私钥。

目标：
1. CI 使用 npm ci、pip --require-hashes、Gradle dependency verification 和 offline 二次构建。
2. Android 和 macOS 使用受保护 secret 注入签名，缺签名立即失败。
3. 对 APK、DMG、ZIP 生成标准 CycloneDX/SPDX SBOM、SHA-256 和可验证 provenance。
4. 校验 APK 证书、macOS codesign/notarization 和更新元数据签名。
5. release_check 不修改 tracked fixture，报告和产物进入独立 artifact 目录。
6. 增加依赖漏洞门禁、锁文件漂移检测和产物保留策略。
7. 文档给出密钥轮换、吊销、回滚和已发布坏版本处置流程。

最终提供 CI run 证据、产物摘要、签名指纹和回滚演练结果。
```
