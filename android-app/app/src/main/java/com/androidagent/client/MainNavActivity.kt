package com.androidagent.client

import android.content.Context
import android.graphics.Typeface
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.GravityCompat
import androidx.core.view.isVisible
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.core.database.AppDatabase
import com.androidagent.client.databinding.ActivityMainNavBinding
import com.androidagent.client.feature.conversation.ConversationRepository
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Chat-first root with a ChatGPT-style navigation drawer and account conversation history. */
class MainNavActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainNavBinding
    private lateinit var prefs: AgentPrefs
    private var creatingGuestSession = false
    private var currentDestination = R.id.nav_chat
    private var drawerConversations: List<ConversationInfo> = emptyList()

    interface Refreshable {
        fun refreshContent()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = AgentPrefs(this)
        applyConfiguredServer()
        binding = ActivityMainNavBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.toolbar.setNavigationIcon(R.drawable.ic_menu)
        binding.toolbar.setNavigationContentDescription(R.string.open_sidebar)
        binding.toolbar.setNavigationOnClickListener {
            binding.drawerLayout.openDrawer(GravityCompat.START)
        }
        binding.btnDrawerNewChat.setOnClickListener { selectChat() }
        binding.btnDrawerProjects.setOnClickListener { switchTo(R.id.nav_projects) }
        binding.btnDrawerCreative.setOnClickListener { switchTo(R.id.nav_creative) }
        binding.btnDrawerActivity.setOnClickListener { switchTo(R.id.nav_activity) }
        binding.btnDrawerPending.setOnClickListener { switchTo(R.id.nav_pending) }
        binding.rowDrawerAccount.setOnClickListener {
            binding.drawerLayout.closeDrawer(GravityCompat.START)
            if (prefs.guestMode) MainActivity.startLogin(this) else switchTo(R.id.nav_me)
        }
        binding.btnDrawerSearch.setOnClickListener { showConversationSearch() }

        if (savedInstanceState == null) {
            currentDestination = when (intent.getStringExtra(DeepLink.EXTRA_TAB)) {
                DeepLink.TAB_APPROVALS -> R.id.nav_pending
                DeepLink.TAB_CREATIVE -> R.id.nav_creative
                else -> R.id.nav_chat
            }
            switchTo(currentDestination)
        }
        renderAccount()
        ensureSession()
    }

    private fun applyConfiguredServer() {
        val configuredUrl = BuildConfig.AGENT_SERVER_URL.trim().trimEnd('/')
        val previousUrl = prefs.serverUrl.trim().trimEnd('/')
        if (previousUrl != configuredUrl && prefs.apiToken.isNotBlank()) prefs.clearAuth()
        prefs.serverUrl = configuredUrl
    }

    fun ensureSession() {
        if (prefs.apiToken.isNotBlank()) {
            renderAccount()
            refreshDrawerHistory()
            return
        }
        if (creatingGuestSession || prefs.serverUrl.isBlank()) return
        creatingGuestSession = true
        lifecycleScope.launch {
            try {
                val auth = withContext(Dispatchers.IO) {
                    AgentApi(prefs.serverUrl).createGuestSession(currentDevice(this@MainNavActivity))
                }
                prefs.saveGuestAuth(auth)
                renderAccount()
                (supportFragmentManager.findFragmentById(R.id.navContent) as? Refreshable)
                    ?.refreshContent()
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (e: Exception) {
                if (e is ApiException && e.isNotFound) return@launch
                Toast.makeText(
                    this@MainNavActivity,
                    getString(R.string.guest_session_failed),
                    Toast.LENGTH_LONG,
                ).show()
            } finally {
                creatingGuestSession = false
            }
        }
    }

    private fun switchTo(itemId: Int) {
        currentDestination = itemId
        val (title, fragment) = when (itemId) {
            R.id.nav_projects -> R.string.nav_projects to ProjectsFragment()
            R.id.nav_creative -> R.string.nav_creative to CreativeSquareFragment()
            R.id.nav_activity -> R.string.nav_activity to ActivityFeedFragment()
            R.id.nav_pending -> R.string.nav_pending to ApprovalsFragment()
            R.id.nav_me -> R.string.nav_me to MeFragment()
            else -> R.string.drawer_chat to ChatHomeFragment()
        }
        binding.toolbar.setTitle(title)
        supportFragmentManager.beginTransaction()
            .replace(R.id.navContent, fragment, "destination_$itemId")
            .commit()
        binding.drawerLayout.closeDrawer(GravityCompat.START)
    }

    fun selectChat() = switchTo(R.id.nav_chat)

    fun selectCreative() = switchTo(R.id.nav_creative)

    fun selectProjects() = switchTo(R.id.nav_projects)

    override fun onResume() {
        super.onResume()
        renderAccount()
        ensureSession()
        if (!prefs.guestMode) refreshDrawerHistory()
    }

    fun refreshDrawerHistory() {
        renderAccount()
        if (prefs.guestMode || prefs.apiToken.isBlank()) {
            drawerConversations = emptyList()
            renderHistory(emptyList())
            return
        }
        val api = AgentApi(prefs.serverUrl, prefs.apiToken)
        val repository = conversationRepository(api)
        lifecycleScope.launch {
            // 缓存优先：先渲染本地会话列表，后台再同步服务端
            if (drawerConversations.isEmpty()) {
                try {
                    val cached = repository.cachedConversations(DRAWER_HISTORY_LIMIT)
                    if (cached.isNotEmpty()) {
                        val cachedItems = cached.map { repository.cachedConversationInfo(it) }
                        drawerConversations = cachedItems
                        renderHistory(cachedItems)
                    }
                } catch (_: Exception) {
                    /* 缓存不可用时直接走网络 */
                }
            }
            try {
                val conversations = withContext(Dispatchers.IO) {
                    api.listProjects()
                        .flatMap { project -> api.listConversations(project.id) }
                        .sortedByDescending { it.updatedAt ?: it.createdAt ?: 0.0 }
                        .take(DRAWER_HISTORY_LIMIT)
                }
                repository.replaceConversations(conversations)
                drawerConversations = conversations
                renderHistory(conversations)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                // Drawer history is secondary; the current page shows actionable connection errors.
            }
        }
    }

    private fun conversationRepository(api: AgentApi): ConversationRepository {
        val db = AppDatabase.get(applicationContext)
        return ConversationRepository(
            api = api,
            eventDao = db.conversationEventDao(),
            conversationDao = db.conversationDao(),
            jobDao = db.jobDao(),
            approvalDao = db.approvalDao(),
        )
    }

    private fun renderAccount() {
        if (!::binding.isInitialized) return
        val guest = prefs.guestMode || prefs.userId.isBlank()
        binding.textRecentTitle.isVisible = !guest
        binding.drawerHistoryScroll.isVisible = !guest
        binding.textGuestHistory.isVisible = guest
        if (guest) {
            binding.textAccountAvatar.text = "游"
            binding.textAccountName.setText(R.string.guest_account)
            binding.textAccountMeta.text = getString(R.string.guest_account_meta, prefs.guestRemaining)
        } else {
            val name = prefs.displayName.ifBlank { prefs.displayEmail.substringBefore('@').ifBlank { "我" } }
            binding.textAccountAvatar.text = name.take(1).uppercase()
            binding.textAccountName.text = name
            binding.textAccountMeta.text = prefs.displayEmail
        }
    }

    private fun renderHistory(items: List<ConversationInfo>) {
        if (!::binding.isInitialized || prefs.guestMode) return
        binding.drawerHistoryList.removeAllViews()
        if (items.isEmpty()) {
            binding.drawerHistoryList.addView(historyText(getString(R.string.no_conversations), null, muted = true))
            return
        }
        items.forEach { conversation ->
            binding.drawerHistoryList.addView(
                historyText(conversation.title, conversation) {
                    prefs.selectedProjectId = conversation.projectId
                    prefs.selectedConversationId = conversation.id
                    binding.drawerLayout.closeDrawer(GravityCompat.START)
                    ConversationActivity.start(
                        this,
                        conversation.projectId,
                        conversation.id,
                        conversation.title,
                    )
                },
            )
        }
    }

    private fun historyText(
        label: String,
        conversation: ConversationInfo?,
        muted: Boolean = false,
        onClick: (() -> Unit)? = null,
    ): TextView = TextView(this).apply {
        text = label
        textSize = 15f
        gravity = Gravity.CENTER_VERTICAL
        maxLines = 1
        ellipsize = android.text.TextUtils.TruncateAt.END
        setPadding(dp(10), 0, dp(10), 0)
        minHeight = dp(46)
        if (muted) alpha = 0.6f else {
            setTypeface(typeface, Typeface.NORMAL)
            setBackgroundResource(android.R.drawable.list_selector_background)
            isClickable = true
            isFocusable = true
            setOnClickListener { onClick?.invoke() }
            contentDescription = conversation?.title
        }
    }

    private fun showConversationSearch() {
        if (prefs.guestMode) {
            MainActivity.startLogin(this)
            return
        }
        val input = EditText(this).apply {
            hint = getString(R.string.search_conversations)
            inputType = InputType.TYPE_CLASS_TEXT
            setPadding(dp(20), dp(8), dp(20), dp(8))
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.search_conversations)
            .setView(input)
            .setPositiveButton(R.string.search) { _, _ ->
                val query = input.text?.toString()?.trim().orEmpty()
                renderHistory(
                    if (query.isBlank()) drawerConversations
                    else drawerConversations.filter { it.title.contains(query, ignoreCase = true) },
                )
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    fun updatePendingBadge(count: Int) {
        binding.btnDrawerPending.text = if (count > 0) {
            getString(R.string.drawer_pending_count, count)
        } else getString(R.string.nav_pending)
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    companion object {
        private const val DRAWER_HISTORY_LIMIT = 50

        fun start(context: Context, tab: String? = null) {
            context.startActivity(DeepLink.mainNavIntent(context, tab))
        }
    }
}
