# 创意广场平台：C1 实施与验收记录

日期：2026-09-09。基准提交：`5ba10bf`，代码来自带并行修改的工作区，尚未提交或部署。

后续进展：C2 核心投稿与审核已继续实现，当前行为与验收见 [C2 实施记录](CREATIVE_SQUARE_C2_IMPLEMENTATION_STATUS.md)。本文保留 C1 交付时的范围、计数和下一批计划，不作为当前能力清单。

本轮交付“后台管理官方创意 → 服务端发布目录 → Android 远程浏览”的第一批代码。用户投稿、审核消息和版本化应用属于 C2/C3，仍未开放。统一范围见 [统一升级计划](UNIFIED_UPGRADE_PLAN.md)，完整设计见 [创意平台升级方案](CREATIVE_SQUARE_PLATFORM_UPGRADE.md)。

## 本轮范围

| 工作单 | 本轮状态 | 已实现与后续边界 |
| --- | --- | --- |
| R0-01 | 建立本轮核验记录 | 核对并保留并行安全、样式与界面修改；验证以文件摘要及本地日志为准，不能只用 HEAD 标识本次工作区 |
| R0-02 | 部分完成 | 明确 Python 3.12，补测试依赖入口；移除两个 Gradle 配置中的宿主机 JDK 路径；完成本机 Android 构建。Linux 容器新建项目构建、完整 CI 与部署恢复仍待验收 |
| CG-01 | C1 基础完成 | CreativeStore、独立版本、公开 DTO、后台鉴权、乐观锁、审计；社区作者权限与提交审核状态转换尚未实现 |
| CG-02 | C1 完成 | 导出当前 500 项种子；稳定 legacy ID、源码摘要与本地预览映射；重复导入不覆盖编辑、不恢复下架 |
| CG-03 | C1 完成 | 公开目录、详情、分类、搜索、分页、ETag、capability。首批采用有界 SQLite 查询；未实现 FTS 索引和大规模目录性能验收 |
| CG-04 | 管理员封面首版 | 有界上传、图片解码重编码、私有读取与公开引用检查；用户上传会话、额度、断点恢复、回收仍在 C2 |
| CG-05 | 官方条目管理完成 | 新建/编辑、版本发布与回滚、上下架/归档、推荐排序、分类维护、审计。社区审核 Diff、退修/拒绝、标签别名与分类合并未实现 |
| CG-06 | 代码与 JVM 验收完成 | Android 远程 Repository/ViewModel、搜索分类分页、详情、预览、公共缓存；真实 Android 设备连接服务的验收尚未执行 |
| CG-07/08 | 未开始 | 用户投稿、持久草稿、我的创意、审核消息、举报与收藏 |
| CG-09/10/11 | 未开放完整能力 | 受管应用、隔离验证、结果统计、素材恢复与治理策略仍待实施 |
| CG-12 | 官方管理链路已验证 | 浏览器连接真实临时服务通过；跨用户投稿 → 审核 → 应用 → APK 的完整链路尚未具备 |

R1 原有作用域、配置快照、上下文和备份工作不因 C1 完成而视为通过。本轮新增公共目录缓存也不等同于修复整个 Android 私有缓存体系。

## 已接通的行为

