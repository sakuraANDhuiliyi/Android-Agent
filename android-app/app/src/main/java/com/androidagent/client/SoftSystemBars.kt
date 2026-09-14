package com.androidagent.client

import android.app.Activity
import android.app.Application
import android.content.res.Configuration
import android.os.Bundle
import androidx.core.view.WindowInsetsControllerCompat

/** Reapply system chrome after theme recreation; do not change content Insets ownership. */
class SoftSystemBars : Application.ActivityLifecycleCallbacks {
    override fun onActivityResumed(activity: Activity) {
        val dark = activity.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK == Configuration.UI_MODE_NIGHT_YES
        val controller = WindowInsetsControllerCompat(activity.window, activity.window.decorView)
        controller.isAppearanceLightStatusBars = !dark
        controller.isAppearanceLightNavigationBars = !dark
        activity.window.statusBarColor = activity.getColor(R.color.signal_surface)
        activity.window.navigationBarColor = activity.getColor(R.color.signal_navigation)
    }
    override fun onActivityCreated(activity: Activity, state: Bundle?) = Unit
    override fun onActivityStarted(activity: Activity) = Unit
    override fun onActivityPaused(activity: Activity) = Unit
    override fun onActivityStopped(activity: Activity) = Unit
    override fun onActivitySaveInstanceState(activity: Activity, state: Bundle) = Unit
    override fun onActivityDestroyed(activity: Activity) = Unit
}
