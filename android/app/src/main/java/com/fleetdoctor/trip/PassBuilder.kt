package com.fleetdoctor.trip

import com.fleetdoctor.health.HealthConfig
import com.fleetdoctor.health.adjustEff
import com.fleetdoctor.world.Segment
import com.fleetdoctor.world.World
import kotlin.math.abs
import kotlin.math.sqrt

/**
 * 한 순간의 위치와 차량 신호.
 * @param t 초. 실제 차는 위치 시각, 시연(mock)은 시연 시계라서 실제보다 빨리 갈 수 있다
 * @param wheelDeg 핸들 각도(도, 오른쪽으로 돌린 쪽이 +). 모르면 null
 * @param perKm 지금 1km당 에너지(kWh/km 또는 L/km). 모르면 null
 */
data class DriveSample(
    val t: Double,
    val lat: Double,
    val lon: Double,
    val heading: Double,
    val speedKmh: Double,
    val wheelDeg: Double?,
    val perKm: Double?,
)

/** 구간 하나를 끝까지 지난 결과. 핸들 값은 똑바른 구간에서만, 둘 다 일정한 속도였을 때만 있다. */
data class Pass(val segment: String, val steer: Double?, val eff: Double?) {
    fun value(metric: String): Double? = if (metric == HealthConfig.STEER) steer else eff
}

/**
 * 위치를 구간에 맞춰 가며, 구간 하나를 다 지날 때마다 그 구간의 값(Pass)을 만든다.
 * 구간 길이의 70% 이상을 지났고, 평균 속도가 기준 속도 ±15km/h 안이며 속도 흔들림이 6km/h보다 작을 때만 쓴다.
 * 신호·정체로 서고 가기를 되풀이한 구간은 길 몫과 내 차 몫을 가르기 어려워 버린다.
 * 값은 구간 양 끝 150m를 뺀 가운데에서만 잰다. 교차로 앞뒤에서 줄이고 돌고 높이는 것까지 넣으면 거의 모든 구간이 버려진다.
 */
class PassBuilder(private val world: () -> World?) {
    private var seg: Segment? = null
    private var entryM = 0.0
    private var lastM = 0.0
    private val speeds = mutableListOf<Double>()
    private val wheels = mutableListOf<Double>()
    private val perKms = mutableListOf<Double>()

    /** 지금 지나고 있는 구간. */
    val current: String? get() = seg?.id

    /** 표본 하나를 넣는다. 구간 하나를 막 다 지났으면 그 결과를 돌려준다. */
    fun update(s: DriveSample): Pass? {
        val w = world() ?: return null
        val m = w.match(s.lat, s.lon, s.heading)
        if (m != null && m.segment.id == seg?.id) {
            add(s, m.alongM)
            return null
        }
        val done = finish()
        if (m != null) {
            seg = m.segment
            entryM = m.alongM
            add(s, m.alongM)
        }
        return done
    }

    /** 주행이 끝났다. 지나던 구간이 끝까지 가지 않았으면 버린다. */
    fun flush(): Pass? = finish()

    private fun add(s: DriveSample, alongM: Double) {
        lastM = alongM
        val len = seg?.lengthM ?: return
        if (alongM < HealthConfig.PASS_TRIM_M || alongM > len - HealthConfig.PASS_TRIM_M) return
        speeds.add(s.speedKmh)
        s.wheelDeg?.let { wheels.add(it) }
        s.perKm?.takeIf { it > 0.0 }?.let { perKms.add(it) }
    }

    private fun finish(): Pass? {
        val sg = seg ?: return null
        seg = null
        val result = evaluate(sg)
        speeds.clear()
        wheels.clear()
        perKms.clear()
        return result
    }

    private fun evaluate(sg: Segment): Pass? {
        if ((lastM - entryM) / sg.lengthM < HealthConfig.MIN_COVERAGE || speeds.size < 3) return null
        val mean = speeds.average()
        val sd = sqrt(speeds.sumOf { (it - mean) * (it - mean) } / speeds.size)
        if (abs(mean - sg.vRefKmh) > HealthConfig.STEADY_SPEED_TOL_KMH || sd > HealthConfig.STEADY_SPEED_SD_KMH) return null
        // 신호가 절반 넘게 비면 그 지표는 버린다
        val steer = if (sg.straight && wheels.size * 2 >= speeds.size) wheels.average() else null
        val eff = if (perKms.size * 2 >= speeds.size) adjustEff(perKms.average(), mean, sg.vRefKmh) else null
        return if (steer != null || eff != null) Pass(sg.id, steer, eff) else null
    }
}

/**
 * 주행 나누기. 이만큼(초) 서 있거나 표본이 그만큼 끊기면 주행 한 번이 끝난 것으로 본다.
 * 주행이 끝날 때마다 그 주행의 구간 값을 한꺼번에 판단하고 보고한다.
 */
class TripSplitter(private val gapS: Double = HealthConfig.TRIP_GAP_S) {
    private var lastT: Double? = null
    private var lastMoveT: Double? = null

    var inTrip = false
        private set

    /** 표본 하나를 넣고, 진행 중이던 주행이 이 표본 직전에 끝났으면 true. */
    fun update(t: Double, speedKmh: Double): Boolean {
        val prev = lastT
        lastT = t
        var ended = false
        if (inTrip && prev != null && t - prev >= gapS) {        // 시동을 껐다 켰다
            inTrip = false
            ended = true
        }
        if (speedKmh >= MOVING_KMH) {
            lastMoveT = t
            inTrip = true
        } else if (inTrip && t - (lastMoveT ?: t) >= gapS) {
            inTrip = false
            ended = true
        }
        return ended
    }

    /** 표본이 더 오지 않을 때(시동을 껐을 때) 밖에서 끝낸다. */
    fun end(): Boolean {
        val was = inTrip
        inTrip = false
        return was
    }

    private companion object {
        const val MOVING_KMH = 5.0
    }
}
