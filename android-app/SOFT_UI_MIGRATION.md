# Android Soft UI 重构 · 2026-09-13

依据：`design/android-soft-ui/SPECIFICATION.md`、`COVERAGE.md`、SVG 与原始线性图标。实施对象是现有 Android 客户端，不修改桌面端或后端。

## 本轮落地

- XML、TheoKit 动态控件、Compose 创意页面共用黑白灰浅/深配色。主按钮为 52dp 胶囊，公共卡片 22dp，输入框 16dp，Composer 28dp；成功、失败、警告仍保留语义色。
- 直接将稿件中的 21 个线性图标转为 Android VectorDrawable，并统一发送箭头。登录与抽屉去除蓝紫渐变，修复头像、悬浮按钮的深色对比问题。
- 首页改为可滚动的欢迎区、快捷提示及一体式 Composer。保留聊天/工作入口与原发送流程，没有将示例模型或项目数据写进生产逻辑。
- 对话输入独占一行，操作放在下方；上下文换行、可查看完整路径、可单独移除，附件区最多 96dp 并支持滚动。保留现有窗口 Insets、草稿、发送、停止与任务逻辑。
- 用户气泡按实际内容宽度限制到 78%；助手开放排版；代码块增加行距、圆角与细边框。Review / 撤销按钮复用既有自适应布局，窄屏和大字号下纵排。
- Project 去除重复大标题；保留当前任务、改动、构建、测试、问题、审批、APK 与历史入口，将工具入口收敛成细分隔列表。
- Diff Hunk 长行可横向滚动，字号 13sp、行距 1.5；文件编辑器仍保留原有选择代码和 Agent 操作。
- 构建反馈分项呈现耗时、任务、警告、测试及产物；缺失数据为“—”，不冒充零警告/测试通过。问题行按严重程度着色并增加阅读间距。
- 终端采用独立输出区、命令输入框、运行/停止与复制/询问两组操作；不改变远程 session 和命令执行规则。
- “我的 → 外观与阅读”可选择浅色、深色、跟随系统；设备本地保存，使用系统字号。设置行说明允许换行。
- 主题切换时同步系统状态栏、导航栏及图标明暗；构建页与设置页统一使用 Material 开关。
- 修复 TheoKit 的行高倍率、字体权重及圆角/描边的 dp 换算问题。

公共主题覆盖既有页面，但这不等于将 100 张静态状态稿逐像素变成 100 个新 Activity。模型、任务、审批、构建等仍由真实服务端状态决定，创意示例自身的艺术风格也保留。

## 验证结果

- `assembleDebug`：通过，调试 APK 位于 `app/build/outputs/apk/debug/app-debug.apk`。
- `testDebugUnitTest`：149 项，0 失败，0 错误。新增浅/深文字对比度、XML/动态配色一致性、Composer 结构和缺失构建统计的回归检查。
- `lintDebug`：0 errors，325 warnings，1 hint。警告尚未清零，不应理解为全项目无静态问题。
- 已检查修改的 XML 格式与原有布局 ID 保留情况，未移除原有绑定 ID；`git diff --check` 通过。
- Android 12 模拟器：约 412dp 正常字号、360dp/1.5 倍字体；检查首页、对话、登录、项目、通知及新增外观页。Gboard 弹出时，输入框与发送按钮保持在键盘上方。
- 实测外观页切换至深色，确认页面重建、选中状态与本地偏好保存。

截图见 [实际界面截图](screenshots/soft-ui/README.md)。多数页面使用 debug-only 离线预览入口与演示数据，展示真实布局/消息渲染器，不表示已连通服务器验证全部交互。外观页主题切换使用真实 Activity。

## 构建环境说明

本机 Android Studio 内置 Java 17 曾在 Lint 时触发 ARM JIT 崩溃。以下参数重试后构建、测试和 Lint 均通过；未降低依赖校验或关闭 Lint：

```sh
JAVA_HOME='/Applications/Android Studio.app/Contents/jbr/Contents/Home' \
ANDROID_HOME=/Users/sakura/Library/Android/sdk \
./gradlew :app:assembleDebug :app:testDebugUnitTest :app:lintDebug \
  --offline --no-daemon --max-workers=2 \
  '-Dorg.gradle.jvmargs=-Xmx2048m -XX:TieredStopAtLevel=1 -Dfile.encoding=UTF-8'
```

## 尚需上线前回归

未执行真实账号登录、Agent 任务、审批、远程终端命令、APK 安装、全部横屏/分屏及不同厂商设备矩阵。设计稿中的自动构建、终端启用和后台通知边界没有被 UI 重构改变。未推送远程、未部署后端，未承诺所有设备与数据组合绝无遮挡。
