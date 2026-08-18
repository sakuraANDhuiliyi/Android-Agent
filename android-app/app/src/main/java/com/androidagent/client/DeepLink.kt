package com.androidagent.client

import android.content.Context
import android.content.Intent

/** Notification / intent extras shared by launcher routing and conversation. */
object DeepLink {
    const val EXTRA_PROJECT_ID = "project_id"
    const val EXTRA_CONVERSATION_ID = "conversation_id"
    const val EXTRA_CONVERSATION_TITLE = "conversation_title"
    const val EXTRA_JOB_ID = "job_id"
    const val EXTRA_TAB = "tab"
    const val EXTRA_EDIT_CONNECTION = "edit_connection"
    const val TAB_APPROVALS = "approvals"

    fun conversationIntent(
        context: Context,
        projectId: String,
        conversationId: String,
        title: String,
        jobId: String? = null,
    ): Intent =
        Intent(context, ConversationActivity::class.java)
            .putExtra(EXTRA_PROJECT_ID, projectId)
            .putExtra(EXTRA_CONVERSATION_ID, conversationId)
            .putExtra(EXTRA_CONVERSATION_TITLE, title)
            .putExtra(EXTRA_JOB_ID, jobId)

    fun mainNavIntent(context: Context, tab: String? = null): Intent =
        Intent(context, MainNavActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
            .putExtra(EXTRA_TAB, tab)

    fun hasConversationTarget(projectId: String?, conversationId: String?): Boolean =
        !projectId.isNullOrBlank() && !conversationId.isNullOrBlank()

    fun hasConversationTarget(intent: Intent): Boolean =
        hasConversationTarget(
            intent.getStringExtra(EXTRA_PROJECT_ID),
            intent.getStringExtra(EXTRA_CONVERSATION_ID),
        )
}
