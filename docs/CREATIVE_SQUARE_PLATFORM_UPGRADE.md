创意广场平台升级方案：后台管理、用户投稿与版本化应用

实施进度（2026-09-11）：C1 目录管理及 C2 投稿/审核、收藏、举报处理和基础运营统计已有本地实现。实际接口、首批额度、开关语义、验证与剩余范围见 [C2 实施记录](CREATIVE_SQUARE_C2_IMPLEMENTATION_STATUS.md)。下文继续保留完整目标；例如多截图、标签合并、完整素材/账号治理和 C3 仍未交付，不将方案草案视为已实现能力。

更新日期：2026-09-09。所属总计划：[UNIFIED_UPGRADE_PLAN.md](/Users/mac/Android-Agent/docs/UNIFIED_UPGRADE_PLAN.md)。本方案对应新增 CG-01–CG-12 工作单，补充 R5-01，不重复建设现有 Recipe、任务和快照能力。

用户目标：Android 创意广场中的每一项创意都能在管理后台管理；用户可以上传自己的创意，审核发布后供其他用户浏览和应用。本文最初作为详细设计；当前代码已分批实现其中 C1/C2 能力，实际状态以实施记录和验证证据为准，未完成项继续作为后续目标。

**产品定位与首版默认选择**

把当前内置演示目录升级为同一服务端内共享的创意目录，连接官方内容、社区投稿、后台运营和项目应用。不同 server origin 的目录与账号相互独立，首版不建立跨部署聚合市场。

首版采用“作者投稿 → 自动基础检查 → 管理员审核 → 发布”。注册且有效的普通账号可以创作和投稿；游客按服务端配置浏览公开内容，投稿和应用要求正式账号及对应权限。邮箱验证沿用部署设置，不新增一套账号门槛。

首版投稿支持组件、动效、页面、布局/风格等 Recipe：说明、分类标签、封面/截图、Kotlin/Java/XML 源码文件、兼容条件、依赖与接入说明。用户可粘贴代码、选取文本文件，或从自己的项目选择文件生成草稿。完整 Gradle 工程压缩包、用户 APK、远程 Git 自动导入和动态代码插件留到后续专项；这些内容不应与普通 Recipe 使用同一上传和预览路径。

首版必须同时交付后台管理和普通用户投稿，两者是本需求的核心范围。收藏、作者页、消息通知可作为同次迭代的小模块；评论社区、付费交易、创作者收益和推荐算法不作为首版前置。

**当前实现与改造位置**

| 当前事实 | 代码依据 | 改造方向 |
| --- | --- | --- |
| 广场从 APK 内置列表读取；基准目录有 6 个 Recipe | [CreativeCatalog.kt](/Users/mac/Android-Agent/android-app/app/src/main/java/com/androidagent/client/creative/CreativeCatalog.kt)、[CreativeSquareFragment.kt](/Users/mac/Android-Agent/android-app/app/src/main/java/com/androidagent/client/CreativeSquareFragment.kt) | 服务端目录成为事实源；内置条目迁为官方种子；保留可识别的离线示例 |
| 分类与预览由 enum 和固定 Compose 函数决定 | [CreativeRecipe.kt](/Users/mac/Android-Agent/android-app/app/src/main/java/com/androidagent/client/creative/CreativeRecipe.kt)、[CreativeSquareScreen.kt](/Users/mac/Android-Agent/android-app/app/src/main/java/com/androidagent/client/creative/CreativeSquareScreen.kt) | 分类动态化；图片预览通用化；受支持的内置渲染器保留；未知类型有图片/占位降级 |
| Recipe 可以包含 sourceBuilder、style/pattern 等本地对象 | [CreativeRecipe.kt](/Users/mac/Android-Agent/android-app/app/src/main/java/com/androidagent/client/creative/CreativeRecipe.kt)；styles 正有并行修改 | 网络 DTO 使用纯数据；导出时生成源码快照，不能序列化 Kotlin lambda 或远程下载函数 |
| 应用流程由客户端创建会话，再拼源码 prompt 调 ask | [CreativeSquareFragment.kt](/Users/mac/Android-Agent/android-app/app/src/main/java/com/androidagent/client/CreativeSquareFragment.kt) | 服务端按创意版本生成结构化应用请求，绑定内容摘要、项目版本、任务与产物 |
| 后台目前只有账号与 Token 页面，管理员使用独立静态 Token | [admin/index.html](/Users/mac/Android-Agent/agent/admin/index.html)、[admin.js](/Users/mac/Android-Agent/agent/admin/admin.js)、[api.py](/Users/mac/Android-Agent/agent/api.py) | 扩展现有后台导航和权限入口；首版记录管理员凭据标识，多运营人员时增加独立身份和角色 |
| 有本地/对象 ArtifactStore，但现有接口主要整块读写，业务接线仍需验收 | [artifacts.py](/Users/mac/Android-Agent/agent/stores/artifacts.py) | 增加创意素材服务、流式限额、私有读取与删除生命周期，不直接向用户暴露存储 key |
| 普通 API 请求体默认限额 2 MiB；近期移除了 multipart 依赖 | [config.py](/Users/mac/Android-Agent/agent/config.py)、[api.py](/Users/mac/Android-Agent/agent/api.py)、[requirements.txt](/Users/mac/Android-Agent/requirements.txt) | 使用上传会话与逐文件二进制上传，或明确引入 multipart；按上传路由限额，不全局放大普通 JSON 限额 |

