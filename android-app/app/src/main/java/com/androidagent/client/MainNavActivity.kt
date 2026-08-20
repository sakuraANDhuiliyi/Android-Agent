package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.databinding.ActivityMainNavBinding
import com.google.android.material.badge.BadgeDrawable
import com.google.android.material.navigation.NavigationBarView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 根导航容器：项目 / 活动 / 待处理。
 * 紧凑宽度使用底部导航，>=600dp 使用 Navigation Rail（见 layout-w600dp）。
 * Settings/Connection 通过 toolbar menu 进入，不占用高频导航位。
 */
class MainNavActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainNavBinding
    private lateinit var prefs: AgentPrefs

    private var navView: NavigationBarView? = null
    private var pendingBadge: BadgeDrawable? = null
    private var currentTab = R.id.nav_projects

    interface Refreshable {
        fun refreshContent()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = AgentPrefs(this)
        if (prefs.apiToken.isBlank() || prefs.serverUrl.isBlank()) {
            startActivity(Intent(this, MainActivity::class.java))
            finish()
            return
        }

        binding = ActivityMainNavBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.toolbar.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                R.id.action_settings -> {
                    ConnectionSettingsActivity.start(this)
                    true
                }
                R.id.action_refresh -> {
                    (supportFragmentManager.findFragmentById(R.id.navContent) as? Refreshable)
                        ?.refreshContent()
                    refreshPendingBadge()
                    true
                }
                else -> false
            }
        }

        val bottom = binding.bottomNav
        val rail = binding.railNav
        navView = if (rail != null && rail.visibility == View.VISIBLE) rail else bottom
        navView?.setOnItemSelectedListener { item ->
            if (currentTab != item.itemId || supportFragmentManager.findFragmentById(R.id.navContent) !is ProjectsFragment) {
                currentTab = item.itemId
                switchTo(item.itemId)
            }
            true
        }

        if (savedInstanceState == null) {
            val tab = when (intent.getStringExtra(DeepLink.EXTRA_TAB)) {
                DeepLink.TAB_APPROVALS -> R.id.nav_pending
                else -> R.id.nav_projects
            }
            navView?.selectedItemId = tab
            switchTo(tab)
        }
    }

    private fun switchTo(itemId: Int) {
        val fragment = when (itemId) {
            R.id.nav_activity -> ActivityFeedFragment()
            R.id.nav_pending -> ApprovalsFragment()
            R.id.nav_me -> MeFragment()
            else -> ProjectsFragment()
        }
        binding.toolbar.setTitle(
            when (itemId) {
                R.id.nav_activity -> R.string.nav_activity
                R.id.nav_pending -> R.string.nav_pending
                R.id.nav_me -> R.string.nav_me
                else -> R.string.nav_projects
            },
        )
        supportFragmentManager.beginTransaction()
            .replace(R.id.navContent, fragment, "tab_$itemId")
            .commit()
    }

    override fun onResume() {
        super.onResume()
        if (prefs.apiToken.isBlank()) {
            startActivity(Intent(this, MainActivity::class.java))
            finish()
            return
        }
        refreshPendingBadge()
    }

    /** Approval Inbox 数量由 Fragment 刷新后回写；其他 tab 由 resume 时的轻量查询补足。 */
    fun updatePendingBadge(count: Int) {
        val view = navView ?: return
        if (count <= 0) {
            view.removeBadge(R.id.nav_pending)
            pendingBadge = null
            return
        }
        val badge = view.getOrCreateBadge(R.id.nav_pending)
        badge.isVisible = true
        badge.number = count
        pendingBadge = badge
    }

    private fun refreshPendingBadge() {
        val api = AgentApi(prefs.serverUrl, prefs.apiToken)
        lifecycleScope.launch {
            try {
                val count = withContext(Dispatchers.IO) {
                    val jobs = api.listJobs().filter { UiFormat.isActive(it.status) }
                    var total = 0
                    for (job in jobs) {
                        total += api.listApprovals(job.id).count { it.status == "pending" }
                    }
                    total
                }
                updatePendingBadge(count)
            } catch (e: Exception) {
                // 连接失败不打断导航，Projects 页 banner 会提示
            }
        }
    }

    companion object {
        fun start(context: Context) {
            context.startActivity(
                Intent(context, MainNavActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
            )
        }
    }
}
