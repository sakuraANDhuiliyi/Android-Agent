package com.androidagent.client

import org.junit.Assert.*
import org.junit.Test

class DeliveryVerificationPresentationTest {
    @Test fun `execution results retain their history without claiming unknown or changed inputs are verified`() {
        for (inputs in VerificationInputs.entries) {
            val step = VerificationStep(VerificationState.PASSED, inputs, 1_700_000_000.0)
            val view = DeliveryVerificationPresentation.from(DeliveryVerification(step, step), "succeeded")
            assertEquals(inputs == VerificationInputs.MATCH, view.buildPassed)
            when (inputs) {
                VerificationInputs.MATCH -> assertTrue(view.build.contains("与任务结束时输入一致"))
                VerificationInputs.CHANGED -> assertTrue(view.build.contains("此前执行通过 · 验证后输入已变化"))
                VerificationInputs.UNKNOWN -> assertTrue(view.build.contains("此前执行通过 · 输入关联未知"))
            }
            assertFalse(view.summary(true, 1).contains("当前代码已验证"))
            assertTrue(view.title.startsWith("本任务最近一次验证 · "))
        }
    }

    @Test fun `zero tests all skipped and uncompleted executions never receive a passing label`() {
        for (state in VerificationState.entries.filter { it != VerificationState.PASSED }) {
            val step = VerificationStep(state, VerificationInputs.MATCH)
            val view = DeliveryVerificationPresentation.from(DeliveryVerification(step, step))
            assertFalse("$state must not be green", view.buildPassed)
            assertFalse(view.tests.contains("通过"))
            assertEquals(state == VerificationState.FAILED, view.buildFailed)
        }
    }

    @Test fun `cancel and interruption do not turn a previous successful command into completed delivery`() {
        for (status in listOf("canceled", "cancel_requested", "interrupted")) {
            val step = VerificationStep(VerificationState.PASSED, VerificationInputs.MATCH)
            val view = DeliveryVerificationPresentation.from(DeliveryVerification(step, step), status)
            assertFalse(view.buildPassed)
            assertTrue(view.build.contains("此前执行通过"))
            assertTrue(view.build.contains("任务未完成"))
        }
    }

    @Test fun `an available APK never implies installation and unknown evidence stays explicit`() {
        val view = DeliveryVerificationPresentation.from(DeliveryVerification())
        assertEquals("本任务最近一次验证 · 时间未知", view.title)
        for (apk in listOf(true, false)) {
            val text = view.summary(apk, 2)
            assertTrue(text.contains("安装未验证"))
            assertTrue(text.contains("2 个文件可审阅"))
            assertFalse(text.contains("安装成功"))
            assertFalse(view.buildPassed)
        }
    }

    @Test fun `active and paused evidence only describes inputs at verification time`() {
        val step = VerificationStep(VerificationState.PASSED, VerificationInputs.MATCH)
        for (status in listOf("running", "paused", "queued", "awaiting_approval", null)) {
            val view = DeliveryVerificationPresentation.from(DeliveryVerification(step, step), status)
            assertTrue(view.build.contains("验证时输入一致"))
            assertFalse(view.build.contains("任务结束"))
        }
    }
}
