# 创意广场平台：C2 核心投稿与审核实施记录

日期：2026-09-09，继续更新于 2026-09-11。基准提交：`5ba10bf`。本次在现有工作区继续升级，保留并行的安全、样式、桌面与 Android 修改；未提交、未部署、未修改生产数据。

本次接通「账号 A 保存草稿并投稿 → 后台审核 → 账号 B 浏览、收藏或举报已发布内容 → 管理员处理并按版本下架 → 作者和举报人查看结果」。它覆盖统一计划中 C2 的核心交付和 CG-08 的首批治理；标签合并、多截图、完整素材/账号治理等，以及 C3 的版本化应用和构建验证仍未完成。C1 历史记录见 [目录管理实施记录](CREATIVE_SQUARE_IMPLEMENTATION_STATUS.md)，总体依赖见 [统一升级计划](UNIFIED_UPGRADE_PLAN.md)。

## 已交付的用户流程

1. 正式账号从 Android 创意广场进入「我的创意」，建立本机草稿，填写说明、动态分类、标签、UI 类型、SDK、依赖、接入说明、署名与许可。可编辑源码、选择多个 UTF-8 文本文件，或从自己项目逐个选文件；封面从设备选择。
2. 每次编辑先持久保存到应用私有文件目录。存储按规范化服务地址与账号 ID 隔离，Token 不写入稿件；旧 SharedPreferences 草稿按原命名空间迁移，迁移完成标记最后写入，原始数据保留。未知新格式拒绝覆盖，超限保存保留旧文件。
3. 「同步草稿到云端」只保存作者私有草稿。首次创建的客户端 ID 与原始请求快照持久化；网络中断、响应丢失和重复上传可使用同一请求重试。已有云端修改采用版本检查，发生冲突保留本机内容；重新读取云端会先将未同步修改另存为本机副本。
4. 作者确认全部文件与封面的公开清单后，才可「提交审核」。服务端重新检查身份、所有权、内容摘要、源码与素材、分享确认、许可声明和疑似凭据。提交快照冻结；编辑前须撤回，或将退修/拒绝版本保存为新草稿。
5. 管理员在「审核队列」分页查看待审/审核中的稿件。审核窗口展示作者、版本、封面、当前线上内容与提交内容、逐文件增删改标识，以及作者可见意见与管理员内部备注两个独立字段。支持开始审核、退回修改、拒绝、批准并发布。
6. v1 上架后，作者编辑和提交 v2 不改变 v1 的公开内容；只有明确批准 v2 才切换公开版本。作者撤回、管理员下架或其他审核员先处理后，旧窗口的批准请求失败，不覆盖新的状态。管理员不能从普通编辑接口改写作者源码，也不能直接发布未经批准的社区版本。
7. 「我的创意」提供云端条目、审核意见、审核/下架消息、加载更多和已读操作；作者可撤回审核、主动撤下公开作品。消息保存在服务端，本次通过进入页面/刷新获取，尚无后台推送。
8. 可移除本机草稿或副本；可删除从未发布且不在审核中的云端稿件，删除后本机源码保留为新草稿。服务端保留审核、审计和创建幂等键，避免晚到请求复活已删除稿件；已发布作品保留历史，只能下架。删除稿件时回收不再被版本引用的自有封面，共用封面保留。
9. 正式账号可在公开详情收藏或取消收藏，收藏关系跟随稳定创意 ID，并在内容下架后从可见收藏中隐藏。用户可以选择版权、隐私、危险内容、描述不实或其他原因提交举报；重复请求使用客户端 ID 幂等，同一用户对同一版本、相同原因和说明的重复举报合并。
10. 后台「举报处理」展示待处理数量、待审稿件、社区上架和收藏关系等基础指标；举报详情以纯文本展示举报人说明，公开处理结果与内部备注分开。管理员可开始处理、驳回、确认完成，或使用举报与创意两个版本前置条件执行「处理并下架」。下架在同一创意数据库事务中更新公开状态、目录 generation、审计和通知，并解决该版本其余待处理举报；举报数量本身不会自动永久删除内容。

