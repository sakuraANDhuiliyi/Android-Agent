package com.androidagent.client

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.core.view.isVisible
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.databinding.FragmentChatHomeBinding
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Chat-first landing page. It creates a conversation only after the first message is sent. */
class ChatHomeFragment : Fragment(), MainNavActivity.Refreshable {

    private var _binding: FragmentChatHomeBinding? = null
    private val binding get() = _binding!!
    private lateinit var prefs: AgentPrefs
    private var submitting = false

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        _binding = FragmentChatHomeBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        prefs = AgentPrefs(requireContext())
        binding.btnSend.setOnClickListener { sendPrompt() }
        binding.editPrompt.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_SEND) {
                sendPrompt()
                true
            } else false
        }
        binding.chipSuggestionOne.setOnClickListener {
            fillPrompt(getString(R.string.chat_suggestion_one))
        }
        binding.chipSuggestionTwo.setOnClickListener {
            fillPrompt(getString(R.string.chat_suggestion_two))
        }
        binding.chatModeToggle.addOnButtonCheckedListener { _, checkedId, isChecked ->
            if (isChecked && checkedId == R.id.btnModeWork) {
                binding.chatModeToggle.check(R.id.btnModeChat)
                (activity as? MainNavActivity)?.selectProjects()
            }
        }
        renderQuota()
    }

    override fun onResume() {
        super.onResume()
        renderQuota()
    }

    override fun refreshContent() = renderQuota()

    private fun fillPrompt(value: String) {
        binding.editPrompt.setText(value)
        binding.editPrompt.setSelection(value.length)
        binding.editPrompt.requestFocus()
    }

    private fun renderQuota() {
        if (!::prefs.isInitialized) return
        binding.textGuestQuota.isVisible = prefs.guestMode
        if (prefs.guestMode) {
            binding.textGuestQuota.text = getString(R.string.guest_quota_remaining, prefs.guestRemaining)
        }
    }

    private fun sendPrompt() {
        if (submitting) return
        val prompt = binding.editPrompt.text?.toString()?.trim().orEmpty()
        if (prompt.isBlank()) return
        if (prefs.guestMode && prefs.guestRemaining <= 0) {
            showLoginRequired()
            return
        }
        if (prefs.apiToken.isBlank() || prefs.serverUrl.isBlank()) {
            toast(getString(R.string.guest_session_connecting))
            (activity as? MainNavActivity)?.ensureSession()
            return
        }

        submitting = true
        binding.btnSend.isEnabled = false
        binding.progress.isVisible = true
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val result = withContext(Dispatchers.IO) {
                    val api = AgentApi(prefs.serverUrl, prefs.apiToken)
                    val projects = api.listProjects()
                    val selected = prefs.selectedProjectId
                    val project = projects.firstOrNull { it.id == selected }
                        ?: projects.firstOrNull()
                        ?: api.createProject("我的创作", null)
                    val title = prompt.replace('\n', ' ').take(36)
                    val conversation = api.createConversation(project.id, title)
                    val job = api.askConversation(
                        conversation.id,
                        prompt,
                        provider = prefs.selectedProviderId.takeUnless { it == "auto" },
                    )
                    Triple(project, conversation, job)
                }
                if (prefs.guestMode) prefs.guestRemaining = prefs.guestRemaining - 1
                binding.editPrompt.setText("")
                val (project, conversation, job) = result
                prefs.selectedProjectId = project.id
                prefs.selectedConversationId = conversation.id
                ConversationActivity.start(
                    requireContext(),
                    project.id,
                    conversation.id,
                    conversation.title,
                    job.id,
                )
                (activity as? MainNavActivity)?.refreshDrawerHistory()
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (e: ApiException) {
                if (e.errorCode == "guest_quota_exhausted") {
                    prefs.guestRemaining = 0
                    renderQuota()
                    showLoginRequired()
                } else {
                    toast(e.detail)
                }
            } catch (e: Exception) {
                toast(getString(R.string.connect_failed_status, e.message.orEmpty()))
            } finally {
                submitting = false
                _binding?.let {
                    it.btnSend.isEnabled = true
                    it.progress.isVisible = false
                }
            }
        }
    }

    private fun showLoginRequired() {
        if (!isAdded) return
        AlertDialog.Builder(requireContext())
            .setTitle(R.string.guest_quota_title)
            .setMessage(R.string.guest_quota_message)
            .setPositiveButton(R.string.login_or_register) { _, _ ->
                MainActivity.startLogin(requireContext())
            }
            .setNegativeButton(R.string.not_now, null)
            .show()
    }

    private fun toast(message: String) {
        context?.let { Toast.makeText(it, message, Toast.LENGTH_SHORT).show() }
    }

    override fun onDestroyView() {
        _binding = null
        super.onDestroyView()
    }
}
