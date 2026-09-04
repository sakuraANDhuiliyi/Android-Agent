package com.androidagent.client

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.platform.ViewCompositionStrategy
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.creative.CreativeCatalog
import com.androidagent.client.creative.CreativeRecipe
import com.androidagent.client.creative.CreativeSquareScreen
import com.androidagent.client.creative.CreativeSquareTheme
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Compose 试点模块：浏览、复制并把 UI Recipe 应用到用户项目。 */
class CreativeSquareFragment : Fragment() {

    private lateinit var prefs: AgentPrefs
    private var applyingRecipeId by mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = AgentPrefs(requireContext())
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = ComposeView(requireContext()).apply {
        setViewCompositionStrategy(ViewCompositionStrategy.DisposeOnViewTreeLifecycleDestroyed)
        setContent {
            CreativeSquareTheme {
                CreativeSquareScreen(
                    recipes = CreativeCatalog.recipes,
                    applyingRecipeId = applyingRecipeId,
                    onCopy = ::copyRecipe,
                    onApply = ::chooseTargetProject,
                )
            }
        }
    }

    private fun copyRecipe(recipe: CreativeRecipe) {
        val clipboard = requireContext().getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText(recipe.title, recipe.source))
        toast(getString(R.string.creative_copied, recipe.title))
    }

    private fun chooseTargetProject(recipe: CreativeRecipe) {
        if (applyingRecipeId != null) return
        if (prefs.apiToken.isBlank() || prefs.serverUrl.isBlank()) {
            AlertDialog.Builder(requireContext())
                .setTitle(R.string.guest_title)
                .setMessage(R.string.creative_login_required)
                .setPositiveButton(R.string.guest_login) { _, _ ->
                    MainActivity.startLogin(requireContext())
                }
                .setNegativeButton(R.string.cancel, null)
                .show()
            return
        }
        val api = AgentApi(prefs.serverUrl, prefs.apiToken)
        applyingRecipeId = recipe.id
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val projects = withContext(Dispatchers.IO) { api.listProjects() }
                applyingRecipeId = null
                if (projects.isEmpty()) {
                    toast(getString(R.string.creative_no_project))
                    return@launch
                }
                showProjectPicker(recipe, projects)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                applyingRecipeId = null
                toast(e.message ?: getString(R.string.creative_load_projects_failed))
            }
        }
    }

    private fun showProjectPicker(recipe: CreativeRecipe, projects: List<ProjectInfo>) {
        val labels = projects.map { project ->
            if (project.packageName.isBlank()) project.name else "${project.name}\n${project.packageName}"
        }.toTypedArray()
        AlertDialog.Builder(requireContext())
            .setTitle(getString(R.string.creative_apply_title, recipe.title))
            .setItems(labels) { _, index -> applyToProject(recipe, projects[index]) }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun applyToProject(recipe: CreativeRecipe, project: ProjectInfo) {
        if (applyingRecipeId != null) return
        val api = AgentApi(prefs.serverUrl, prefs.apiToken)
        applyingRecipeId = recipe.id
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val result = withContext(Dispatchers.IO) {
                    val conversation = api.createConversation(
                        projectId = project.id,
                        title = "应用创意：${recipe.title}",
                    )
                    val job = api.askConversation(
                        conversationId = conversation.id,
                        prompt = recipe.buildAgentPrompt(),
                    )
                    conversation to job
                }
                applyingRecipeId = null
                prefs.selectedProjectId = project.id
                prefs.selectedConversationId = result.first.id
                ConversationActivity.start(
                    context = requireContext(),
                    projectId = project.id,
                    conversationId = result.first.id,
                    title = result.first.title,
                    jobId = result.second.id,
                )
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                applyingRecipeId = null
                toast(e.message ?: getString(R.string.creative_apply_failed))
            }
        }
    }

    private fun toast(message: String) {
        context?.let { Toast.makeText(it, message, Toast.LENGTH_SHORT).show() }
    }
}