本机草稿采用逐文件临时写入、fsync 和重命名，独立于可清理的 Room/目录缓存。账号或服务切换会取消当前云端操作、清空私有界面，并拒绝晚到结果写入新身份的草稿。本次覆盖创意模块，不能据此宣告 R1-03 的全部 Room、偏好和其他业务缓存问题已修复。卸载应用、清除应用数据不属于草稿保留范围。

## 服务端接口与数据

作者接口统一要求有效正式账号的服务端会话；不接受游客、停用账号或静态开发 API Token 代替作者身份。服务器决定 owner、origin、审核与展示状态，作者请求无法指定这些字段。访问其他作者的私有 ID 返回 404。作者接口使用 `private, no-store`，私有素材按上传者校验；公开 DTO 不包含管理备注。

| 接口 | 行为 |
| --- | --- |
| `GET /api/me/creative/identity` | 校验当前账号，返回作者 ID、分类和是否接受新投稿 |
| `GET/POST /api/me/creative/items` | 我的条目 / 幂等创建私有草稿 |
| `GET /api/me/creative/items/{id}` | 当前私有内容、版本与作者可见意见 |
| `PUT /api/me/creative/items/{id}/draft` | 使用 `expected_version` 保存草稿；提交快照不能原地覆盖 |
| `POST /api/me/creative/items/{id}/submit` | 校验版本、revision、content hash 和 `sharing_confirmed=true` 后提交 |
| `POST /api/me/creative/items/{id}/withdraw` | 撤回待审版本 |
| `POST /api/me/creative/items/{id}/unpublish` | 作者主动撤下公开作品 |
| `DELETE /api/me/creative/items/{id}?expected_version=…` | 删除从未发布的稿件，保留幂等墓碑与审核/审计 |
| `POST /api/me/creative/uploads` | 用客户端 ID、SHA-256、字节数创建或恢复封面会话 |
| `GET/DELETE /api/me/creative/uploads/{id}` | 查询或取消会话；已有版本引用的素材不可删除 |
| `PUT /api/me/creative/uploads/{id}/content` | 接收有界二进制，检查长度/摘要，重编码后原子登记素材 |
| `GET /api/me/creative/assets/{id}` | 读取作者自己的封面，禁止私有缓存 |
| `GET /api/me/creative/notifications?after=…` | 按事件 ID 增量读取，单页最多 50 条 |
| `POST /api/me/creative/notifications/read` | 只标记当前作者指定 ID 以前的消息已读 |
| `GET /api/admin/creative/reviews` | 待审队列，每页 40 条 |
| `GET /api/admin/creative/reviews/{id}?revision_id=…` | 指定冻结版本、当前线上内容及管理审核历史 |
| `POST /api/admin/creative/reviews/{id}` | `claim/approve/changes_requested/reject`，检查版本与内容摘要 |
| `GET/PUT/DELETE /api/me/creative/favorites[/{id}]` | 获取可见收藏 ID，幂等收藏或取消；最多 500 项 |
| `POST /api/creative/items/{id}/reports` | 正式账号举报当前公开版本；请求私有且可幂等重试 |
| `GET /api/me/creative/reports` | 举报人查看原因、状态和公开处理结果，不返回举报正文、举报人标识或内部备注 |
| `GET /api/admin/creative/reports[/{id}]` | 分页队列和举报详情，含当前创意版本与重复数量 |
| `POST /api/admin/creative/reports/{id}` | 开始/完成/驳回；可在双版本检查后处理并下架 |
| `GET /api/admin/creative/metrics` | 上架、社区上架、待审、待处理举报、收藏、24 小时投稿量 |

在 C1 SQLite 上附加 `creative_submission_keys`、`creative_reviews`、`creative_notifications`、`creative_uploads`、`creative_favorites`、`creative_reports`、`creative_report_keys` 和查询索引，并给 `creative_assets` 增加可空作者字段。既有官方素材保持兼容，原有任务表不重建。批准、发布指针、举报下架、目录 generation、审核/举报结果、通知与审计在同一创意数据库事务中写入。账号库仍是独立 SQLite；本次未迁移 PostgreSQL、多实例或对象存储。