本轮核对时创意样式、账号和服务端仍有其他未提交修改，实施前需重新确认实际目录数量和已有行为。现有源码与提示词测试只能验证内置条目，不能替代远程目录、投稿和审核测试。

**用户端流程**

| 页面 | 主要内容和动作 | 必须清楚的状态 |
| --- | --- | --- |
| 创意广场 | 推荐、最新、分类、标签、搜索；官方/社区来源；封面、作者、兼容性和验证标签 | 加载、空结果、失败重试、离线缓存；服务端下架和客户端不支持分别说明 |
| 创意详情 | 图片、可选内置互动预览、说明、文件列表与代码、依赖、适用工程、作者与来源、版本记录 | 当前发布版本；内容已审核与实际编译验证分别显示；已过期或已撤回不能继续应用 |
| 发布创意 | 选择类型 → 填说明与分类 → 选源码文件 → 上传封面/截图 → 填兼容和接入 → 预览提交 | 自动保存草稿；上传进度、失败重试；选择项目文件后显示完整待分享清单；最终提交是明确动作 |
| 我的创意 | 草稿、审核中、需修改、已发布、已撤回/下架；继续编辑、提交、撤回、创建新版本 | 当前线上版本与待审核版本并列；审核意见仅作者和管理人员可见 |
| 应用到项目 | 选项目 → 检查工程能力 → 显示必要依赖/接入影响 → 发起任务 → Review/构建/安装 | 使用的创意版本与项目 revision；不重复创建任务；不兼容时给出明确取舍或拒绝原因 |
| 消息与反馈 | 审核通过/退回/下架通知、我的举报处理结果 | 基于持久通知和已读状态；应用关闭时不承诺即时推送 |

首版可将发布和“我的创意”放在广场顶部动作与账号页入口，详情使用独立页面承载长代码、多截图和版本信息；保留当前 Compose 风格，不要求重写整个 Android 导航。

上传前保留本地草稿；WorkManager 只恢复已由用户发起的上传或提交操作，不因后台重试自行发布。账号/服务地址变化后不能继续以新账号身份上传旧草稿。

**管理后台：每项创意都可管理**

