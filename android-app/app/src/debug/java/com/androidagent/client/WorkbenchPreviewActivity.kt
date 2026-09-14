package com.androidagent.client

import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.compose.runtime.*
import com.androidagent.client.creative.*
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.appbar.MaterialToolbar

/** Offline visual fixtures. Debug source set only; no credentials, API or task execution. */
class WorkbenchPreviewActivity : AppCompatActivity() {
    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        recreate()
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        delegate.localNightMode = if (intent.getBooleanExtra("dark", false))
            androidx.appcompat.app.AppCompatDelegate.MODE_NIGHT_YES else androidx.appcompat.app.AppCompatDelegate.MODE_NIGHT_NO
        super.onCreate(savedInstanceState)
        val name = intent.getStringExtra("screen") ?: "activity_project_detail"
        if (name == "appearance-live") {
            startActivity(android.content.Intent(this, AppearanceActivity::class.java))
            finish()
            return
        }
        if (name.startsWith("creative-")) {
            setContent {
                CreativeSquareTheme {
                    val example = CreativeDraft("preview", title = "灵动底部导航", summary = "为常用页面提供清晰、流畅的导航体验。", source = "@Composable fun Navigation() {}", integration = "在主页面底部接入 Navigation。", license = "MIT")
                    var selected by remember { mutableStateOf(if (name == "creative-editor") example else null) }
                    if (name == "creative-square") {
                        CreativeSquareScreen(CreativeCatalog.recipes, null, {}, {})
                    } else CreativeStudioScreen(listOf(example), selected, true,
                        "离线界面预览 · 社区投稿尚未开放，草稿仅保存在本机。",
                        onBack = { finish() }, onLogin = {}, onCreate = { selected = CreativeDraft("new") },
                        onSelect = { selected = it }, onUpdate = { selected = it }, onImport = {})
                }
            }
            return
        }
        val layouts = mapOf(
            "fragment_chat_home" to R.layout.fragment_chat_home,
            "fragment_me" to R.layout.fragment_me,
            "fragment_projects" to R.layout.fragment_projects,
            "activity_notification_settings" to R.layout.activity_notification_settings,
            "activity_register" to R.layout.activity_register,
            "activity_build_log" to R.layout.activity_build_log,
            "activity_project_detail" to R.layout.activity_project_detail,
            "activity_conversation" to R.layout.activity_conversation,
            "activity_feedback" to R.layout.activity_feedback,
            "activity_diff" to R.layout.activity_diff,
            "activity_file_diff" to R.layout.activity_file_diff,
            "activity_file_browser" to R.layout.activity_file_browser,
            "activity_apk" to R.layout.activity_apk,
            "activity_main_nav" to R.layout.activity_main_nav,
            "activity_token_usage" to R.layout.activity_token_usage,
            "activity_main" to R.layout.activity_main,
        )
        val previewLayout = layouts[name] ?: resources.getIdentifier(name, "layout", packageName).takeIf { it != 0 }
        setContentView(previewLayout ?: R.layout.activity_project_detail)
        val fixture = mapOf(
            "textHubName" to "Android Workspace",
            "textHubPackage" to "com.example.workspace · main",
            "textHubStatus" to "main · 6 个文件待审查",
            "textCurrentTaskTitle" to "深色模式改造已完成",
            "textCurrentTaskMeta" to "修改 6 个文件 · 1 分 24 秒",
            "textChangesSummary" to "6 个文件\n+128 −42",
            "textBuildSummary" to "构建成功\n42.8 秒",
            "textTestsSummary" to "27 项通过",
            "textProblemsSummary" to "3 条警告",
            "textStatus" to "构建成功",
            "textSummary" to "assembleDebug\n42.8 秒 · 38 个任务\n27 项测试通过 · 3 条警告\napp-debug.apk · 18.6 MB",
            "textPath" to "app/src/main/java/com/example/LoginRepository.kt",
            "textStats" to "+128  −42",
            "textCheckpoint" to "检查点 · 完成深色模式改造\n6 个文件 · 刚刚",
            "textCurrentPath" to "app/src/main/java/com/example",
            "textOpenFile" to "LoginRepository.kt",
        )
        fun fill(view: View) {
            if (view is MaterialToolbar) {
                view.title = "Android Workspace"
                view.setNavigationIcon(R.drawable.ic_arrow_back)
            }
            if (view is TextView && view.id != View.NO_ID) {
                fixture[resources.getResourceEntryName(view.id)]?.let { view.text = it }
            }
            if (view is ViewGroup) for (i in 0 until view.childCount) fill(view.getChildAt(i))
        }
        fill(findViewById(android.R.id.content))
        if (name == "activity_feedback") {
            findViewById<TextView>(R.id.textSummary).text = "42.8 秒"
            findViewById<TextView>(R.id.textTaskMetrics).text = "38 已执行 · 12 已缓存"
            findViewById<TextView>(R.id.textWarningMetrics).text = "3"
            findViewById<TextView>(R.id.textTestMetrics).text = "27 通过 · 0 失败"
            findViewById<TextView>(R.id.textArtifactMetrics).text = "app-debug.apk · 18.6 MB"
        }
        if (name == "activity_main_nav" && intent.getBooleanExtra("drawer", false)) {
            findViewById<androidx.drawerlayout.widget.DrawerLayout>(R.id.drawerLayout)
                .openDrawer(androidx.core.view.GravityCompat.START)
        }
        if (name == "activity_diff") findViewById<TextView>(R.id.textSummary).text = "6 个文件　+128 −42"
        if (name == "fragment_chat_home") findViewById<View>(R.id.textGuestQuota).visibility = View.GONE
        if (name == "activity_conversation") {
            val timeline = findViewById<androidx.recyclerview.widget.RecyclerView>(R.id.recyclerTimeline)
            val adapter = ConversationTimelineAdapter(object : ConversationTimelineAdapter.Callbacks {
                override fun onToggleWork(turnKey: String, expanded: Boolean) = Unit
                override fun onApprovalAction(model: ApprovalCardBinder.Model, approve: Boolean, always: Boolean) = Unit
                override fun onViewChanges(turnKey: String) = Unit
                override fun onLoadEarlier() = Unit
            })
            timeline.layoutManager = androidx.recyclerview.widget.LinearLayoutManager(this)
            timeline.adapter = adapter
            adapter.submitList(listOf(
                ConversationTimelineBuilder.Row.User("u", 1, "turn", "给登录页加上深色模式，保留现有的交互。"),
                ConversationTimelineBuilder.Row.Assistant("a", 1, "turn", "已添加深色模式，并保持原有的登录、注册和输入校验。\n\n```kotlin\n@Composable\nfun LoginScreen() {\n    val colors = MaterialTheme.colorScheme\n    LoginForm(colors = colors)\n}\n```\n\n颜色会跟随系统外观变化。请检查登录页在不同字体大小下的显示。", false),
                ConversationTimelineBuilder.Row.Changes("c", 1, "turn", listOf("LoginScreen.kt", "themes.xml", "colors.xml"), 0, 3, 0, 64, 18),
            ))
            if (intent.getBooleanExtra("contexts", false)) {
                findViewById<View>(R.id.scrollContexts).visibility = View.VISIBLE
                findViewById<com.google.android.material.chip.ChipGroup>(R.id.chipContexts).apply {
                    listOf("@ MainActivity.kt", "@ LoginScreen.kt", "# build error").forEach { label ->
                        addView(com.google.android.material.chip.Chip(this@WorkbenchPreviewActivity).apply { text = label; isCloseIconVisible = true })
                    }
                }
            }
        }
    }
}
