# 三端 UI 升级与并行功能衔接

本轮负责桌面、Android 和管理后台的视觉与新增功能呈现，以 `UNIFIED_UPGRADE_PLAN.md` 的 R4 与 CG 工作单为依据。保留正在修改的创意目录、账号缓存、执行安全和后端接口。

设计方向：中性分层表面、蓝色主操作、清楚的状态标签、可滚动的长表单、深浅主题。参考 [Linear 界面层级](https://linear.app/now/behind-the-latest-design-refresh) 与 [Material 3 自适应布局](https://m3.material.io/foundations/layout/canonical-examples/overview)。

功能衔接原则：UI 只依据服务端返回的事实显示完成、验证和发布状态。尚未开放的能力显示原因；本地草稿与服务端提交分开。新增界面独立于创意样式目录和服务端领域实现，便于并行升级合并。

主要视觉落点：`desktop/src/workbench-ui.css`、`agent/admin/studio.css`、Android 共享资源色板和创意 Compose 主题。新增任务摘要与创意作者界面使用独立模块。

## 当前接入结果

- 桌面：统一深浅主题、导航、表单和工具区；`delivery-ui.js` 根据单个任务的真实标记展示交付信息，明确测试、安装、代码版本关联的缺失证据。
- Android：XML 与 TheoKit 共用蓝色中性色板；创意 Compose 主题使用相同资源。远程广场、内置示例与账号页均接入 `CreativeStudioActivity`，支持按账号/服务地址隔离的本地草稿、源码导入和三步预览。投稿按钮有明确未开放状态。
- 后台：`studio.js` 对接并行增加的 `agent/creative/api.py`；编辑、版本发布、下架恢复、分类、推荐和审计均沿用实际接口。审核入口读取 capabilities，不显示虚构待审数据。并行补充的内置导入和参考链接保留。

后台“恢复”为解除下架并回到未发布状态，之后须显式选择版本发布。编辑中的内容不能误发布成旧的已保存快照。所有普通文本使用 textContent；写请求携带 expected_version。

本轮没有把社区投稿、审核处理、云端草稿与版本化应用宣称为已完成；当前服务端关闭这些能力。创意作者页现阶段保存到本机，不会自动上传。

[本机三端预览与验证记录](/Users/mac/Android-Agent/.artifacts/ui-v4/README.md)。桌面测试入口：`npm run check`、`npm run test:unit`、`npm run test:screenshot`、`npm run test:studio`。Android 构建使用独立临时目录以免并行缓存冲突；构建日志位于 `.artifacts/ui-v4/`。

最终验证：桌面 check、全套 unit、截图矩阵与 studio UI 检查通过；Android assembleDebug、128 项 JVM 测试与 lintDebug 通过（0 errors、329 warnings、1 hint）。隔离 API 36 模拟器已检查列表、编辑、键盘、深色与 1.3 倍字体。此为客户端 UI 和离线数据验证，不替代社区投稿到审核发布的在线闭环验收。