| 后台区域 | 首版能力 | 约束与记录 |
| --- | --- | --- |
| 创意列表 | 搜索标题/作者/ID；按官方/社区、分类、审核和上下架状态筛选；查看当前/待审版本 | 分页，批量操作返回逐项结果；每项记录操作人和原因 |
| 创意编辑 | 新建官方创意；编辑标题、说明、代码、兼容性、依赖、媒体；查看手机样式预览 | 修改发布内容产生新版本；管理人员修改社区稿件保留原作者与修改者，不静默归为官方 |
| 审核队列 | 查文件、图片、自动检查、前后版本 Diff；批准发布、退回修改、拒绝 | 审核绑定 revision ID 和内容 hash；原因结构化且可读；作者提交后内容冻结 |
| 分类与标签 | 新增、重命名、排序、停用、合并分类；维护标签别名 | 使用稳定 ID；有内容的分类不能直接删除成为孤儿；未知分类客户端可展示 |
| 展示运营 | 推荐、置顶、精选、排序；查看推荐位预览 | 运营配置与源码版本分离并审计；精选不等于官方作者或已通过构建；只有当前可见条目才能出现在推荐位 |
| 上下架与版本 | 下架、作者撤回状态查看、恢复、归档、切回已审核旧版本 | 服务端立即阻止新的受管应用；不撤销已安装 APK 或已复制代码；被撤销的版本不能回滚重新发布 |
| 举报处理 | 按原因查看、合并重复举报、限制传播、退回作者、关闭处理记录 | 举报人身份和联系方式不公开；不依赖举报数量自动永久删除 |
| 内容统计 | 浏览、收藏、应用请求、任务启动、构建成功、失败原因、审核积压 | 用稳定事件去重；“应用点击”不能记作“构建成功” |
| 操作日志 | 查询编辑、审核、上下架、分类、推荐、账号关联处理 | 保留对象版本、actor、前后状态摘要、原因与 request ID；不记录 Token 或秘密源码片段 |

后台沿用现有 HTML/CSS/JS 外壳，拆出 gallery、editor、review、taxonomy 等模块，避免所有逻辑继续堆进 admin.js。内容用 textContent 或明确允许的 Markdown 子集渲染，不把投稿中的 HTML/脚本作为后台界面执行。

首版允许单管理员使用当前认证方式，但审计只能声称识别了管理员凭据，不能声称识别具体自然人。需要多个审核员时，增加 moderator、editor、admin 的独立账号/凭据和服务端权限映射；不让内容审核员自动拥有账号密码重置权限。

**权限边界**

| 主体 | 可读 | 可写 |
| --- | --- | --- |
| 游客/未登录者 | 服务端允许公开的已发布目录、详情及其公开素材 | 无投稿、审核或应用权限；公开浏览可关闭 |
| 普通用户 | 公开目录、自己的全部稿件/素材/审核意见、自己项目内的应用结果 | 自己的草稿、提交/撤回、新版本、收藏/举报、应用到有权限的项目 |
| 审核员/编辑 | 获授范围内的稿件与检查结果 | 获授的审核、编辑或运营动作；不能获取账号秘密凭据 |
| 管理员 | 管理所需全部创意和审计记录 | 新建官方条目、上下架、分类运营和角色管理；内容更改仍走版本与审计 |
| 验证 Worker | 被分配的不可变稿件快照与临时沙箱 | 该次验证结果和产物；不访问作者其他项目或服务端凭据 |

owner、origin=official、审核结论、推荐状态和验证标签由服务端确定，不能从普通用户 PATCH 中批量赋值。作者 ID 不等同于用户可填的署名文本；公开资料仅返回允许展示的作者名/头像，不返回邮箱、会话、项目路径或审核内部备注。

**内容版本、审核和展示是三个独立状态**

创意实体保存稳定 creative_id 和 published_revision_id。每个提交版本有独立 revision_id、序号、manifest、文件/素材摘要和内容 hash；提交后的版本内容不可原地修改。

| 状态维度 | 建议值 | 说明 |
| --- | --- | --- |
| 稿件审核 | draft、submitted、in_review、changes_requested、approved、rejected、withdrawn | 退回后从被退回版本生成新草稿；审核人始终审特定内容快照 |
| 展示状态 | unpublished、listed、author_hidden、admin_blocked、archived | 公共查询只返回 listed 且公开版本有效的条目；管理员阻断优先于作者发布请求 |
| 验证结果 | 未验证、基础检查通过、指定环境构建通过、指定设备运行通过等独立证据 | 审核通过不自动获得构建或运行标签；每项结果绑定版本和环境 |

典型转换：

