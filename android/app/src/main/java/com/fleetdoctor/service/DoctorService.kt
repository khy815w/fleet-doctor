package com.fleetdoctor.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import com.fleetdoctor.BuildConfig
import com.fleetdoctor.DoctorState
import com.fleetdoctor.engine.Answers
import com.fleetdoctor.engine.DoctorEngine
import com.fleetdoctor.engine.TripResult
import com.fleetdoctor.health.HealthConfig
import com.fleetdoctor.net.FleetClient
import com.fleetdoctor.platform.CarPlatform
import com.fleetdoctor.platform.PlatformFactory
import com.fleetdoctor.trip.DriveSample
import com.fleetdoctor.trip.Pass
import com.fleetdoctor.trip.PassBuilder
import com.fleetdoctor.trip.TripSplitter
import com.fleetdoctor.world.Geo
import com.fleetdoctor.world.World
import org.json.JSONObject
import java.io.File
import java.util.concurrent.Executors

/**
 * 주행 중 계속 도는 포그라운드 서비스.
 *
 * 표본(위치 + 핸들 각도 + 에너지) → 구간 맞추기 → 구간 값(Pass) 모으기
 * 주행이 끝나면(3분 서 있으면) → 익명 보고 → 길 몫 받기 → 판단 → 새 판단이면 음성으로 알리고 경유지를 묻는다
 *
 * 판단과 서버 통신은 일꾼 스레드 하나에서만 한다. 표본 처리와 음성은 메인 스레드에서 한다.
 */
class DoctorService : Service() {
    private lateinit var platform: CarPlatform
    private lateinit var engine: DoctorEngine
    private val api = FleetClient(BuildConfig.SERVER_URL)
    private val worker = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private val splitter = TripSplitter()
    private val builder = PassBuilder { world }
    private val tripPasses = mutableListOf<Pass>()

    @Volatile private var world: World? = null
    private val worldParts = mutableListOf<JSONObject>()
    private val fetchedCells = HashSet<String>()
    private var fetchingWorld = false
    private var lastWorldTry = 0L
    private var lastSampleAt = 0L
    private var lastFix: DriveSample? = null
    private var ready = false

    private val tick = object : Runnable {
        override fun run() {
            // 표본이 끊긴 채로 시간이 지나면(시동을 껐다) 그 주행을 끝낸다
            if (splitter.inTrip && SystemClock.elapsedRealtime() - lastSampleAt > HealthConfig.TRIP_GAP_S * 1000) {
                splitter.end()
                closeTrip()
            }
            main.postDelayed(this, 10_000)
        }
    }

    override fun onCreate() {
        super.onCreate()
        startInForeground()
        instance = this
        platform = PlatformFactory.create(this, BuildConfig.SERVER_URL)
        worker.execute {
            engine = DoctorEngine.open(api, File(filesDir, "health.json"), BuildConfig.MODEL_GROUP, BuildConfig.POWERTRAIN)
            DoctorState.connected = api.ping()
            if (BuildConfig.DEMO && engine.isEmpty) importDemoHistory()
            publish(null)
            main.post {
                ready = true
                platform.start(::onSample, ::onRequest)
            }
        }
        main.postDelayed(tick, 10_000)
    }