1. 管理员在 `/admin/` 的“创意目录”创建官方条目，填写源码、分类、标签、适用 UI/SDK、依赖、接入说明、来源/许可证及参考链接，上传或移除封面。
2. 保存只产生草稿。发布时明确选择一个已保存版本；界面阻止直接发布尚未保存的表单内容。
3. 已发布 v1 后编辑形成 v2 草稿，v1 继续对外可见。发布 v2 后切换公开版本；也可选择历史版本重新发布实现回滚。历史内容不直接覆写。
4. 上下架、发布和编辑共享 `row_version` 冲突检查。旧编辑器不能覆盖新的下架操作。下架/归档需填写原因，恢复只改为“未发布”，再次显式发布后才回到广场。
5. 推荐优先；同层级中展示排序数值较大的靠前，然后按发布时间与稳定 ID 排序。分类排序数值较小的靠前。分类停用会从筛选列表消失并阻止新的发布，但不自动下架该分类已有内容；需撤销传播时使用条目下架。
6. “导入内置创意”默认导入 500 项草稿，可选择同时上架本次新增项。重复导入跳过已有 legacy ID。启动服务不会自动灌入或自动发布种子。中断后可重试导入并在后台检查已有草稿；不会为补齐发布而自动改动已有条目。
7. Android 默认读取远程目录；成功的空目录替换旧缓存。离线内容带状态提示，已取消的慢请求不能覆盖较新缓存。内置 500 项示例通过独立入口保留，不作为线上条目的兜底。
8. 本地预览只在 native preview ID 和源码 SHA-256 均匹配时使用；不匹配则展示封面/占位，不执行服务端 Kotlin 或 HTML。详情读取当前公开版本并提供源码复制；本轮不声称任何条目已经构建验证。

目录下架会阻止后续公开读取；它无法撤回已经下载、缓存或复制的内容。离线目录不提供远程详情源码的缓存读取，一键应用也未开放。

## 接口与存储

| 接口 | 用途 |
| --- | --- |
| `GET /api/creative/capabilities` | `schema_version=1`；目录可用；`submissions_enabled=false`、`applications_enabled=false` |
| `GET /api/creative/categories` | 动态分类 |
| `GET /api/creative/items` | q/category/origin/cursor/limit；返回卡片、分类、目录 generation 与下一页游标，不传整份源码 |
| `GET /api/creative/items/{id}` | 当前公开版本、manifest 与源码 |
| `GET /api/creative/assets/{id}` | 仅当前已上架版本所引用的封面；共享素材仍被其他上架条目引用时继续公开 |
| `GET/POST /api/admin/creative/items` | 后台查询、新建官方草稿 |
| `GET /api/admin/creative/items/{id}` | 可通过 revision_id 查看历史版本 |
| `PUT /api/admin/creative/items/{id}/draft` | 带 expected_version 保存草稿 |
| `POST /api/admin/creative/items/{id}/publish` | 发布选定 revision_id |
| `POST /api/admin/creative/items/{id}/visibility/{action}` | unpublish / restore / archive |
| `PATCH /api/admin/creative/items/{id}/placement` | 推荐与排序 |
| `GET/PUT /api/admin/creative/categories` | 查看/维护分类，更新检查版本 |
| `GET /api/admin/creative/audit` | 查询审计，可按 item_id 过滤 |
| `POST /api/admin/creative/covers`、`GET /api/admin/creative/assets/{id}` | 封面上传和私有预览 |
| `POST /api/admin/creative/import-builtins` | `{ "publish": false }`，显式执行种子导入 |

新增表位于 TaskStore 的同一个 SQLite 文件中，默认是数据目录下的 `agent.db`：`creatives`、`creative_revisions`、`creative_categories`、`creative_assets`、`creative_audit`、`creative_meta`。封面存放在该数据库旁的 `creative-assets/`。初始化采用附加建表；未删除原有项目/任务表。

公开素材每次检查当前引用；未发布素材只允许管理员读取。上传上限 1.5 MiB、1600 万像素，限 JPEG/PNG/WebP，解码后重编码为最长边不超过 1200 的 JPEG。源码最多 30 文件、单文件 256 KiB、总计 1 MiB；拒绝越界/隐藏路径、Windows 盘符与非白名单后缀。不支持 zip、APK/dex/jar 或 SVG 上传。

后台沿用现有管理员 Token 鉴权。审计中的操作者为凭据摘要标识，不是独立的人类管理员账号；原始 Token 不进入日志。当前尚无社区作者上传权限、秘密扫描、内容审核管线或素材垃圾回收，不能将此首版后台能力直接当作开放 UGC 平台。

## 验证证据

环境：macOS ARM64；Python 3.12.13、pytest 9.1.1、Pillow 12.3.0；Node 22.17.0；JDK 17.0.9；项目既有 Android SDK/Kotlin/KSP 版本。运行时依赖兼容性检查通过。测试不调用真实模型，不连接生产数据库。