后台继续使用现有独立管理员 Token，审计记录凭据摘要标识；它不等于独立的多人管理员身份或角色系统。

## 本批限制与开关

| 项目 | 当前实现 |
| --- | --- |
| 标题 / 摘要 / 正文 | 80 / 300 / 10,000 字符 |
| 源码 | 30 个文本文件；单个 256 KiB，总计 1 MiB；Kotlin、Java、XML、txt、md |
| 封面 | 每版本一张；JPEG/PNG/WebP，原文件最多 1.5 MiB、16 MP；服务端去元数据并重编码为最长边 1200 的 JPEG |
| 上传 | 每账号最多 2 个未完成会话，未完成会话 24 小时过期；完成后重复请求复用素材，不重复占额 |
| 投稿 | 滚动 24 小时最多 5 次真实提交，同时最多 3 项待审；同一次提交重试不重复计数 |
| 云端存储 | 每账号 50 项创意、100 MiB 源码版本内容；每项最多 30 个版本；封面会话总计 100 个或原始字节预留 50 MiB |
| 本机存储 | 每账号/服务 50 个草稿；封面 100 个或 50 MiB；限制失败保留原稿 |
| 收藏 | 每个正式账号最多 500 项；重复收藏不重复占额，下架内容不出现在可见收藏集合 |
| 举报 | 说明 5–2,000 字符；每账号滚动 24 小时最多 10 条新举报；同一客户端请求重试不重复计数 |

核心源码、图片、待审、上传、收藏和举报限额通过 capability 返回；Android 本批还保留相同的保守本地校验，并未实现所有限制的动态协商。此次选择单封面、整文件重试；方案中的多截图、5 MiB 图片、分块续传及通用素材治理仍待推进。源码通过有界 JSON 同步，不接收完整工程压缩包、APK、动态插件或远程 Git 自动导入。上传完成不代表构建通过，基础秘密规则未命中也不构成无秘密的证明。

配置示例（在已有服务配置中设置，管理员凭据通过安全的现有配置渠道提供）：

```yaml
admin_ui_enabled: true
creative_catalog_enabled: true
creative_submissions_enabled: true
```

管理员凭据仍要求至少 24 位，推荐使用部署原有环境变量 `AGENT_ADMIN_TOKEN`；不要使用普通用户 Token。也可设置 `AGENT_CREATIVE_SUBMISSIONS_ENABLED=true`。设置变化后重启服务，访问 `/api/creative/capabilities` 确认 `submissions_enabled=true`；启用新投稿还要求管理员后台及凭据已配置。

默认 `creative_submissions_enabled=false` 只阻止新提交审核，保留本机/云端草稿、上传、撤回、已有审核、消息和公开内容。Android 显示暂停原因并停用提交按钮，后台继续显示队列。`creative_catalog_enabled=false` 则关闭整个创意 API。本次没有替用户开启生产投稿。

## 备份与恢复

新增 `scripts/backup_creative.py`：使用 SQLite backup API 导出包括已提交 WAL 内容的 `agent.db`、`users.db`，复制数据库索引中全部不可变封面，记录 SHA-256 清单，验证数据库完整性、素材摘要，以及作者、收藏者和举报人的账号关联。数据库并非同一个跨库事务；账号变更应暂停后再建立业务恢复点，遇到缺失/变化素材或账号关联不完整会失败，不生成成功备份。

在项目根目录使用项目 Python 3.12 环境执行以下示例；将路径替换成部署的实际数据目录和**尚不存在**的输出目录：

```bash
AGENT_DATA_DIR=/srv/android-agent/data python scripts/backup_creative.py create \
  --output /srv/backups/creative-2026-09-09
python scripts/backup_creative.py restore \
  --input /srv/backups/creative-2026-09-09 \
  --output /srv/restore-check/creative-2026-09-09
```

自定义数据库位置可用 `--database` 和 `--users-database` 明确指定。恢复只允许写入新目录，并在复制前后验证摘要；已有目标拒绝覆盖。恢复目录含私有源码、账号库和审核备注，应按私有业务备份管理。先在隔离服务中核对作者登录、私有草稿、审核历史、公开目录与封面，再按部署切换流程操作；本次没有执行实际数据切换。