1. 初次投稿：draft → submitted → in_review → approved；管理员可在一次事务中批准并发布，设置公开版本、增加目录 generation，并写审计与通知。
2. 更新投稿：v1 继续公开，作者从 v1 新建 v2 草稿；v2 审核通过后原子切换公开版本。v2 被退回/拒绝不会影响 v1。
3. 作者撤回待审版本只影响该版本；撤下已发布创意改变展示状态，两个按钮必须区分。
4. 管理员下架改变展示状态和 generation；并发的批准请求必须检查实体 revision/状态前置条件，不能把刚下架的内容重新上架。
5. 恢复或回滚必须显式选择仍被批准且未被撤销的版本，记录原因。admin_blocked 的解除属于管理员动作，作者不能通过新建版本绕过。

状态变化使用 expected_revision/If-Match；冲突返回 409，不采用最后写入覆盖。提交、审核、发布、上传完成、应用接口使用幂等键，重复请求返回原结果。

已经开始的应用绑定旧版本快照，不随作者发布新版本变化。普通版本更新不取消旧任务；发现有害内容并紧急阻断时，未开始任务拒绝执行，运行中任务在安全检查点停止并告知原因，保留已有改动与恢复入口，不自动覆盖用户工程。

**数据与存储设计**

首版沿用 SQLite，新增 CreativeStore/领域服务；创意、应用登记和任务表尽量位于现有 agent.db 的可共用事务边界中，不为这个功能强制引入 PostgreSQL。账号仍从 UserStore 校验，不跨独立 users.db 假装存在数据库外键。

| 表/集合 | 主要字段与约束 |
| --- | --- |
| creatives | id、owner_user_id、origin、slug、published_revision_id、distribution_state、row_version、created_at/updated_at；旧内置 ID 可作为稳定 slug/legacy_id |
| creative_revisions | id、creative_id、version_no、review_state、schema_version、title/summary、manifest_json、content_hash、created_by、submitted_at；同条目版本号唯一，提交后内容冻结 |
| creative_assets / revision_assets | 上传者、所属条目/草稿、storage_key、purpose、size、MIME、digest、dimensions、scan_state、lifecycle；引用关系绑定 revision，不靠猜路径授权 |
| creative_taxonomy / creative_labels | 稳定分类/标签 ID、名称、别名、排序、启用状态及关联；保留停用/合并映射 |
| creative_reviews | revision_id、reviewer_actor、decision、reason_code、作者可见意见、私有备注、检查报告引用、时间 |
| creative_placements | creative_id、推荐位、排序、启用状态、row_version；查询时仍联结当前发布状态 |
| creative_validation_runs | revision_id、content_hash、工具链/设备、任务状态、构建/运行证据、产物和过期原因；不能由作者写“通过” |
| creative_applications | requester、project/conversation/task/turn、creative_id/revision_id/hash、base_revision、结果 revision/APK、idempotency_key；保留可恢复启动状态 |
| creative_reports / creative_audit | 举报原因与处理；append-only 操作记录和 request ID；公开字段与管理字段分离 |
| creative_notifications / creative_favorites | 审核/下架消息与已读状态；用户收藏唯一键；通知事件 key 防重复 |

为公开状态+分类+排序、owner+更新时间、审核状态+提交时间和创意版本建立索引；标题/说明/标签使用 SQLite FTS 与中文查询策略，暂不建设向量库。分页采用稳定游标和明确排序，返回 next_cursor、catalog_generation 和 server time；不要把全部源码随列表下载。

审核、发布、审计与通知记录在同一事务内落库。外部素材与 DB 无法跨系统原子提交时，采用临时态→校验→封存→可引用的流程；中断后有重试和孤儿回收。未来队列或对象存储迁移复用 R8，首版不依赖尚未接通的 outbox dispatcher。

建议存储命名空间由服务端生成，分 staging、sealed/private、published/readable 语义；即使物理 blob 去重，读权限仍由引用关系判断，不能因为 hash 相同就让另一个用户访问私有稿件。资产只有被当前公开版本引用且条目可见时才可通过公开入口读取。

旧版本供已登记的应用按该任务权限读取封存快照，作者和管理员也可按权限查看；这不扩大为公开素材访问。首版公共历史仅展示曾公开的版本号和变更摘要，不暴露未发布代码或审核意见。素材读取先走服务端授权路由；公开缓存需重新验证，私有响应禁止共享缓存。以后增加对象存储短期票据或 CDN 时，必须定义失效时间和下架清缓存流程，不能承诺已经发出的永久 URL 可以立即撤销。

