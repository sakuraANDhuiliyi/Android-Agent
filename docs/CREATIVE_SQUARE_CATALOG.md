# 创意广场 · 精选交互实验

2026-10-08：移除旧有 494 个风格排列与 6 个基础组件，先改为八个独立设计的作品。三轮再增加十五个有明确来源与许可的开源最小移植，总计 23 件。每件作品有自己的构图、绘制方式与交互，不再通过配色与通用布局相乘扩充数量。

| 作品 | 设计与交互 |
| --- | --- |
| 轨道声场 · 轨道唱片机 | 偏心轨道、巨幅唱片和酸绿标记；播放状态与曲目切换演示 |
| 棱镜实验 · 光谱调音台 | 折射光束与精密刻度；滑杆调整光谱参数 |
| 地形漫游 · 等高线探索 | 连续地形线、地图坐标与路线标记；路线与行程信息联动 |
| 字形引力 · 动态排版海报 | 巨幅字体、裁切色块和错位排版；切换版式 |
| 蓝色档案 · 艺术展览索引 | 钴蓝与纸色、不对称索引和抽象艺术；切换展品 |
| 情绪花园 · 生长心情签 | 植物形态与陶土配色；心情选择改变花瓣与文案 |
| 出走车站 · 翻页出发票 | 翻页显示、冲孔票面与线路索引；目的地切换 |
| 蓝调仪式 · 流沙专注钟 | 群青与粉笔白、抽象沙漏；一分钟演示支持开始、暂停和重置 |

## 新增开源微移植（2026-10-08）

只提取实现视觉效果所需的路径、绘制或交互，不接入上游整库、演示站、图片、JS 引擎或额外依赖。Compose 详情可直接操作；静态缩略图不会启动持续动画。元数据记录 40 位提交、源码链接、视频来源、提取范围和完整许可。复制代码、应用提示词及服务端种子都保留归属，开源移植不标记为原创。

