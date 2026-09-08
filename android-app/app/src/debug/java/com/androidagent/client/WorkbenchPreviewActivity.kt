package com.androidagent.client

import android.os.Bundle
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
        val layouts = mapOf(
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
        setContentView(layouts[name] ?: R.layout.activity_project_detail)
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
        if (name == "activity_diff") findViewById<TextView>(R.id.textSummary).text = "6 个文件　+128 −42"
    }
}
