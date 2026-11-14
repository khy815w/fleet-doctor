package com.fleetdoctor.platform

import ai.pleos.playground.location.fused.FusedLocation
import ai.pleos.playground.location.fused.listener.FusedLocationListener
import ai.pleos.playground.location.fused.model.LocationData
import ai.pleos.playground.navi.constants.WaypointIndex
import ai.pleos.playground.navi.data.RequestWaypointInfo
import ai.pleos.playground.navi.helper.NaviHelper
import ai.pleos.playground.stt.SpeechToText
import ai.pleos.playground.stt.listener.ResultListener
import ai.pleos.playground.tts.TextToSpeech
import ai.pleos.playground.tts.listener.EventListener
import ai.pleos.playground.tts.listener.OnInitializeListener
import ai.pleos.playground.vehicle.Vehicle
import ai.pleos.playground.vehicle.listener.EvBatteryEfficiencyListener
import ai.pleos.playground.vehicle.listener.FuelEconomyListener
import ai.pleos.playground.vehicle.listener.SteeringWheelAngleListener
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.fleetdoctor.health.HealthConfig
import com.fleetdoctor.trip.DriveSample
import com.fleetdoctor.world.ServicePoint
import ai.pleos.playground.stt.constant.ErrorType as SttError
import ai.pleos.playground.stt.constant.Mode as SttMode
import ai.pleos.playground.stt.listener.OnInitializeListener as SttInitListener
import ai.pleos.playground.tts.constant.ErrorType as TtsError
import ai.pleos.playground.tts.constant.Mode as TtsMode

/**
 * Pleos Connect SDK로 차와 이어진다(SDK 파일에서 확인한 서명 기준: Vehicle 2.3.2, FusedLocation 1.1.3,
 * TextToSpeech·SpeechToText 3.3.1, NaviHelper 3.0.5).
 *
 * - 핸들: SteeringWheel.registerSteeringWheelAngle(CAR_DRIVE). 앞바퀴 각도라서 기준 조향비 15를 곱한다.
 * - 에너지: 전기차는 EvBattery 순간 전비(km/kWh → 1/값), 내연·하이브리드는 Fuel 순간 연비(L/100km → 값/100). CAR_ENERGY
 * - 위치: FusedLocation(차량 센서와 합친 위치). 속도는 주지 않으므로 지난 위치들로 구한다.
 * - 차종마다 지원이 다르므로 checkXXXCapabilities가 실패하면 그 신호 없이 돈다(그 지표만 판단하지 않는다).
 * - 결과는 IPC 비동기 콜백으로 오므로, 콜백이 끝나기 전에 release()를 부르지 않는다.
 *
 * 컴파일만 확인했다. Pleos Connect 기기에서는 아직 시험하지 못했다.
 */
class PleosPlatform(private val context: Context, private val powertrain: String) : CarPlatform {
    private val main = Handler(Looper.getMainLooper())
    private val vehicle = Vehicle(context)
    private val fused = FusedLocation(context)
    private val tts = TextToSpeech(context, TtsMode.ON_DEVICE)
    private val stt = SpeechToText(context, SttMode.ON_DEVICE)
    private val navi = NaviHelper(context)
    private val speed = SpeedEstimator()

    @Volatile private var frontWheelDeg: Double? = null
    @Volatile private var perKm: Double? = null
    private var angleListener: SteeringWheelAngleListener? = null
    private var evListener: EvBatteryEfficiencyListener? = null
    private var fuelListener: FuelEconomyListener? = null
    private var locationListener: FusedLocationListener? = null
    private val speakQueue = ArrayDeque<Pair<String, () -> Unit>>()
    private var speaking = false
    private var onHeard: ((String?) -> Unit)? = null

    override fun start(onSample: (DriveSample) -> Unit, onRequest: (String) -> Unit) {
        vehicle.initialize()
        startSteering()
        if (powertrain == "ev") startEvEfficiency() else startFuelEconomy()

        fused.initialize()
        val ll = object : FusedLocationListener {
            override fun onChanged(data: LocationData?) {
                val d = data ?: return
                val t = d.timestampMillis / 1000.0
                val v = speed.update(t, d.latitude, d.longitude)
                val wheel = frontWheelDeg?.let { it * HealthConfig.STEER_RATIO_NOMINAL }
                val s = DriveSample(t, d.latitude, d.longitude, d.heading.toDouble(), v, wheel, perKm)
                main.post { onSample(s) }
            }

            override fun onError(e: Exception) {
                Log.w(TAG, "fused location: ${e.message}")
            }
        }
        locationListener = ll
        fused.registerFusedLocationCallback(ll)

        tts.initialize(object : OnInitializeListener {
            override fun onSuccess() {
                Log.i(TAG, "tts ready")
            }
            override fun onFailure(type: TtsError) {
                Log.w(TAG, "tts init failed: $type")
            }
        })
        tts.addEventListener(object : EventListener {
            override fun onStart() = Unit
            override fun onReady() = Unit
            override fun onStop() = Unit

            override fun onDone() {
                main.post { nextSpeech() }
            }

            override fun onError(type: TtsError) {
                Log.w(TAG, "tts error: $type")
                main.post { nextSpeech() }
            }

            override fun onUpdatedRms(rms: Double) = Unit
        })

        stt.initialize(object : SttInitListener {
            override fun onSuccess() {
                Log.i(TAG, "stt ready")
            }
            override fun onFailure(type: SttError) {
                Log.w(TAG, "stt init failed: $type")
            }
        })
        stt.addListener(object : ResultListener {
            override fun onUpdated(text: String, completed: Boolean) {
                if (completed) main.post { deliver(text) }
            }

            override fun onError(errorType: SttError) {
                Log.w(TAG, "stt error: $errorType")
                main.post { deliver(null) }
            }

            override fun onUpdatedEpdData(on: Long, off: Long) = Unit
            override fun onStartedRecognition() = Unit
            override fun onEndedRecognition() = Unit
            override fun onReady() = Unit
            override fun onUpdatedRms(rms: Double) = Unit
        })

        navi.initialize({ Log.i(TAG, "navi ready") }, { e -> Log.w(TAG, "navi init failed: ${e.message}") })
    }

