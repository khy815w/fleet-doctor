package com.fleetdoctor.platform

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.core.content.ContextCompat
import com.fleetdoctor.health.HealthConfig
import com.fleetdoctor.trip.DriveSample
import com.fleetdoctor.world.ServicePoint
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * 시연용 차(mock 빌드). Pleos SDK 없이 일반 에뮬레이터에서 돈다.
 *
 * - 차량 신호: simulator/drive_emulator.py가 시연 서버 통로(POST /v1/demo/feed)에 쌓은 표본을 받아 온다.
 *   표본의 시각은 시연 시계라서 15km 주행을 1분 안에 돌려 볼 수 있다. 값의 꼴은 Pleos SDK와 같다
 *   (앞바퀴 각도 → 기준 조향비 15를 곱해 핸들 각도로).
 * - 음성: 안드로이드 기본 음성으로 말하고, 대답은 방송으로 흉내 낸다(docs/protocol.md).
 * - 경유지: 기록만 남긴다.
 */
class DemoPlatform(private val context: Context, baseUrl: String) : CarPlatform {
    private val base = baseUrl.trimEnd('/')
    private val http = OkHttpClient.Builder().connectTimeout(5, TimeUnit.SECONDS).readTimeout(10, TimeUnit.SECONDS).build()
    private val main = Handler(Looper.getMainLooper())
    private val voice = AndroidVoice(context)
    @Volatile private var running = false
    private var onRequest: ((String) -> Unit)? = null
    private var onAnswer: ((String?) -> Unit)? = null

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context, intent: Intent) {
            Log.i(TAG, "broadcast ${intent.action}")
            when (intent.action) {
                ACTION_ASK -> onRequest?.invoke("ask")
                ACTION_SERVICED -> onRequest?.invoke("serviced")
                ACTION_ANSWER -> {
                    val cb = onAnswer
                    onAnswer = null
                    cb?.invoke(intent.getStringExtra("text"))
                }
            }
        }
    }

    override fun start(onSample: (DriveSample) -> Unit, onRequest: (String) -> Unit) {
        this.onRequest = onRequest
        val filter = IntentFilter().apply {
            addAction(ACTION_ASK)
            addAction(ACTION_ANSWER)
            addAction(ACTION_SERVICED)
        }
        ContextCompat.registerReceiver(context, receiver, filter, ContextCompat.RECEIVER_EXPORTED)
        running = true
        Thread({ poll(onSample) }, "demo-feed").apply { isDaemon = true }.start()
    }

    override fun stop() {
        running = false
        context.unregisterReceiver(receiver)
        voice.shutdown()
    }

    override fun speak(text: String, onDone: () -> Unit) = voice.speak(text, onDone)

    override fun listen(onText: (String?) -> Unit) {
        Log.i(TAG, "listening (send $ACTION_ANSWER)")
        onAnswer = onText
        main.postDelayed({
            if (onAnswer === onText) {
                onAnswer = null
                onText(null)
            }
        }, LISTEN_MS)
    }

    override fun addWaypoint(point: ServicePoint, onResult: (Boolean) -> Unit) {
        Log.i(TAG, "waypoint: ${point.name} (${point.lat}, ${point.lon})")
        onResult(true)
    }

    private fun poll(onSample: (DriveSample) -> Unit) {
        var after = -1L                      // 처음에는 지난 신호를 건너뛰고 마지막 번호만 받는다
        while (running) {
            try {
                val req = Request.Builder().url("$base/v1/demo/feed?after=$after&limit=500").build()
                val body = http.newCall(req).execute().use { if (it.isSuccessful) it.body!!.string() else null }
                if (body == null) {
                    Thread.sleep(1000)
                    continue
                }
                val o = JSONObject(body)
                val arr = o.getJSONArray("samples")
                if (after < 0) after = o.getLong("last")
                for (i in 0 until arr.length()) {
                    val s = arr.getJSONObject(i)
                    after = s.getLong("seq")
                    val sample = DriveSample(
                        t = s.getDouble("t"),
                        lat = s.getDouble("lat"),
                        lon = s.getDouble("lon"),
                        heading = s.getDouble("heading"),
                        speedKmh = s.getDouble("speed_kmh"),
                        wheelDeg = if (s.isNull("wheel_deg")) null else s.getDouble("wheel_deg") * HealthConfig.STEER_RATIO_NOMINAL,
                        perKm = if (s.isNull("consumption")) null else s.getDouble("consumption"),
                    )
                    main.post { onSample(sample) }
                }
                if (arr.length() == 0) Thread.sleep(200)
            } catch (e: Exception) {
                Log.w(TAG, "demo feed: ${e.message}")
                Thread.sleep(1000)
            }
        }
    }

    companion object {
        private const val TAG = "FleetDoctor"
        private const val LISTEN_MS = 20_000L
        const val ACTION_ASK = "com.fleetdoctor.ASK"
        const val ACTION_ANSWER = "com.fleetdoctor.ANSWER"
        const val ACTION_SERVICED = "com.fleetdoctor.SERVICED"
    }
}
