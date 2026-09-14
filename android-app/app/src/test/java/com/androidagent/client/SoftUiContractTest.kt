package com.androidagent.client

import com.androidagent.client.theokit.TheoTokens
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import kotlin.math.pow

class SoftUiContractTest {
    private fun contrast(a: Int, b: Int): Double {
        fun luminance(color: Int): Double {
            fun channel(shift: Int): Double {
                val c = ((color shr shift) and 255) / 255.0
                return if (c <= .04045) c / 12.92 else ((c + .055) / 1.055).pow(2.4)
            }
            return .2126 * channel(16) + .7152 * channel(8) + .0722 * channel(0)
        }
        val x = luminance(a); val y = luminance(b)
        return (maxOf(x, y) + .05) / (minOf(x, y) + .05)
    }

    @Test fun `reading and primary action colors remain accessible in both themes`() {
        listOf(TheoTokens.LIGHT, TheoTokens.DARK).forEach { p ->
            assertTrue(contrast(p.foreground, p.background) >= 4.5)
            assertTrue(contrast(p.mutedForeground, p.background) >= 4.5)
            assertTrue(contrast(p.primary, p.primaryForeground) >= 4.5)
            assertTrue(contrast(p.cardForeground, p.card) >= 4.5)
        }
    }

    @Test fun `XML and dynamic components share the same core palette`() {
        listOf("values" to TheoTokens.LIGHT, "values-night" to TheoTokens.DARK).forEach { (folder, p) ->
            val xml = File("src/main/res/$folder/colors.xml").readText()
            fun color(name: String): Int = Regex("<color name=\"$name\">#([A-Fa-f0-9]+)</color>")
                .find(xml)!!.groupValues[1].toLong(16).toInt()
            assertEquals(p.background, color("signal_surface"))
            assertEquals(p.foreground, color("signal_on_surface"))
            assertEquals(p.primary, color("signal_primary"))
            assertEquals(p.primaryForeground, color("signal_on_primary"))
            assertEquals(p.card, color("projects_card_surface"))
        }
    }

    @Test fun `composer keeps wrapping context and a full width text editor`() {
        val xml = File("src/main/res/layout/activity_conversation.xml").readText()
        assertTrue(xml.contains("app:singleLine=\"false\""))
        assertTrue(xml.contains("app:boxBackgroundMode=\"none\""))
        assertTrue(xml.indexOf("@+id/inputPrompt") < xml.indexOf("@+id/btnAddContext"))
    }
}