    override fun onDestroy() {
        instance = null
        main.removeCallbacks(tick)
        if (ready) platform.stop()
        worker.shutdown()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ── 표본 ────────────────────────────────────────────────────
    private fun onSample(s: DriveSample) {
        lastSampleAt = SystemClock.elapsedRealtime()
        lastFix = s
        ensureWorld(s.lat, s.lon)
        if (splitter.update(s.t, s.speedKmh)) closeTrip()
        if (splitter.inTrip) {
            builder.update(s)?.let { p ->
                tripPasses.add(p)
                Log.i(TAG, "pass ${p.segment} steer=${p.steer?.let { "%.2f".format(it) }} eff=${p.eff?.let { "%.4f".format(it) }}")
            }
        }
        val seg = builder.current
        if (seg != DoctorState.segment || tripPasses.size != DoctorState.tripPasses) {
            DoctorState.segment = seg
            DoctorState.tripPasses = tripPasses.size
            DoctorState.changed()
        }
    }

    /** 지금 칸(약 5km)과 주변 8칸의 구간 목록을 받아 둔다. 칸을 옮기면 더 받는다. */
    private fun ensureWorld(lat: Double, lon: Double) {
        val cell = Geo.geohash(lat, lon, 5)
        if (cell in fetchedCells || fetchingWorld) return
        val now = SystemClock.elapsedRealtime()
        if (now - lastWorldTry < 10_000) return
        lastWorldTry = now
        fetchingWorld = true
        val cells = Geo.neighbors(cell)
        worker.execute {
            try {
                val part = api.world(cells)
                main.post {
                    worldParts.add(part)
                    fetchedCells.addAll(cells)
                    world = World.fromJson(worldParts)
                    fetchingWorld = false
                    DoctorState.connected = true
                    Log.i(TAG, "world: ${world?.segments?.size} segments")
                }
            } catch (e: Exception) {
                Log.w(TAG, "world fetch failed: ${e.message}")
                main.post {
                    fetchingWorld = false
                    DoctorState.connected = false
                    DoctorState.changed()
                }
            }
        }
    }

    // ── 주행 끝 ─────────────────────────────────────────────────
    private fun closeTrip() {
        builder.flush()?.let { tripPasses.add(it) }
        val passes = tripPasses.toList()
        tripPasses.clear()
        DoctorState.tripPasses = 0
        if (passes.isEmpty()) return
        val day = HealthConfig.dayOf(System.currentTimeMillis() / 1000.0)
        worker.execute {
            val r = engine.onTripEnd(passes, day)
            Log.i(TAG, "trip ${r.trip} day=$day passes=${r.passes} reported=${r.reported} waiting=${r.waiting} " +
                "finding=${r.finding?.kind} new=${r.isNew} speech=${engine.health.speech()}")
            publish(r)
            if (r.isNew) {
                val text = engine.health.speech()
                main.post { announce(text) }
            }
        }
    }

    /** 새 판단: 알리고, 가까운 정비소를 경유지로 넣을지 묻는다. */
    private fun announce(text: String) {
        platform.speak(text)
        val fix = lastFix ?: return
        val sp = world?.nearestServicePoint(fix.lat, fix.lon) ?: return
        val question = "가까운 정비소를 경유지로 넣을까요?"
        platform.speak(question) {
            DoctorState.dialog = "묻는 중: \"$question\""
            DoctorState.changed()
            platform.listen { heard ->
                Log.i(TAG, "heard: $heard")
                DoctorState.dialog = heard?.let { "들은 대답: \"$it\"" }
                DoctorState.changed()
                when (Answers.parse(heard)) {
                    true -> platform.addWaypoint(sp) { ok ->
                        if (ok) DoctorState.waypoint = sp.name
                        say(if (ok) "${sp.name}, 경유지로 넣었어요." else "경유지를 넣지 못했어요. 내비에서 직접 넣어 주세요.")
                    }
                    false -> say("알겠어요. 시간 날 때 점검해 보세요.")
                    null -> Unit
                }
            }
        }
    }

    // ── 요청 ────────────────────────────────────────────────────
    private fun onRequest(what: String) {
        when (what) {
            "ask" -> worker.execute {
                engine.retry()?.let { publish(it) }
                val text = engine.health.speech()
                main.post { say(text) }
            }
            "serviced" -> worker.execute {
                engine.serviced()
                DoctorState.waypoint = null
                DoctorState.dialog = null
                publish(null)
                main.post { say("알겠어요. 정비한 뒤의 상태를 오늘부터 다시 배울게요.") }
            }
        }
    }

    private fun say(text: String) {
        DoctorState.speech = text
        DoctorState.changed()
        platform.speak(text)
    }

    private fun importDemoHistory() {
        try {
            val h = api.demoHistory() ?: return
            if (h.getString("model") != BuildConfig.MODEL_GROUP) return
            val passes = DoctorEngine.parseHistory(h)
            engine.importHistory(passes)
            Log.i(TAG, "demo history: ${passes.size} passes, last trip ${engine.lastTrip}, learned=${engine.health.learned}")
        } catch (e: Exception) {
            Log.w(TAG, "demo history failed: ${e.message}")
        }
    }

    /** 일꾼 스레드에서 부른다. */
    private fun publish(r: TripResult?) {
        val h = engine.health
        val f = h.finding()
        DoctorState.status = when {
            f != null -> DoctorState.Status.CHECK
            !h.learned -> DoctorState.Status.LEARNING
            else -> DoctorState.Status.OK
        }
        DoctorState.learning = DoctorEngine.learningLine(h)
        DoctorState.speech = h.speech()
        if (r != null) {
            DoctorState.connected = r.waiting == 0
            DoctorState.lastTrip = "지난 주행: 구간 ${r.passes}개" + if (r.waiting > 0) " · 서버 연결을 기다리는 중" else ""
        }
        DoctorState.changed()
    }

    fun ask() = onRequest("ask")

    fun serviced() = onRequest("serviced")

    private fun startInForeground() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "플릿 닥터", NotificationManager.IMPORTANCE_LOW))
        val notification = Notification.Builder(this, CHANNEL)
            .setContentTitle("플릿 닥터")
            .setContentText("핸들 쏠림과 에너지 쓰는 양을 지켜보고 있어요")
            .setSmallIcon(android.R.drawable.ic_menu_compass)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    companion object {
        private const val TAG = "FleetDoctor"
        private const val CHANNEL = "doctor"
        private const val NOTIFICATION_ID = 1
        @Volatile var instance: DoctorService? = null
            private set
    }
}
