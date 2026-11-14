package com.fleetdoctor.engine

/** 짧은 대답(응·아니)을 알아듣는다. 못 알아들으면 null. "괜찮아요"는 거절로 본다. */
object Answers {
    private val NO = listOf("아니", "괜찮", "됐어", "됐습니다", "싫어", "나중", "그만", "no")
    private val YES = listOf("응", "네", "예", "그래", "좋아", "넣어", "부탁", "yes", "ok")
    private val SPLIT = Regex("[^\\p{L}\\p{N}]+")

    fun parse(text: String?): Boolean? {
        val t = text?.trim()?.lowercase()?.takeIf { it.isNotEmpty() } ?: return null
        if (NO.any { it in t }) return false
        val words = t.split(SPLIT).filter { it.isNotEmpty() }
        if (words.any { w -> YES.any { w.startsWith(it) } }) return true
        return null
    }
}