此工具虽然复制完整 `agent.db`，但不包含工作区、checkpoint blob、APK 或运行凭据，**不是整个 Agent 的完整恢复方案**。原 `backup_data.sh` 的全量部署恢复问题以及统一计划 R1-09 仍待单独修复。此次恢复演练只使用临时测试账号和数据。

## 验证与交付物

环境：macOS ARM64；Python 3.12.13 / pytest 9.1.1 / Pillow 12.3.0；Node 22.17.0；Android Studio JBR 17，使用隔离的 Gradle 项目缓存和构建目录，未清理并行工作缓存。

| 验证 | 本次结果 |
| --- | --- |
| Python：creative_community、creative_catalog、accounts_api、api_contract | 68 项通过；覆盖越权、不可变版本、并发审批、上传/投稿/收藏/举报额度、举报双版本下架与合并处理、删除墓碑、灰度暂停、包含收藏举报的 WAL 备份恢复、篡改拒绝和 C1 素材迁移 |
| Android `testDebugUnitTest assembleDebug` | 143 项 JVM 测试通过，0 失败/错误/跳过，Debug APK 构建成功；覆盖持久化重开、迁移、身份隔离、取消晚到请求、无凭据重定向、上传重试、共享作者 DTO、删除/封面引用，以及收藏举报的鉴权路径 |
| `node desktop/tests/creative-community-live.test.js` | 真实临时服务：作者 A 上传并投稿 → 实际浏览器审核 → 作者 B 公开读取、收藏和举报 → 浏览器后台处理并下架；私有备注隔离、v1/v2 隔离、退修重投、撤回后的旧审批失败全部通过；同时回归 C1 官方管理及 500 项草稿导入 |
| `node desktop/tests/studio-ui.test.js` | 安全文本预览、保存/发布保护、暂停提示与队列、举报纯文本渲染及处理、基础指标、响应布局、重试和退出通过（界面 fixture） |
| API 契约 | 实际新增行为验证后更新快照并检查通过，共 163 条方法/路径；作者 DTO 同时由 Python 与 Android 解析验证 |
| 桌面语法与种子 | `npm run check` 与 `scripts/export_creative_seed.py --check` 通过；500 项种子一致，`git diff --check` 无空白错误 |

本机证据在 [.artifacts/creative-c2](../.artifacts/creative-c2)，本次续作摘要见 [verification-governance.json](../.artifacts/creative-c2/verification-governance.json)，并包含审核/举报后台截图和 [Debug APK](../.artifacts/creative-c2/android-agent-c2-governance-debug.apk)。这些忽略目录中的证据不保证随 Git 检出存在。APK 包含当前工作区并行改动，不能称为只含 C2 的独立发布包。后台截图已检查桌面审核、举报处理和窄屏队列；未执行 Android 真机/模拟器安装、仪器测试、真实模型/社区源码构建、Linux 部署隔离或完整发布门禁。

## 下一步仍需推进

- C2 补齐：标签别名/分类合并、多截图、统一素材保留回收及账号注销策略；更完整的运营趋势/转化指标、后台推送和离线自动同步；手机端收藏集合页、云端封面重新下载预览及新增参考链接表单。当前参考链接从云端读取时保留，可移除，不会因同步静默丢失。
- 真机验收：账号/服务切换、旋转与进程重启、弱网响应丢失、源码/项目文件选择、封面上传、退修后修改、另一台设备浏览。当前 Kotlin/JVM 测试与浏览器真实后端测试不能替代该项。
- R1 继续完成全局账号缓存、工程能力识别、执行隔离与全量备份恢复；本次只补创意域所需边界。
- C3 在版本化文件接口、幂等任务与隔离构建条件满足后，接入固定审核版本的应用任务、构建证据和对应 APK。现在 `applications_enabled=false`，不会将社区源码直接接入旧 ask 链路。

本记录将 CG-01 的社区状态、CG-04 的单封面上传、CG-05 的审核、CG-07 的核心创作、CG-08 的收藏/举报/消息/基础统计，以及 CG-11 的备份/灰度首版标为已有实现，不将整个 C2、CG 或项目升级计划标为完成。