**Recipe manifest：可展示、可适配、可追踪**

建议包含 schema_version、kind、ui_stack、min_sdk、模块/入口要求、文件清单、dependency 坐标与版本、允许的仓库、设计参数、接入步骤、验证规则、作者/来源声明和 preview 描述。

文件清单使用相对路径、语言、字节数、digest 和 file asset ID。禁止绝对路径、父目录跳转、重复规范化路径及符号链接导入；文本大小和总文件数有界。接入说明作为不可信参考材料，不成为系统指令，不能借投稿启用网络、MCP、Hooks、Rules 或读取秘密文件。

preview 分为 image_gallery 与受支持的 native_preset。native_preset 只接受客户端已实现的 renderer ID 和校验后的有限参数，不接受 Kotlin/JS/HTML 执行代码。sourceBuilder 在种子导出时求值成版本源码，不出现在网络 schema。

作者上传的截图标为作者预览，平台实际构建/设备截图另有来源与版本标签。首版预览上传与 R7 的“让模型看到图片像素”是不同能力：上传图片成功不代表 Agent 已具备视觉输入。

**投稿与素材上传**

首版建议采用“创建上传会话 → 逐文件 PUT 二进制 → finalize → 引用 asset_id”。普通 JSON 仍保持小体积限制；上传端点单独设置限额并对真实接收字节计数，不能只相信 Content-Length。复用 ArtifactStore 前补流式写入、删除、只读分段/下载和元数据校验。

以下是待测量后可调整的首版默认值，全部通过服务端 capability 返回，Android 不写死：

| 项目 | 建议初值 |
| --- | --- |
| 标题/摘要 | 80 / 300 个字符；正文 10,000 个字符 |
| 源码 | 每版本最多 30 个文本文件，单文件 256 KiB，总源码 1 MiB |
| 图片 | 封面加截图最多 6 张；JPEG/PNG/WebP，单张 5 MiB；解码后最多 16 MP，生成缩略图并移除位置等元数据 |
| 上传 | 每稿件素材总量 20 MiB，单账号同时最多 2 个上传会话；未完成会话 24 小时过期 |
| 投稿 | 每账号每日最多提交 5 次、同时待审最多 3 项；上传存储配额与调用/构建配额独立 |

检查 MIME 与文件签名、可解码性、文本编码和大小；异常图片不进公开缓存；不接受 SVG/HTML、APK/dex/jar 或可执行附件作为图片/源码。压缩包后续若开放，需要单独的解压后配额、路径和炸弹防护设计。

从项目分享时由用户选文件并确认公开清单；排除凭据、keystore、local.properties、.env、.git、构建目录及服务配置，扫描疑似秘密和明显个人数据并要求处理命中。扫描通过仅表示未命中规则，不等于证明没有秘密。

首版记录作者署名、原创/改编声明、来源链接和允许复用的许可信息，公开详情展示这些字段；缺失信息退回补齐。管理员可依据举报暂时下架并联系作者。这里是产品数据与运营流程设计，不以勾选声明替代内容判断。

不默认由服务端抓取用户填写的外链，避免把来源 URL 变成任意网络请求入口。检查、预览和验证失败不得触发无限重试或无预算的模型/构建调用。

**审核、验证与应用执行**

基础检查包括 manifest、文件/图片、兼容字段、来源信息、秘密规则及重复内容提示。编译验证可由管理员在受控模板里触发，使用 R1 的隔离、独立缓存与资源预算；审核服务进程本身不运行投稿代码。首版允许发布“已审核、未构建验证”的条目，标签必须准确；发布与“平台已验证”分别设定条件。

应用流程由服务端接收 creative_id、revision_id、project_id、base_revision 和幂等键，按下列顺序执行：

