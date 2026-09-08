package com.androidagent.client.core.agent

/**
 * Conversation feature 需要的会话级持久状态（实现方：AgentPrefs）。
 * 抽出接口是为了让 ViewModel 可脱离 Android 框架做单测。
 */
interface ConversationSessionPrefs {
    var selectedJobId: String?
    var selectedProviderId: String
    val guestMode: Boolean
    var guestRemaining: Int
    fun eventCursor(jobId: String): Long
    fun setEventCursor(jobId: String, cursor: Long)
}
