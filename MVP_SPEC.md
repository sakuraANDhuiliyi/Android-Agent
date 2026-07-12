# Android Agent MVP Spec

## 1. Product Positioning

Android Agent 第一阶段是一个自用的 Android Agent 调试台。

手机 App 不是完整 IDE，而是移动端控制台、输入器、审阅器和 APK 获取入口。真正的代码修改、项目生成、构建、日志分析和有限自动修复都由 Agent 服务端完成。

第一阶段的核心目标不是保证每个生成 App 都达到产品级质量，而是稳定跑通并调试这条闭环：

```text
手机输入临时提示词 -> Agent 修改模板项目 -> 构建 debug APK
-> 自动修复有限轮 -> 手机查看事件、日志、改动和 APK
```

## 2. Deployment Model

第一阶段 Agent 运行在本地电脑或可信局域网服务器上，手机 App 通过 HTTP API 连接 Agent。

- 支持局域网 HTTP，不强制 HTTPS。
- 不做公网部署要求。
- 不做扫码配对或自动发现，手动输入服务端 URL。
- 公网化、HTTPS、反向代理和云部署作为后续阶段。

## 3. User And Identity Model

第一阶段以单用户体验为主，但保留现有 token 隔离结构。

- 后端继续使用 `user_id + Bearer token`。
- 项目目录继续按用户隔离。
- 手机端产品文案不叫账号注册，叫“初始化设备连接”。
- 不做用户名、密码、团队、多设备同步或 SaaS 账号体系。
- Token 保存在手机端本地；API Key 不保存在手机端。

## 4. Core Architecture

项目分为两部分：

- `android-app/`: Android 手机客户端。
- `agent/`: Python/FastAPI Agent 服务端。

现有 `template/` 作为生成目标 Android 应用的基础模板，第一阶段继续沿用，不重新设计模板，不做多模板。

手机端职责：

- 配置 Agent 服务端地址。
- 初始化设备连接。
- 查看项目列表。
- 创建项目。
- 在项目内提交每次任务的临时提示词。
- 查看 Agent 计划、LLM 轮次、工具调用、构建日志、改动文件、token usage 和 APK。
- 下载并引导安装 debug APK。

Agent 服务端职责：

- 用户/token 鉴权。
- 创建基于模板的 Android 项目。
- 管理项目、任务、事件、日志和 APK。
- 调用 LLM。
- 执行受限工具。
- 修改生成项目源码。
- 运行 Gradle 构建。
- 构建失败后自动修复有限轮。

## 5. Android Client Direction

第一阶段继续维护现有 Java/XML Activity 客户端，不迁移 Jetpack Compose。

- 首页：已连接时直接进入项目列表；未连接或连接失败时显示连接引导。
- 项目详情页：以任务输入和 Agent 调试进度为主。
- 文件浏览、APK 下载、构建日志、项目设置作为辅助入口。
- UI 风格偏高密度调试工具，清晰、紧凑、易扫描。
- 跟随系统深色/浅色模式，不做手动主题切换。
- 工具本身中文优先，不做国际化。
- Android `minSdk` 保持现有配置 `24`。

## 6. Project Creation

第一阶段只支持从服务端 `template/` 创建新项目。

- 项目名由用户填写。
- 包名可手动填写。
- 包名不填时由服务端根据项目名生成默认值。
- 包名创建后默认保持稳定，不提供普通 UI 修改入口。
- 项目显示名可以后续支持修改。
- 不支持从手机上传已有 Android 项目。
- 不支持复制项目。
- 支持删除项目，删除前二次确认；第一阶段可以硬删除，不做回收站。

## 7. Generated App Scope

生成目标为原生 Android 项目，基于固定模板修改并构建 debug APK。

- 默认使用现有模板的 `minSdk = 24`。
- 第一阶段只构建 debug APK。
- 不做 release 签名。
- 不做应用商店发布。
- 不做自动更新体系。
- 不限制用户提示词中的 App 类型；可以尝试任何 Android App 需求。
- 系统只保证基础模板、受限文件权限、构建闭环和失败诊断，不承诺所有类型都稳定支持。

## 8. Prompt Model

第一阶段只做每次任务的临时提示词，不做项目级长期提示词。

- 用户每次提交任务时可以自由输入完整需求和行为指令。
- 临时提示词可以影响实现风格和执行偏好。
- 临时提示词不能覆盖安全、隔离、鉴权、路径白名单等硬规则。
- 服务端仍保留基础系统提示词，用于约束工具调用、路径安全、构建和失败修复流程。

## 9. Agent Execution Flow

每个任务默认自动执行到构建结果。

推荐任务流程：

