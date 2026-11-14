package com.fleetdoctor.platform

import android.content.Context
import android.media.AudioAttributes
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap

/**
 * 안드로이드 기본 음성(mock 빌드). 길안내 음성 용도로 재생해 음악 소리를 잠시 줄이게 한다.
 * 준비되기 전에 온 말은 모아 뒀다가 준비되면 말한다.
 */
class AndroidVoice(context: Context) : TextToSpeech.OnInitListener {
    private val tts = TextToSpeech(context.applicationContext, this)
    private val main = Handler(Looper.getMainLooper())
    private val done = ConcurrentHashMap<String, () -> Unit>()
    private val early = mutableListOf<Pair<String, () -> Unit>>()
    @Volatile private var ready = false

    override fun onInit(status: Int) {
        if (status != TextToSpeech.SUCCESS) {
            Log.w(TAG, "tts init failed: $status")
            return
        }
        val lang = tts.setLanguage(Locale.KOREAN)
        if (lang == TextToSpeech.LANG_MISSING_DATA || lang == TextToSpeech.LANG_NOT_SUPPORTED) {
            Log.w(TAG, "tts: Korean voice missing ($lang)")
        }
        tts.setAudioAttributes(
            AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ASSISTANCE_NAVIGATION_GUIDANCE)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build()
        )
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String) = Unit
            override fun onDone(utteranceId: String) = finish(utteranceId)
            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String) = finish(utteranceId)
        })
        ready = true
        val pending = synchronized(early) { early.toList().also { early.clear() } }
        main.post { pending.forEach { (text, cb) -> speak(text, cb) } }
    }

    fun speak(text: String, onDone: () -> Unit) {
        Log.i(TAG, "speak: $text")
        if (!ready) {
            synchronized(early) { early.add(text to onDone) }
            return
        }
        val id = "fd-${System.nanoTime()}"
        done[id] = onDone
        if (tts.speak(text, TextToSpeech.QUEUE_ADD, null, id) != TextToSpeech.SUCCESS) finish(id)
    }

    fun shutdown() = tts.shutdown()

    private fun finish(id: String) {
        done.remove(id)?.let { cb -> main.post(cb) }
    }

    private companion object {
        const val TAG = "FleetDoctor"
    }
}
