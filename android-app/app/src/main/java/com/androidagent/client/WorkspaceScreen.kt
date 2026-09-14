package com.androidagent.client

import android.content.ClipData
import android.content.ClipboardManager
import android.os.Bundle
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Shared small-screen chrome; server work is never owned by these Activities. */
abstract class WorkspaceScreen : AppCompatActivity() {
    protected lateinit var api: AgentApi
    protected lateinit var body: LinearLayout
    protected lateinit var toolbar: MaterialToolbar
    protected var projectId = ""
    protected fun screen(title: String, scrollable: Boolean = true) {
        val prefs = AgentPrefs(this)
        projectId = intent.getStringExtra("project_id").orEmpty()
        api = AgentApi(prefs.serverUrl, prefs.apiToken)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(androidx.core.content.ContextCompat.getColor(context, R.color.signal_surface))
        }
        toolbar = MaterialToolbar(this).apply {
            this.title = title
            setNavigationIcon(R.drawable.ic_arrow_back)
            setNavigationOnClickListener { finish() }
        }
        root.addView(toolbar, LinearLayout.LayoutParams(-1, -2))
        body = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(20), dp(8), dp(20), dp(24)) }
        root.addView(if (scrollable) ScrollView(this).apply { addView(body) } else body, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
    }
    protected fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
    protected fun label(value: String): TextView = TextView(this).apply {
        text = value; textSize = 15f
        setTextColor(androidx.core.content.ContextCompat.getColor(context, R.color.signal_on_surface))
        setLineSpacing(dp(3).toFloat(), 1f)
        setPadding(0, dp(12), 0, dp(12)); body.addView(this)
    }
    protected fun button(value: String, action: () -> Unit): MaterialButton = MaterialButton(this, null, com.google.android.material.R.attr.materialButtonOutlinedStyle).apply {
        text = value; minHeight = dp(52); cornerRadius = dp(26)
        gravity = android.view.Gravity.START or android.view.Gravity.CENTER_VERTICAL
        body.addView(this, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(8) })
        setOnClickListener { action() }
    }
    protected fun <T> request(load: () -> T, done: (T) -> Unit) {
        lifecycleScope.launch {
            try { done(withContext(Dispatchers.IO) { load() }) }
            catch (e: kotlinx.coroutines.CancellationException) { throw e }
            catch (e: Exception) { Toast.makeText(this@WorkspaceScreen, e.message, Toast.LENGTH_LONG).show() }
        }
    }
    protected fun copy(value: String) {
        getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Agent context", value))
    }
    protected fun askAgent(context: ContextAttachment, draft: String) {
        request({ api.createConversation(projectId, draft.take(60)) }) { conversation ->
            ConversationActivity.start(this, projectId, conversation.id, conversation.title, draft = draft, contexts = listOf(context))
        }
    }
}