1. 校验用户、项目归属、版本批准及当前可应用状态；首版仅允许当前公开版本新建应用，版本已切换则返回冲突并刷新。已登记任务继续使用原快照；从封存内容获取 manifest/源码，忽略客户端伪造内容。
2. 识别项目 UI 栈、SDK、依赖及入口；必要时要求用户选择兼容方案，普通明确兼容的应用不强制额外规划。
3. 在可恢复事务边界登记 application、会话与任务；同一请求重试返回相同 ID。如果不能一次提交，使用 application 启动状态和幂等补偿，不留下无法关联的孤儿会话。
4. Worker 开始前重新校验版本是否被阻断；把来源材料作为低信任上下文，所有写入、依赖、网络和命令继续经过当前工具权限。
5. 使用版本化编辑、checkpoint、构建/测试和有限修复；结果关联创意版本、工程 revision、验证任务和 APK。
6. 更新 application 与结果卡。收藏数、浏览数、应用请求数和实际成功数分别统计；普通用户不能上报一个 success 字段获得平台验证标签。

浏览/投稿不应隐式发起付费模型调用；应用到项目沿用已有任务费用与授权设置，验证构建另有管理员配额。配额预留、重试和失败释放需要幂等，避免重复点击重复计费。

**API 草案**

以下为目标契约。C1/C2 已实现其中目录、作者、审核、收藏、举报与指标子集；表内仍包含 C3 和完整素材治理的后续接口，当前精确路径及行为以 C2 实施记录和 OpenAPI 快照为准。公开、作者、管理三个路由分区独立鉴权。

| 路由组 | 主要接口 |
| --- | --- |
| 能力与浏览 | GET /api/creative/capabilities；GET /api/creative/categories；GET /api/creative/items?q=&category=&origin=&sort=&cursor=&limit=；GET /api/creative/items/{id}；GET /api/creative/items/{id}/versions/{revision_id} |
| 作者管理 | GET/POST /api/me/creative/items；POST /api/me/creative/items/{id}/drafts；GET/PATCH /api/me/creative/items/{id}/drafts/{draft_id}；POST /api/me/creative/items/{id}/drafts/{draft_id}/submit |
| 撤回与个人状态 | POST /api/me/creative/items/{id}/revisions/{revision_id}/withdraw；POST /api/me/creative/items/{id}/unpublish；DELETE 私有草稿接口；GET /api/me/creative/notifications，POST 已读接口 |
| 素材 | POST /api/me/creative/uploads；PUT /api/me/creative/uploads/{upload_id}/content；POST /api/me/creative/uploads/{upload_id}/finalize；DELETE 未完成上传；GET /api/creative/assets/{asset_id}；管理员素材读取走管理路由 |
| 使用与反馈 | POST /api/projects/{project_id}/creative-applications；GET application 状态；PUT/DELETE /api/me/creative/favorites/{id}；POST /api/creative/items/{id}/reports |
| 管理 | GET/POST /api/admin/creative/items；GET/PATCH 稿件；GET review queue；POST revision review；POST publish/unpublish/restore/archive；PATCH placements/categories；GET reports/audit/metrics |

PublicItem 只含公开版本和可展示作者资料；OwnerItem 额外含本人草稿/审核意见；AdminItem 才包含内部检查与管理字段。不要以同一 dict 返回全部数据再让前端隐藏。

内容版本使用 ETag/content hash，列表使用 generation；写请求携带版本前置条件。错误至少区分 creative_unavailable、revision_conflict、incompatible_project、submission_limit、asset_not_ready、unsupported_preview、account_restricted。越权访问私有 ID 默认不泄露对象存在性；公开条目撤回可返回稳定不可用状态。

发布、下架、恢复、分类和推荐排序变化都更新相应目录 generation；游标需绑定筛选条件和排序版本，失效时客户端重新载入，避免分页混入两套排序或遗漏下架记录。

capabilities 提供 schema 版本、可投稿类型、文件/素材/次数限额、支持的 preview kind、是否允许公开浏览/投稿/验证/应用及最低客户端能力。管理 API 即使继续不进入公开 OpenAPI，也应有独立契约快照和鉴权测试。

**Android 数据层、缓存与兼容**

建立 CreativeRepository、CreativeSquareViewModel、CreativeEditorViewModel、CreativeDetailViewModel 和远程 DTO；Compose 接收 UI state 与事件。去掉 Fragment 直接依赖静态目录、直接创建裸 ask 的职责。