1. 接收用户临时提示词。
2. Agent 输出简短计划。
3. 计划写入任务事件流，不等待用户确认。
4. Agent 读取必要文件。
5. Agent 修改允许范围内的项目文件。
6. Agent 运行 `assembleDebug`。
7. 构建失败时读取日志摘要并尝试修复。
8. 最多自动修复 2 到 3 轮。
9. 成功则保存 APK、日志、改动摘要和最终回复。
10. 失败则保存失败原因、已尝试修复、关键日志摘要和最终回复。

同一项目同一时间只允许一个运行中任务。第一阶段可以先全局串行，后续再扩展不同项目并行。

## 10. Cancellation

第一阶段支持请求取消，不追求所有场景立即强杀。

- 手机端显示停止按钮。
- 后端将任务标记为 `cancel_requested`。
- Agent 在模型调用前、工具调用前、构建前等安全检查点停止。
- 如果正在 Gradle 构建，第一阶段可以等当前构建结束后停止。
- 强制中断模型请求或 Gradle 子进程作为后续增强。

## 11. Task Status And Polling

第一阶段以 job 轮询为主，不依赖 WebSocket。

- 提交任务后后端立即返回 `job_id`。
- 手机端每 1 到 2 秒轮询任务状态。
- App 切后台后不强求持续轮询。
- 回到前台时重新同步项目和任务状态。
- WebSocket 可作为后续增强。

## 12. Debug Event Display

第一阶段默认就是调试模式。

手机端应易于查看：

- 当前任务状态。
- Agent 简短计划。
- Agent 原始回复。
- LLM 轮次。
- provider/model/fallback 信息。
- token usage。
- 工具调用事件。
- 修改文件列表。
- 构建日志摘要。
- 完整日志入口。
- 失败堆栈或错误摘要。
- 任务耗时。

工具调用事件展示粒度：

- 工具名称。
- 参数摘要。
- 结果摘要。
- 耗时。
- 成功/失败状态。
- 完整输入/输出默认折叠，可展开查看。

## 13. LLM And Model Configuration

第一阶段支持现有 OpenAI-compatible 与 Anthropic，不新增专用 provider。

- 服务端 `config.yaml` 保存 API Key、base URL、provider、model 和 fallback。
- 手机端可查看当前模型状态。
- 每次任务可以选择 provider/model，默认使用全局配置。
- 任务记录保存实际使用 provider/model。
- fallback 事件必须记录并展示。
- API Key 只保存在 Agent 服务端。
- 第一阶段不专门支持本地大模型，不做 Ollama/LM Studio/vLLM 专项适配。
- 如果现有 OpenAI-compatible 配置天然可用，可以保留，但不列为第一版目标。

## 14. Token Usage

第一阶段记录基础 token usage，不做成本计算。

- 输入 token。
- 输出 token。
- 总 token。
- provider。
- model。
- 如果 API 未返回 usage，则显示未知。

## 15. File Permissions

第一阶段继续使用服务端白名单限制文件访问。

允许 Agent 操作生成项目 workspace，不允许通过手机端 Agent 修改 Android-Agent 工具自身源码。

推荐读权限：

- `app/src/`
- `app/build.gradle.kts`
- `build.gradle.kts`
- `settings.gradle.kts`
- `gradle/`

推荐写权限：

- `app/src/main/java/`
- `app/src/main/res/`
- `app/src/main/AndroidManifest.xml`
- `app/build.gradle.kts`

不允许修改：

- Gradle wrapper。
- `settings.gradle.kts`。
- 根级构建脚本。
- workspace 外路径。
- 其他用户目录。
- Agent 服务端源码。
- Android 客户端源码。

## 16. Resources

第一阶段只支持文本、XML 和 vector 类资源。

- 支持 Kotlin/Java 源码。
- 支持 Manifest。
- 支持 layout、values、drawable XML。
- 支持 vector drawable。
- 允许 Agent 修改 `app/build.gradle.kts` 添加必要依赖。
- 不支持手机上传图片。
- 不支持 Agent 生成 PNG/JPG。
- 不支持字体文件管理。
- 不支持任意二进制资源上传或编辑。

## 17. Build And Failure Handling

每次任务完成代码修改后必须尝试构建。

- 默认运行 `assembleDebug`。
- 构建成功后保存最新 APK。
- 每次成功构建的 APK 与任务记录关联。
- 项目主界面突出最新 APK。
- 历史任务中可查看对应 APK。
- 构建失败时保存完整日志。
- 传给 LLM 修复时只传关键错误摘要和日志尾部。

日志摘要策略：

- 提取 `ERROR`、`Exception`、`FAILED`。
- 提取 Kotlin/Java 编译错误。
- 提取 manifest/resource merge 错误。
- 保留最后 80 到 150 行。
- 如果日志太长，保留首个关键错误块和尾部摘要。

## 18. APK Download And Install

构建成功后手机端提供下载和安装引导。