| 作品 | 视频 / 项目 | 最小范围与许可 |
| --- | --- | --- |
| 文字潮汐 · 让文字绕着你走 | [抖音 Cooper说](https://jingxuan.douyin.com/m/video/7623065044632243508) · [chenglou/pretext](https://github.com/chenglou/pretext) | `dynamic-layout.ts` 行槽切分与连续游标，障碍改为圆弦、用 Android Paint 测量。MIT；完整附录也保留。 |
| 流体海报 · 液态揭幕 | [YouTube DevSense](https://www.youtube.com/watch?v=uGoWVz-q2M8) · [react-liquidswipe](https://github.com/ashutosh1919/react-liquidswipe) | `getPath` 两段三次贝塞尔、拖动过半提交与回弹。MIT。 |
| 折页印刷室 · 掌心翻页 | [GitHub pagecurl](https://github.com/oleksandrbalan/pagecurl) | `CurlDraw` 折线裁剪、镜像背面与旋转，缩成两页，移除分页系统与阴影位图。Apache-2.0。 |
| 引力工具台 · 弹跳凹槽导航 | [GitHub Exyte](https://github.com/exyte/AndroidAnimatedNavigationBar) | `IndentPath` 双贝塞尔凹槽、`Parabolic` 二次轨迹，移除整库配置。MIT。 |
| 环形观测站 · 数据环流 | [Bilibili Android_Zero](https://www.bilibili.com/video/BV18nQwYZEJw/) · [GitHub Developer Chunk](https://github.com/developerchunk/Custom-Pie-Chart-Jetpack-Compose) · [原作者 YouTube](https://www.youtube.com/watch?v=H9cJ9jBzQ3c) | `drawArc` 分段和入场动画；增加选择与重播，修正边界。上游 MIT 文本包含一句附加说明，按原文保留，不仅存 SPDX 标签。 |

核验范围：抖音视频索引标题点名 `chenglou/pretext`，据此核对仓库；YouTube 液态视频公开描述直接链接源码，README 回链同视频；Bilibili 简介索引直接链接圆环图项目，仓库回链原作者视频。B站正文抓取受限，未将索引核验描述为完整观看。另两项来自直接 GitHub 检索及作者 README 演示。完整证据说明放在各条 `origin.evidence`。

许可原文位于 `tools/creative/licenses/`，同时嵌入 App 详情和复制源码。源码头标明上游版本与本地修改。未引入没有明确许可证的 WouoUI、Juraj fluid-bottom-navigation 等候选。

## 第二轮新增（2026-10-08）

| 作品 | 视频 / 项目 | 最小范围与许可 |
| --- | --- | --- |
| 深度卷轴 · 圆柱透视选择器 | [YouTube William Candillon](https://www.youtube.com/watch?v=PVSjPswRn0U) · [Picker 源码](https://github.com/wcandillon/can-it-be-done-in-react-native/tree/72678212d4041f124e1585cdf6360f36737daa5f/the-10-min/src/Picker) | `asin` 旋转、圆柱深度与等距吸附；Compose 替代 React/Reanimated，提供前后按钮。MIT。 |
| 黏性墨量实验 · 黏性气泡滑杆 | [Ramotion Fluid Slider](https://github.com/Ramotion/fluid-slider-android) | 双圆切线与双贝塞尔液桥；移除 View/XML 状态系统，数值同步控制墨量图形。MIT。 |
| 放射印章罗盘 · 弹簧放射工具盘 | [skydoves Example14](https://github.com/skydoves/compose-animations#14-radial-fab-menu) | 极坐标定位、逐项弹簧、50ms 错峰与逆序回收；增加四枚可选择印章。Apache-2.0。 |
| 星图刮印 · 刮开一片星空 | [AdamDawi ScratchCardCompose](https://github.com/AdamDawi/ScratchCardCompose) | 离屏遮罩与清除笔触；程序绘制替换图片，去重网格覆盖率修复重复擦拭累计，提供揭开和重置按钮。MIT。 |
| 磁场标本 · 把秩序轻轻推开 | [Bilibili Proton 演示](https://www.bilibili.com/video/BV1Dd4y1G7se/) · [drawcall/Proton](https://github.com/drawcall/Proton) | 吸引、排斥与带阻尼的积分；原生 Canvas 小规模点阵，不引入 Web 粒子引擎。MIT。 |

第二轮视频链核验：YouTube 公开描述直接链接 Picker 源码目录；Bilibili 已索引简介直接列出 `drawcall/Proton`。其他三项为 GitHub 直接检索，分别核对作者演示、源码、完整许可及提交版本。没有将所有项目都标成视频直接来源。

## 第三轮新增（2026-10-08）

| 作品 | 视频 / 项目 | 最小范围与许可 |
| --- | --- | --- |
| 折光通行证 · 指尖全息通行证 | [vanilla-tilt.js](https://github.com/micku7zu/vanilla-tilt.js) | 归一化触点、双轴角度、`atan2` 反光方向；原生印纹和视差图层替换 DOM，使用触摸和角度按钮，无传感器权限。MIT。 |
| 形态印章 | [veltman/flubber](https://github.com/veltman/flubber) | 轮廓补点、环起点对齐、逐点插值；96 点的芒星／十字／徽盾连续过渡，可拖动或滑块控制，不搬 SVG 解析器。MIT。 |
| 排字工坊 | [YouTube Chrome Drag-to-Sort](https://www.youtube.com/watch?v=-39OEXk_mWc) · [Candillon Chrome 示例](https://github.com/wcandillon/can-it-be-done-in-react-native/tree/72678212d4041f124e1585cdf6360f36737daa5f/season4/src/Chrome) | 触点转格序、双槽交换、拖拽层抬升与归位；长按拖动有按钮替代，移除浏览器、自动滚动和图片。MIT。 |
| 分层地貌标本 | [Ramotion expanding-collection](https://github.com/Ramotion/expanding-collection-android) | 邻页透明度／缩放、先推开再展开的时序；原生绘制地貌分层，移除 ViewPager 与位图缓存。MIT。 |
| 机械翻牌计数台 | [absswds/FlipClock](https://github.com/absswds/FlipClock) | 全高测量的上下半片、90° 换面与180°收束；手动计数只翻变化位，不带定时器、日期 API 或字体资源。MIT。 |

第三轮视频链核验：YouTube 公开视频简介明确指向 `season4/src/Chrome`。其余四项明确记为直接 GitHub 来源；本轮未核实到可采用的新抖音或 Bilibili 视频直链，未将搜索线索写成已确认关联。所有条目保留固定提交、原始许可与实际修改范围。

## 展示与数据边界

- 默认入口是 App 内置精选，离线可用；搜索、分类、详情、源码复制和应用到项目继续可用。
- 社区目录为独立入口，保留服务端分页、收藏、举报、源码与封面能力。线上数据不会冒充本地精选。
- 缩略图使用实际 Compose 场景缩放绘制，关闭交互与计时；详情运行原生交互。图形通过 Canvas 绘制，不下载图片或字体。
- 展示数据与播放控制属于本地演示；专注钟有真实的一分钟倒计时，不连接音频或业务服务。
- App 外层跟随深浅色主题，作品自身保持经过设计的色板。
- 新种子包包含当前 23 件作品。服务端导入仍遵循“新增、不覆盖”的规则；已有线上旧条目不会被自动删除，需要管理员按原有发布流程下架。这次代码更新不修改生产数据库。

## 维护

- 开源归属：`origin` 字段声明项目、版本、许可、提取范围、来源证据及 `noticePath`；生成器校验版本和许可文件，`references` 使用锁定提交的源码链接。
- 元数据：`tools/creative/catalog.json`，每个视觉系列只对应一个独立布局；选项数量按交互需要设置为 1–12 项。
- 原生作品：`StyleImmersiveLayouts.kt`、`StyleEditorialLayouts.kt`、`StyleSource*Layouts.kt`。每段 `// pattern:<id>` 包含一个可独立导出的作品及其私有辅助函数。
- 公共类型与基础工具：`StyleFoundation.kt`；缩略图测量：`StyleThumbnail.kt`。
- 生成器：`scripts/generate_creative_catalog.py`。同时生成 Kotlin 目录、分发器和可复制源码，合并场景所需 imports。
- 种子导出：`scripts/export_creative_seed.py`。必须与 Android 源码保持逐字节一致，哈希匹配才允许远程内容使用内置预览。

```bash
python3 scripts/generate_creative_catalog.py
python3 scripts/export_creative_seed.py
python3 scripts/generate_creative_catalog.py --check
python3 scripts/export_creative_seed.py --check
cd android-app
./gradlew testDebugUnitTest assembleDebug --offline
```

独立验证所有复制源码（使用全新输出目录）：

```bash
./gradlew testDebugUnitTest --tests '*CreativeCatalogTest' \
  -I ../tools/creative/validate-recipes.init.gradle \
  -PcreativeExportDir=/tmp/creative-recipes-new
./gradlew compileDebugKotlin \
  -I ../tools/creative/validate-recipes.init.gradle \
  -PcreativeValidationSources=/tmp/creative-recipes-new
./gradlew assembleDebug
```

Debug 的 `WorkbenchPreviewActivity` 支持 `screen=creative-square` 检查展厅，或 `screen=creative-recipe` 与 `recipe=<id>` 检查指定作品，不需要连接线上服务。