| 验证 | 结果 |
| --- | --- |
| `pytest tests/test_creative_catalog.py tests/test_accounts_api.py tests/test_api_contract.py -q` | 47 项通过；种子更新后创意专项再次 23 项通过 |
| Android `testDebugUnitTest assembleDebug` | 130 项 JVM 测试通过，Debug APK 构建成功；包含跨请求取消、空目录缓存、服务隔离、共享 DTO 和全部 500 项源码摘要核对 |
| `node desktop/tests/creative-live.test.js` | 真实后台与临时 API：新建、封面、草稿隔离、发布、回滚、推荐、下架、显式恢复、审计、500 项草稿导入、移动布局及退出通过 |
| `node desktop/tests/studio-ui.test.js` | 后台安全文本预览、保存与发布保护、功能状态、响应布局、重试、退出通过（界面 fixture 测试） |
| `npm run check`（desktop） | JavaScript 语法检查通过 |
| `scripts/check_api_contract.py` | 完整快照与共享样例一致，135 条路由；公开目录和关闭的后台不再被错误地当作普通账号接口验收 |
| `scripts/export_creative_seed.py --check` | 当前 500 项导出一致；还需 JVM 摘要测试共同核对运行时代码生成规则 |
| `git diff --check` | 无空白错误 |

后台截图和验证文件位于本机 [.artifacts/creative-c1](../.artifacts/creative-c1)。`verification.json` 保存验证时的文件摘要；这些本地证据不保证随 Git 检出存在。构建包含当前工作区中其他正在进行的改动，不应把它当成仅含 C1 的独立发布包。

首次普通 Android 构建遇到共享 KSP 缓存异常，随后改为独立 `/private/tmp` 构建和项目缓存目录，未清理其他任务缓存。源码摘要回归发现并行更新的辅助函数命名规则，已同步导出器并通过全量摘要核对。缺陷修复和测试失败均未通过放宽断言消除。

`release_check.py` 已加入种子一致性、后台界面及真实后台流程检查，并使用其自身 Python 启动临时服务。此次未执行完整发布门禁，未执行真机安装/Android 仪器测试、真实模型构建、Linux 隔离验收、生产迁移或部署；不标记为正式发布通过。

## 本地使用与下一批入口

先按 README 安装 Python 3.12 及 hash 锁定依赖。启用现有 `admin_ui_enabled`，通过环境变量 `AGENT_ADMIN_TOKEN` 或本地配置提供满足服务端要求的管理员凭据；保留 `creative_catalog_enabled: true`。启动后进入 `/admin/`，导入草稿并按需要发布。Android 与服务端必须使用对应版本；旧服务不支持目录时客户端显示说明，可进入内置示例。

数据升级前应备份数据库及相邻 `creative-assets/`，在隔离环境恢复验证。在线 SQLite 不能仅复制主文件来代替一致性备份。此次没有在生产数据上执行导入或升级，也未完成新增素材与整体备份系统的恢复演练。

下一批 C2 按以下顺序实施：

1. 先验收 R1-03 的账号/服务缓存边界和备份恢复，建立与可删除缓存分开的持久草稿存储。
2. 实现账号限定的草稿、上传会话和素材绑定权限；用户只能编辑自己的稿件，服务端决定 owner/origin。
3. 接通 Android 发布向导、文件选择、公开清单确认、我的创意、上传恢复和草稿升级迁移。
4. 接通后台提交快照、审核 Diff、退修/拒绝、批准发布、私有意见和审核消息；v2 待审时保持 v1 公开。
5. 补账号 A 投稿 → 管理员审核 → 账号 B 浏览的真实回归，再开放投稿 capability。收藏、举报及统计按 CG-08/11 接入。
6. C3 满足工程能力识别、隔离、版本、幂等与构建证据条件后，才接入受管应用和对应 APK；不把远程内容拼进旧 ask 接口来代替该链路。
