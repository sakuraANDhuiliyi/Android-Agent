package com.androidagent.client

import org.json.JSONObject

/**
 * Device-local "always allow" fingerprints.
 * High-risk / destructive approvals can never be remembered.
 */
class ApprovalAllowlist(
    private val stored: MutableSet<String>,
) {
    fun allows(kind: String, payload: JSONObject): Boolean =
        fingerprint(kind, payload) in stored

    fun remember(kind: String, payload: JSONObject) {
        stored += fingerprint(kind, payload)
    }

    fun snapshot(): Set<String> = stored.toSet()

    companion object {
        fun canRemember(risk: String?, kind: String): Boolean =
            !UiFormat.isDestructive(risk, kind)

        fun fingerprint(kind: String, payload: JSONObject): String {
            val intent = UiFormat.approvalIntent(payload, kind).trim()
            return "$kind|$intent"
        }
    }
}