公共目录缓存按 server origin + 条目 revision 标识；作者稿件、通知、收藏和上传凭据额外按 user ID 隔离。未同步草稿是用户创作数据，不能放进当前带 destructive migration 的纯缓存数据库；用具有迁移策略的持久草稿存储，附件引用也需在进程重启后有效。

公开列表定期条件刷新；收到 tombstone/新 generation 后清理被撤回条目及素材引用；详情与应用前重新校验。离线时可显示已缓存内容并标为离线/可能过期，不能离线启动受管应用或把待审稿变为已发布。已保存到设备或被复制的内容无法被远程保证抹除，下架承诺应限定为新的服务端分发与受管应用。

原生 preview 只使用本机支持的类型和参数；未知预览或新分类使用图片/文本降级，不能让 enum 解码导致整个广场失败。图片服务与客户端实现尺寸限制、取消、缩略图和按需加载。

旧版 App 的静态目录在更新前无法远程完整管理；过渡期提供升级提示及明确的最低创意 API 能力。不能声称新的下架 API 能撤销旧客户端已经内置或用户已经复制的代码，也不通过模糊匹配普通 ask 文本来假装实现撤回。

**现有创意迁移与发布步骤**

1. 固定导出时的目录与源码快照；把 sourceBuilder 求值为文本、生成 manifest 和内容 hash，保留 ID/slug、分类、来源与预览映射。导出当前实际目录，不写死只允许六项。
2. 幂等导入为 official 草稿，记录导入版本和 source commit；不覆盖后台已编辑内容，重复导入生成差异或新版本。
3. 核对每项封面/原生预览、代码、兼容条件及接入说明，显式批准发布；只为实际通过的条目标记构建验证。
4. Android 通过 capability 切换远程目录；远程空目录是有效结果，不能自动用内置列表重新补回已下架内容。离线示例另区显示，不能冒充当前在线目录。
5. 先开放后台官方内容运营，再小范围开放用户投稿；关闭 submissions_enabled 只停新提交，已发布内容、作者草稿和处理中的审核仍可访问和处理。
6. 将创意数据库表、封存素材、草稿、审核与应用记录纳入 R1-09 备份恢复；运行迁移演练。回滚优先关闭功能写入口、兼容读取，不删除新稿件或回退为覆盖式种子导入。

**工作单与交付阶段**

| 工作单 | 范围与关键落点 | 验收结果 |
| --- | --- | --- |
| CG-01 | 创意/版本/审核/展示状态、manifest、角色、DTO、CreativeStore 与迁移 | v1 发布时 v2 可独立待审；不可变提交、状态冲突和不同 DTO 字段权限通过测试 |
| CG-02 | 种子导出、官方导入、legacy ID 和 native preview 映射 | 实际目录条目完整迁移；重复导入不覆盖后台改动；未知 preview 可降级 |
| CG-03 | 列表/详情/分类/搜索/分页/capabilities/ETag | 管理后台变化无需重发 APK 即可显示；未发布、下架、私有稿件不进入公开结果 |
| CG-04 | 素材上传会话、限额/扫描、封存、鉴权读取及回收 | 进程中断可重试；跨用户不可绑定/读取素材；假 MIME、超限和未完成素材不能发布 |
| CG-05 | 后台目录、官方编辑、审核、版本 Diff、发布/下架/恢复、推荐分类 | 每项创意可管理；两审核员并发不覆盖；下架与批准竞态不会重新公开；全部操作可追溯 |
| CG-06 | Android 远程 Repository/ViewModel、目录/详情/原生及图片预览、缓存兼容 | 网络异常可恢复；未知分类不崩溃；空目录不补回下架种子；账号切换无私有数据残留 |
| CG-07 | 用户发布向导、项目文件选择、持久草稿、上传恢复和“我的创意” | 普通用户独立完成投稿、查看意见、修改再提交；进程死亡与升级保留未同步草稿；后台任务不会自行发布 |
| CG-08 | 举报、审核消息、收藏、作者显示、基础运营统计 | 用户能看到审核/下架结果；私有备注不泄露；点击与成功指标分开，重试不重复计数 |
| CG-09 | 服务端版本化 creative-application、兼容检查、幂等任务与证据关联 | 重复点击只有一个应用任务；客户端伪造源码无效；应用固定审核版本；排队后下架会阻止执行 |
| CG-10 | 隔离验证、检查报告、Recipe/工程质量与验证标签 | 投稿代码不在 API/管理员设备执行；构建证据绑定版本/环境；未验证不显示已验证；配额和取消有效 |
| CG-11 | capability 灰度、旧版兼容、备份/恢复、资产保留与账号注销关联 | 后台下架在在线新客户端失效；恢复演练保留作品和关联；关闭投稿开关不丢稿件；账号注销按既定策略处理公开作品和私有数据 |
| CG-12 | 端到端、越权/并发/失败回归、后台与 Android 可用性、发布门禁 | 普通用户上传 → 后台审核 → 另一用户看到 → 应用 → 对应版本 APK；核心失败场景与迁移通过 |

