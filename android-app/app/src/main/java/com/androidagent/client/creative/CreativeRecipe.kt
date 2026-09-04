package com.androidagent.client.creative

enum class CreativeCategory(val label: String) {
    ALL("全部"),
    COMPONENT("组件"),
    MOTION("动效"),
    FEEDBACK("反馈"),
    DATA("数据"),
}

enum class CreativePreview {
    PULSE_BUTTON,
    EXPANDABLE_CARD,
    FAVORITE_TOGGLE,
    ANIMATED_COUNTER,
    LOADING_DOTS,
    PROGRESS_REVEAL,
}

data class CreativeRecipe(
    val id: String,
    val title: String,
    val summary: String,
    val category: CreativeCategory,
    val preview: CreativePreview,
    val tags: Set<String>,
    val source: String,
    val dependencies: List<String> = emptyList(),
    val minSdk: Int = 24,
) {
    fun buildAgentPrompt(): String = """
        请把 Android Agent 创意广场中的 UI Recipe「$title」应用到当前项目。

        Recipe ID: $id
        类型: ${category.label}
        最低 SDK: $minSdk
        Recipe 额外依赖（Compose 基础依赖之外）:
        ${if (dependencies.isEmpty()) "- 无额外依赖" else dependencies.joinToString("\n") { "- $it" }}

        参考实现：
        ```kotlin
        $source
        ```

        执行要求：
        1. 先检查当前工程使用 Compose 还是 XML/View，并理解现有主题与导航结构。
        2. 以参考实现的视觉和动画行为为准，适配当前工程；不要盲目覆盖已有入口或主题。
        3. 若工程尚未启用 Compose，优先完成最小、兼容的 Compose 接入；如果改造代价明显过高，可以实现等价的 View 版本并说明取舍。
        4. 将组件接入一个用户能够实际打开的页面，补充必要的字符串、颜色、依赖和无障碍描述。
        5. 运行 Gradle assembleDebug；如失败，修复后再次验证。
        6. 最后汇报改动文件、使用方式和验证结果。
    """.trimIndent()
}
