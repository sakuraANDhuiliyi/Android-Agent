# Aora 表情接入

来源：[sam70361/aora-bot](https://github.com/sam70361/aora-bot)，2026-09-18 获取。
本次接入 `emotion-ball` 球形角色；没有接入 `mood-mates` 的云宝、亮亮角色或上游展示站。

## 位置与行为

- Android：`ConversationActivity` 顶部标题栏右侧，56dp。复用原生任务状态文本供无障碍阅读。
- Desktop：智能工作台顶部显示选中任务的状态；编辑器 AI 面板显示当前会话状态。
- 全部 32 种原始表情保留。自动映射目前采用下面的子集，不向模型新增提示词、不解析聊天文字猜测情绪。
- 映射未知值时回到待机；同一表情不重复启动，避免流式更新不断重置动画。
- 隐藏或被工作台遮挡的桌面面板、后台标签页和后台 Android Activity 停止动画；回到前台后恢复。桌面遵循减少动态效果设置，Android 实时遵循系统动画开关；销毁聊天页时释放 WebView。

| 任务状态 | emotionId | 表情 |
| --- | --- | --- |
| 无任务 / 未知值 | 02 | 待机 |
| 未连接 / 暂停 | 06 | 休眠 |
| 发送中（Android） | 31 | 接收任务 |
| 排队 / 等待审批 / 等待输入 | 35 | 等待输入 |
| 运行 | 30 | 思考 |
| 成功 | 33 | 完成 |
| 失败 / 中断 | 34 | 出错 |
| 正在停止 / 已取消 | 41 | 停止 |

`agent-emotion.js` 还提供 `executing`、`searching`、`replying` 的映射，供以后接入更细粒度的工具执行事件。

## 文件与更新

`desktop/src/vendor/aora-bot/` 是主副本：

- `rings.js`、`emotions.js`、`ball.js`、`engine.js`：未修改的上游源文件，SHA-256 记录于 `UPSTREAM.json`。
- `agent-emotion.js`：本项目的状态映射和生命周期封装。
- `avatar.html`、`avatar.js`：Android 离线页面。
- `LICENSE`、`LICENSE-COMMERCIAL.md`、`NOTICE.md`：完整上游声明。

Android 将相同内容打包到 `app/src/main/assets/aora-bot/`。更新主副本后执行：

```sh
python3 scripts/sync_aora_assets.py
python3 scripts/sync_aora_assets.py --check
```

Android 通过 `AgentEmotionView` 拦截专用本地域名，仅加载允许列表内的包内资源。
WebView 不接入 JavaScript 原生桥、不传递账号凭证，不允许文件、内容提供器或网络资源访问。
宿主仅使用 JSON 编码后的状态字符串调用页面。

桌面复用：

```js
window.AgentEmotion.update('元素ID', 'running');
const avatar = window.AgentEmotion.mount(document.getElementById('元素ID'));
avatar.setActive(false);
avatar.destroy();
```

## 验证

```sh
cd desktop
npm run check
npm run test:unit
npm run test:emotion
cd ../android-app
./gradlew :app:testDebugUnitTest :app:assembleDebug :app:lintDebug
```

`test:emotion` 使用真实 Chromium 检查 32 种表情的连续渲染、状态切换、未知输入、桌面双入口联动、尺寸、前后台暂停恢复、减少动态效果、页面缓存恢复和销毁。
浏览器测试截图保存在 `.artifacts/aora/`，不提交版本库。

`AgentEmotionPlaybackTest` 覆盖页面迟到回调、父视图隐藏、分离再挂载、系统动画开关与释放后的回调。设备验收还须检查实际 WebView：运行时帧变化，关闭系统动画后静止，后台 DOM 不再变化，返回同一页面后恢复，退出后无残留 WebView 或异常日志。

Android 的调试预览页支持离线验证（仅 Debug 包导出）：

```sh
adb shell am start -n com.androidagent.client/.WorkbenchPreviewActivity \
  --es screen activity_conversation --es emotionStatus running --ez dark true
```

`emotionStatus` 还可用 `succeeded`、`failed`、`paused` 等映射状态。实际聊天页由 ViewModel 状态驱动，不读取该预览参数。

## 许可范围

本次包含的球形角色（blob / wedge / gem 造型、配色及特效）仅供个人技术学习研究，禁止任何商业用途，上游明确不提供商业授权。
引擎代码和表情数据采用独立的非商业免费 / 商业授权许可；引擎商业授权不包含球形角色视觉。
如项目要商用，需要另行取得引擎授权并使用有合法商用许可的角色。以随包保留的 LICENSE 和 NOTICE 原文为准。
