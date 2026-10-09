package com.androidagent.client

import android.annotation.SuppressLint
import android.content.Context
import android.database.ContentObserver
import android.graphics.Color
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.AttributeSet
import android.view.View
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import org.json.JSONObject
import java.io.ByteArrayInputStream

/** Offline Aora SVG renderer. No JS bridge, credentials, or remote content. */
@SuppressLint("SetJavaScriptEnabled")
class AgentEmotionView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null,
) : FrameLayout(context, attrs) {
    private val playback = AgentEmotionPlaybackState()
    private var initialized = false
    private var browserPaused = false
    private var lastAnimationActive: Boolean? = null
    private var observingAnimationScale = false
    private var status = "idle"
    private val browser = WebView(context)
    private val animationScaleObserver = object : ContentObserver(Handler(Looper.getMainLooper())) {
        override fun onChange(selfChange: Boolean) {
            if (playback.released) return
            refreshAnimationPreference()
            syncPlayback()
        }
    }

    init {
        importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
        isFocusable = false // The adjacent native toolbar already announces task status.
        browser.setBackgroundColor(Color.TRANSPARENT)
        browser.isVerticalScrollBarEnabled = false
        browser.isHorizontalScrollBarEnabled = false
        browser.settings.apply {
            javaScriptEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            blockNetworkLoads = true
            domStorageEnabled = false
            setSupportZoom(false)
        }
        browser.setOnTouchListener { _, _ -> true }
        browser.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) = true
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse {
                val uri = request.url
                val name = uri.lastPathSegment.orEmpty()
                if (uri.scheme == "https" && uri.host == "appassets.androidplatform.net" &&
                    uri.path == "/aora-bot/$name" && name in ASSET_FILES
                ) {
                    val mime = if (name.endsWith(".js")) "application/javascript" else "text/html"
                    return WebResourceResponse(mime, "UTF-8", context.assets.open("aora-bot/$name"))
                }
                return WebResourceResponse("text/plain", "UTF-8", ByteArrayInputStream(ByteArray(0)))
            }
            override fun onPageFinished(view: WebView, url: String) {
                if (playback.released || url != PAGE_URL) return
                playback.pageFinished()
                lastAnimationActive = null
                render()
                syncPlayback()
            }
        }
        addView(browser, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
        browser.loadUrl(PAGE_URL)
        initialized = true
        syncPlayback()
    }

    fun setStatus(value: String) {
        if (playback.released || status == value) return
        status = value
        render()
    }

    fun setForeground(value: Boolean) {
        if (playback.released) return
        playback.setForeground(value)
        refreshAnimationPreference()
        syncPlayback()
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        if (playback.released) return
        playback.attach(isShown && windowVisibility == View.VISIBLE)
        context.contentResolver.registerContentObserver(
            Settings.Global.getUriFor(Settings.Global.ANIMATOR_DURATION_SCALE),
            false,
            animationScaleObserver,
        )
        observingAnimationScale = true
        refreshAnimationPreference()
        syncPlayback()
    }

    override fun onVisibilityAggregated(isVisible: Boolean) {
        super.onVisibilityAggregated(isVisible)
        if (!initialized || playback.released) return
        // Includes ancestor and window visibility; isShown alone misses window changes.
        playback.setVisible(isVisible)
        syncPlayback()
    }

    override fun onDetachedFromWindow() {
        if (!playback.released) {
            playback.detach()
            syncPlayback()
        }
        stopObservingAnimationScale()
        super.onDetachedFromWindow()
    }

    private fun refreshAnimationPreference() {
        // Read the setting directly so its observer cannot race ValueAnimator's cached scale.
        // This also supports API 24/25, where areAnimatorsEnabled() is unavailable.
        playback.setAnimationsEnabled(Settings.Global.getFloat(
            context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f,
        ) > 0f)
    }

    private fun stopObservingAnimationScale() {
        if (!observingAnimationScale) return
        context.contentResolver.unregisterContentObserver(animationScaleObserver)
        observingAnimationScale = false
    }

    private fun render() {
        if (playback.ready && !playback.released) browser.evaluateJavascript(
            "window.agentAvatar && window.agentAvatar.update(${JSONObject.quote(status)});", null,
        )
    }

    private fun syncPlayback() {
        if (!initialized || playback.released) return
        if (playback.shouldResume && browserPaused) {
            browser.onResume()
            browserPaused = false
        }
        val active = playback.shouldAnimate
        if (playback.ready && lastAnimationActive != active) {
            browser.evaluateJavascript(
                "window.agentAvatar && window.agentAvatar.setActive($active);", null,
            )
            lastAnimationActive = active
        }
        if (!playback.shouldResume && !browserPaused) {
            // onPause does not pause JavaScript. Stop this avatar above first; pauseTimers
            // would also stop unrelated WebViews throughout the app.
            browser.onPause()
            browserPaused = true
        }
    }

    fun release() {
        if (playback.released) return
        playback.release()
        stopObservingAnimationScale()
        browser.stopLoading()
        removeView(browser)
        browser.destroy()
    }

    companion object {
        private const val PAGE_URL = "https://appassets.androidplatform.net/aora-bot/avatar.html"
        private val ASSET_FILES = setOf("avatar.html", "avatar.js", "agent-emotion.js", "rings.js", "emotions.js", "ball.js", "engine.js")
    }
}

/** Native playback gates, independent of WebView so lifecycle races can be tested on the JVM. */
internal class AgentEmotionPlaybackState {
    var ready = false
        private set
    var released = false
        private set
    private var foreground = false
    private var attached = false
    private var visible = false
    private var animationsEnabled = true

    val shouldResume get() = !released && foreground && attached && visible
    val shouldAnimate get() = ready && shouldResume && animationsEnabled

    fun setForeground(value: Boolean) { if (!released) foreground = value }
    fun setVisible(value: Boolean) { if (!released) visible = value }
    fun setAnimationsEnabled(value: Boolean) { if (!released) animationsEnabled = value }
    fun pageFinished() { if (!released) ready = true }

    fun attach(isVisible: Boolean) {
        if (released) return
        attached = true
        visible = isVisible
    }

    fun detach() {
        attached = false
        visible = false
    }

    fun release() {
        released = true
        ready = false
        detach()
    }
}