建议交付批次：

| 批次 | 包含工作 | 依赖与可交付边界 |
| --- | --- | --- |
| C1：可管理的远程广场 | CG-01/02/03，CG-04 基础素材，CG-05 管理官方条目，CG-06 | R0、R1 身份/部署基础、相应 R2-02 契约；先使后台每项修改可在 Android 生效 |
| C2：用户投稿与审核 | CG-04 完整上传、CG-05 完整审核、CG-07/08、CG-11 首版策略 | C1、R1-03 缓存身份边界、备份恢复；实现本需求另一半，不能以只做 C1 宣布完成 |
| C3：可信应用与运营发布 | CG-09/10/11/12 | R1-06 工程能力、R1-08 执行隔离、R2-01/03 版本和幂等、R3-04 证据；受管应用放量前必须满足 |

C1/C2 的目录和 CRUD 不需要等待完整 TurnRunner 重构、LSP、多实例或所有双端体验升级。CG-09/10 的执行能力按明确依赖接入；在这些依赖未通过时可浏览投稿，但隐藏/关闭社区内容的应用和验证入口，不能绕回不校验版本的旧 ask 路径。本需求完整完成以 C1–C3 的验收为准。

工期需要在 CG-01 明确首版范围、素材存储和现有并行修改完成度后估算；不能把“数据库加一张表”的成本当成投稿、审核、素材和任务链路的总成本。

**必须覆盖的验收场景**

- 账号 A 无法读取或修改 B 的草稿、素材、审核意见；伪造 owner/official/approved/verification 字段不生效；匿名只能取得已公开内容。
- v2 待审、退回或拒绝不影响 v1；作者提交后修改不能改变审核中的内容；两次审批、批准与下架并发有确定结果。
- 上传断线、过期、进程重启、重复 finalize、配额超限、错误 MIME、路径跳转、疑似秘密均有明确失败与恢复；服务不执行投稿脚本。
- 图片加载失败和未知 preview/category 不使列表崩溃；后台新增/排序/下架后新客户端可见对应结果；离线缓存不会绕过应用前检查。
- 账号切换、设备旋转、进程死亡及 Room 版本升级后，公开缓存与私有持久草稿仍有正确边界；旧上传不能归到新账号。
- 应用重试不重复建会话/任务；公开版本切换不改变已登记快照；版本被阻断后排队任务无法开始；构建后项目变化使 APK 标为过期。
- 后台预览不执行投稿 HTML/脚本；审核意见、通知和 audit 不包含 Token；多人运营的身份与权限按实际认证方式验证。
- 作者注销默认隐藏其公开作品并清理私有草稿/未引用素材；去标识化的审核和既有任务必要版本引用按配置保留，不向公开接口泄露已注销资料；不自动转成官方作品。正常删除、管理员封禁和材料留存分别处理。
- 备份恢复覆盖 DB 与封存素材；GC 不删仍被公开版本、有效任务或恢复点引用的内容；撤回后短期访问票据失效策略有测试。

上线观察投稿完成率、审核时长、退回原因、上传失败率、曝光→详情→应用→构建成功转化、每次验证成本、举报与处理时间。所有指标按版本及环境区分，先建立基线再设目标。