- 下载 debug APK。
- 下载完成后提供安装按钮。
- 使用 Android 系统安装 Intent。
- 首次安装时引导用户开启允许安装未知来源。
- 保留 APK 文件分享能力。
- 不做 release 签名、商店发布或自动更新。

## 19. Task History And Storage

任务历史保存在 Agent 服务端，手机端只拉取展示和缓存。

结构化状态使用 SQLite 管理；workspace、APK、构建日志保存在文件系统。

项目最小字段：

- `id`
- `user_id`
- `name`
- `package_name`
- `created_at`
- `updated_at`
- `workspace_path`
- `latest_status`
- `latest_apk_path`
- `latest_task_id`

任务最小字段：

- `id`
- `user_id`
- `project_id`
- `prompt`
- `status`
- `created_at`
- `started_at`
- `finished_at`
- `final_message`
- `error_message`
- `apk_path`
- `build_log_path`
- `changed_files`
- `cancel_requested`

任务状态：

- `queued`
- `running`
- `succeeded`
- `failed`
- `canceled`

事件表建议字段：

- `id`
- `task_id`
- `type`
- `message`
- `payload`
- `created_at`

## 20. Change Summary And Diff

第一阶段不使用 Git，不做一键回滚。

改动追踪采用轻量方案：

- 任务开始前记录允许写入区域的文件清单和内容哈希。
- 任务结束后重新扫描。
- 对比新增、修改、删除文件。
- 对文本文件生成简单 diff。
- 保存本次任务的改动摘要。

不做：

- Git commit。
- 分支。
- merge。
- 冲突解决。
- 一键回滚。

## 21. Notifications And Background

第一阶段支持本地通知提醒任务完成或失败。

- App 前台时直接更新界面。
- App 后台时任务完成/失败发本地通知。
- 不做远程推送。
- 不做复杂通知操作按钮。
- Android 13+ 请求通知权限。
- 手机端断开不会影响服务端任务执行。

## 22. Offline Behavior

第一阶段不做真正离线模式。

- 无法连接 Agent 服务端时不能提交新任务。
- 可以显示最近缓存的项目或任务数据。
- 不保证离线下载 APK。
- 回到前台或网络恢复后重新同步服务端状态。

## 23. API And CLI

主路径是 Android 客户端调用 HTTP API。

- 保留 CLI 用于启动服务和开发调试。
- 保留 FastAPI `/docs` 和 `/openapi.json`。
- 不额外开发文档站。
- 不引入 Redis、Celery 或独立 worker。
- 任务执行继续使用 Agent 服务进程内后台任务。

## 24. Data Retention

第一阶段不自动清理旧数据，优先保留调试历史。

- 保留任务历史。
- 保留事件。
- 保留构建日志。
- 保留成功构建的 APK。
- 自动清理、空间统计和手动清理后续再做。

## 25. Testing

第一阶段后端核心逻辑需要最小自动化测试。

重点覆盖：

- 用户注册/token 鉴权。
- 项目创建。
- 路径隔离。
- 写入白名单。
- 任务状态流转。
- 取消标记。
- 构建日志摘要提取。
- SQLite 持久化。

Android 客户端第一阶段以手工测试为主。

## 26. Error Reporting

第一阶段不接第三方崩溃或错误上报平台。

- 服务端保存任务日志、异常堆栈和构建日志。
- 手机端显示错误并支持复制/分享。
- 后续可增加导出诊断包。
- 不接 Firebase Crashlytics、Sentry 等服务。

## 27. Explicit Non-Goals For MVP

第一阶段不做以下内容：

- 完整手机 IDE。
- 手机本机运行 Agent。
- 云多用户平台。
- HTTPS 强制要求。
- 扫码配对。
- 局域网自动发现。
- 用户名密码账号体系。
- 上传已有 Android 项目。
- 多模板。
- 复制项目。
- Git 管理。
- 一键回滚。
- release 签名。
- 应用商店发布。
- 自动更新。
- 自动真机/模拟器预览。
- 多模型并行对比。
- 本地大模型专项支持。
- 项目级长期提示词。
- 任意二进制资源上传或生成。
- 第三方崩溃平台。
- 完整客户端 UI 自动化测试。

## 28. MVP Success Criteria

第一阶段成功的标准：

- 手机可以稳定连接本地/局域网 Agent。
- 可以初始化设备连接并保存 token。
- 可以创建模板 Android 项目。
- 可以在项目内提交任意临时提示词。
- Agent 会先输出计划，再自动执行。
- 手机端可以看到 LLM 轮次、工具调用、日志、改动、token usage。
- Agent 可以修改允许范围内的项目文件。
- Agent 可以运行 Gradle 构建。
- 构建失败时 Agent 可以读取摘要并自动修复有限轮。
- 成功时手机端可以下载并安装 debug APK。
- 失败时手机端可以看到明确失败原因、关键日志和已尝试修复。
- 任务历史、事件、日志和 APK 在服务端可追溯。
