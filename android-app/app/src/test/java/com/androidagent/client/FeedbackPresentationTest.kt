package com.androidagent.client

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class FeedbackPresentationTest {
    @Test fun `absent metrics do not imply zero warnings or passing tests`() {
        val view = FeedbackPresentation.from(JSONObject())
        assertEquals("—", view.warnings)
        assertEquals("时间未知 · 耗时未知", view.duration)
        assertEquals("—", view.tasks)
        assertEquals("单测结果未知 · 输入关联未知\n时间未知 · 耗时未知", view.tests)
        assertEquals("—", view.artifact)
    }

    @Test fun `partial task counters keep missing values unknown`() {
        val view = FeedbackPresentation.from(JSONObject("""{"build":{"tasks":{"executed":38},"warnings":0},"tests":{"tests":{"reported":true,"passed":27}}}"""))
        assertEquals("0", view.warnings)
        assertTrue(view.tasks.contains("38 已执行"))
        assertTrue(view.tasks.contains("— 已缓存"))
        assertTrue(view.tests.contains("结果未知"))
        assertFalse(view.tests.contains("27 通过"))
    }

    @Test fun `unreported tests do not imply success and missing artifact size is not zero MB`() {
        val view = FeedbackPresentation.from(JSONObject("""{"tests":{"status":"success"},"artifact":{"name":"app-debug.apk"}}"""))
        assertTrue(view.tests.contains("结果未知"))
        assertEquals("app-debug.apk", view.artifact)
    }
}