    private fun startSteering() {
        val wheel = vehicle.getSteeringWheel()
        wheel.checkSteeringWheelAngleCapabilities({ _, _ ->
            val l = object : SteeringWheelAngleListener {
                override fun onSteeringWheelAngleUpdated(angle: Float?) {
                    frontWheelDeg = angle?.toDouble()
                }

                override fun onFailed(e: Exception) {
                    frontWheelDeg = null
                }
            }
            angleListener = l
            wheel.registerSteeringWheelAngle(l)
        }, { e -> Log.w(TAG, "steering angle not supported: ${e.message}") })
    }

    private fun startEvEfficiency() {
        val ev = vehicle.getEvBattery()
        ev.checkInstantaneousEvEfficiencyCapabilities({ _ ->
            val l = object : EvBatteryEfficiencyListener {
                override fun onEvBatteryEfficiencyUpdated(value: Float?) {   // km/kWh
                    perKm = value?.takeIf { it > 0f }?.let { 1.0 / it }
                }

                override fun onFailed(e: Exception) {
                    perKm = null
                }
            }
            evListener = l
            ev.registerInstantaneousEvEfficiency(l)
        }, { e -> Log.w(TAG, "ev efficiency not supported: ${e.message}") })
    }

    private fun startFuelEconomy() {
        val fuel = vehicle.getFuel()
        fuel.checkInstantaneousFuelEconomyCapabilities({ _ ->
            val l = object : FuelEconomyListener {
                override fun onFuelEconomyUpdated(value: Float?) {           // L/100km
                    perKm = value?.takeIf { it > 0f }?.let { it / 100.0 }
                }

                override fun onFailed(e: Exception) {
                    perKm = null
                }
            }
            fuelListener = l
            fuel.registerInstantaneousFuelEconomy(l)
        }, { e -> Log.w(TAG, "fuel economy not supported: ${e.message}") })
    }

    override fun stop() {
        angleListener?.let { vehicle.getSteeringWheel().unregisterSteeringWheelAngle(it) }
        evListener?.let { vehicle.getEvBattery().unregisterInstantaneousEvEfficiency(it) }
        fuelListener?.let { vehicle.getFuel().unregisterInstantaneousFuelEconomy(it) }
        locationListener?.let { fused.unregisterFusedLocationCallback(it) }
        vehicle.release()
        fused.release()
        tts.release()
        stt.release()
        navi.release()
    }

    /** 말은 차례로 한다. 앞의 말이 끝나야(onDone) 다음 말을 하고, 그 말의 onDone을 부른다. */
    override fun speak(text: String, onDone: () -> Unit) {
        Log.i(TAG, "speak: $text")
        speakQueue.addLast(text to onDone)
        if (!speaking) nextSpeech(first = true)
    }

    private fun nextSpeech(first: Boolean = false) {
        if (!first) speakQueue.removeFirstOrNull()?.second?.invoke()
        val next = speakQueue.firstOrNull()
        speaking = next != null
        if (next != null) tts.speak(next.first)
    }

    override fun listen(onText: (String?) -> Unit) {
        onHeard = onText
        stt.request()
        main.postDelayed({ if (onHeard === onText) { stt.stop(); deliver(null) } }, LISTEN_MS)
    }

    private fun deliver(text: String?) {
        val cb = onHeard ?: return
        onHeard = null
        cb(text)
    }

    override fun addWaypoint(point: ServicePoint, onResult: (Boolean) -> Unit) {
        try {
            navi.addWaypoint(RequestWaypointInfo(point.lat, point.lon, point.name, WaypointIndex.FIRST, point.id, "", "", ""))
            onResult(true)
        } catch (e: Exception) {
            Log.w(TAG, "waypoint failed: ${e.message}")
            onResult(false)
        }
    }

    private companion object {
        const val TAG = "FleetDoctor"
        const val LISTEN_MS = 8_000L
    }
}
