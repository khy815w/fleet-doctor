package com.fleetdoctor.health

import com.fleetdoctor.health.HealthConfig.EFF
import com.fleetdoctor.health.HealthConfig.STEER
import java.math.BigDecimal
import java.math.RoundingMode
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min
import kotlin.math.round
import kotlin.math.sqrt

/*
 * 차 안에서 도는 판단. 서버의 fleetdoc/carmodel.py(기준 구현)와 같은 계산을 같은 순서로 한다.
 * 두 구현은 시험 자료 carhealth.json 하나로 함께 시험한다(소수점 12자리까지 같아야 한다).
 *
 * 구간을 한 번 지날 때마다 지표별 값 y가 하나 나온다.
 * - steer: 똑바른 구간을 일정한 속도로 지날 때 핸들 각도 평균(도). 핸들을 오른쪽으로 돌린 쪽이 +
 * - eff:   그 구간에서 1km당 쓴 에너지의 자연로그(속도 보정). 0.04 차이 ≈ 4%
 * 서버가 주는 길 몫 r을 빼면 z = y − r 에는 '내 차 몫'만 남는다.
 */

/** 배우는 동안 모아 두는 통과 하나. */
data class Sample(val segment: String, val day: Int, val trip: Int, val y: Double)

/** 주행 한 번 안의 구간 하나: (구간, 날짜, 잰 값 y, 길 몫 r) */
data class TripItem(val segment: String, val day: Int, val y: Double, val r: Double)

/** 최근 주행 하나: (판단용 평균 편차, 구간 수, 크기용 평균 편차) */
data class RecentTrip(val m: Double, val n: Int, val mRaw: Double)

internal fun median(xs: List<Double>): Double {
    val s = xs.sorted()
    val n = s.size
    return if (n % 2 == 1) s[n / 2] else (s[n / 2 - 1] + s[n / 2]) / 2.0
}

internal fun clip(x: Double, lo: Double, hi: Double): Double = if (x < lo) lo else if (x > hi) hi else x

/** 지표 하나(steer 또는 eff)의 평소 값과 변화 감지. */
class MetricTracker(val metric: String) {
    val target = HealthConfig.TARGET.getValue(metric)
    val h = HealthConfig.CUSUM_H.getValue(metric)
    private val twoSided = metric == STEER      // 에너지는 '더 쓸 때'만 고장 신호다

    val learn = mutableListOf<Sample>()
    var baseline: Double? = null
    var sigma: Double? = null      // 주행 안 구간마다의 흔들림
    var tau: Double? = null        // 주행마다의 흔들림(다른 가족이 몰았다, 히터를 세게 틀었다)
    var learned = false
    var fitDay: Int? = null
    var sPos = 0.0
    var sNeg = 0.0
    val recent = mutableListOf<RecentTrip>()
    var alarmShift: Double? = null
    var calm = 0

    // ── 배우기 ──────────────────────────────────────────────────
    fun learnDays(): Int = learn.map { it.day }.toSet().size

    fun learnTrips(): Int = learn.map { it.trip }.toSet().size

    /** 다시 계산할 때 서버에 길 몫을 물어볼 (구간, 날짜) 목록. */
    fun learnKeys(): List<Pair<String, Int>> = learn.map { it.segment to it.day }

    fun needsRefit(day: Int): Boolean {
        if (!learned) return learn.size >= HealthConfig.REPORT_MIN_PASSES && fitDay != day
        val fd = fitDay ?: return true
        return day - fd >= HealthConfig.RECOMPUTE_EVERY_DAYS
    }

    /** 배운 통과들의 y와, 그 통과들의 최신 길 몫 rs로 평소 값과 흔들림을 다시 잡는다. */
    fun refit(rs: List<Double>, day: Int) {
        require(rs.size == learn.size && rs.isNotEmpty()) { "rs must match learning samples" }
        val floorS = HealthConfig.SIGMA_FLOOR.getValue(metric)
        val floorT = HealthConfig.TAU_FLOOR.getValue(metric)
        val z = learn.mapIndexed { i, s -> s.y - rs[i] }
        val med = median(z)
        val s0 = max(floorS, 1.4826 * median(z.map { abs(it - med) }))
        val lim = HealthConfig.CLIP_SIGMA * s0
        val zw = z.map { clip(it, med - lim, med + lim) }        // 차로 변경처럼 크게 튄 값은 자른다
        val base = zw.sum() / zw.size

        val trips = LinkedHashMap<Int, MutableList<Double>>()
        learn.forEachIndexed { i, s -> trips.getOrPut(s.trip) { mutableListOf() }.add(zw[i]) }
        var ss = 0.0
        var dof = 0
        for (vals in trips.values) {
            if (vals.size >= 2) {
                val mu = vals.sum() / vals.size
                ss += vals.sumOf { (it - mu) * (it - mu) }
                dof += vals.size - 1
            }
        }
        val sg = max(floorS, if (dof >= 5) sqrt(ss / dof) else s0)
        var between = 0.0
        var noise = 0.0
        for (vals in trips.values) {
            val mu = vals.sum() / vals.size
            between += (mu - base) * (mu - base)
            noise += sg * sg / vals.size
        }
        val tau2 = (between - noise) / trips.size
        baseline = base
        sigma = sg
        tau = max(floorT, sqrt(max(0.0, tau2)))
        fitDay = day
        if (!learned && learn.size >= HealthConfig.LEARN_PASSES &&
            learnDays() >= HealthConfig.LEARN_MIN_DAYS && learnTrips() >= HealthConfig.LEARN_MIN_TRIPS
        ) {
            learned = true
        }
    }

    // ── 보고 ────────────────────────────────────────────────────
    /** 서버에 보낼 익명 편차 v = y − a. 아직 모르거나 내 차가 의심스러우면 보내지 않는다. */
    fun reportValue(y: Double): Double? {
        val base = baseline ?: return null
        if (alarmShift != null) return null
        if (sPos > h / 2 || sNeg > h / 2) return null
        return y - base
    }

    // ── 지켜보기 ────────────────────────────────────────────────
    /** 최근 주행들의 평균 편차(구간 수 가중). 판단에 쓴다. */
    fun shift(): Double {
        val n = recent.sumOf { it.n }
        return if (n != 0) recent.sumOf { it.m * it.n } / n else 0.0
    }

    /** 알림 문장에 쓸 변화량: 최근 주행 중 같은 쪽으로 벗어난 주행들만, 덜 자른 값으로 평균한다. */
    fun estimate(): Double {
        val sign = if (shift() >= 0) 1.0 else -1.0
        val need = HealthConfig.MIN_SHIFT_RATIO * target
        val rows = recent.filter { sign * it.m >= need / 2 }
        val n = rows.sumOf { it.n }
        return if (n != 0) rows.sumOf { it.mRaw * it.n } / n else shift()
    }

    /** 최근 주행 평균이 기준의 70%를 넘고, 그중 둘 이상이 같은 쪽으로 기준의 절반 넘게 벗어났나. */
    private fun confirmed(sign: Int): Boolean {
        val need = HealthConfig.MIN_SHIFT_RATIO * target
        if (recent.sumOf { it.n } < HealthConfig.MIN_WINDOW_PASSES) return false
        val agree = recent.count { sign * it.m >= need / 2 }
        return sign * shift() >= need && agree >= HealthConfig.AGREE_TRIPS
    }

    /** 주행 한 번이 끝났다. */
    fun observeTrip(trip: Int, items: List<TripItem>) {
        if (items.isEmpty()) return
        if (!learned) {
            items.forEach { learn.add(Sample(it.segment, it.day, trip, it.y)) }
            return
        }
        val base = baseline!!
        val sg = sigma!!
        val tu = tau!!
        var raw = items.map { (it.y - it.r) - base }
        val lim = HealthConfig.CLIP_SIGMA * sg
        val n = raw.size
        // 판단용: 평소 값에서 4σ 넘게 튄 구간(차로 변경)은 자른다
        val m = raw.sumOf { clip(it, -lim, lim) } / n
        // 크기용: 큰 고장도 제 크기로 말하도록, 그 주행의 가운데 값 기준으로만 자른다
        if (n >= 3) {
            val med = median(raw)
            raw = raw.map { clip(it, med - lim, med + lim) }
        }
        val mRaw = raw.sum() / n
        val variance = tu * tu + sg * sg / n
        val d = target
        val cap = HealthConfig.CUSUM_CAP_RATIO * h
        sPos = min(cap, max(0.0, sPos + d / variance * (m - d / 2)))
        sNeg = if (twoSided) min(cap, max(0.0, sNeg + d / variance * (-m - d / 2))) else 0.0
        recent.add(RecentTrip(m, n, mRaw))
        if (recent.size > HealthConfig.RECENT_TRIPS) recent.removeAt(0)
        if (alarmShift == null) {
            if ((sPos > h && confirmed(1)) || (sNeg > h && confirmed(-1))) alarmShift = estimate()
            return
        }
        calm = if (abs(shift()) < HealthConfig.CLEAR_RATIO * d) calm + 1 else 0
        if (calm >= HealthConfig.CLEAR_TRIPS) {          // 정비 뒤 원래대로 돌아왔다
            alarmShift = null
            calm = 0
            sPos = 0.0
            sNeg = 0.0
        } else {
            alarmShift = estimate()
        }
    }

    /** 다 배우려면 남은 (통과 수, 날짜 수, 주행 수). */
    fun remaining(): Triple<Int, Int, Int> {
        if (learned) return Triple(0, 0, 0)
        return Triple(
            max(0, HealthConfig.LEARN_PASSES - learn.size),
            max(0, HealthConfig.LEARN_MIN_DAYS - learnDays()),
            max(0, HealthConfig.LEARN_MIN_TRIPS - learnTrips()),
        )
    }
}

data class Finding(
    val kind: String,          // pull(핸들 쏠림) · tire(쏠림 + 에너지도 더 씀) · drag(에너지만 더 씀)
    val steerShift: Double,
    val effShift: Double,
)

/** 차 한 대. 지표 둘을 지켜보고, 알릴 것이 있으면 무엇인지 고른다. */
class CarHealth(val model: String, val powertrain: String = "ev") {
    val trackers: Map<String, MetricTracker> = linkedMapOf(STEER to MetricTracker(STEER), EFF to MetricTracker(EFF))

    fun finding(): Finding? {
        val st = trackers.getValue(STEER)
        val ef = trackers.getValue(EFF)
        if (st.alarmShift == null && ef.alarmShift == null) return null
        val steerNow = if (st.alarmShift != null) st.estimate() else if (st.learned) st.shift() else 0.0
        val effNow = if (ef.alarmShift != null) ef.estimate() else if (ef.learned) ef.shift() else 0.0
        val kind = if (st.alarmShift != null) {
            val effRising = ef.alarmShift != null || (ef.learned && ef.shift() >= TIRE_EFF)
            if (effRising) "tire" else "pull"
        } else {
            "drag"
        }
        return Finding(kind, steerNow, effNow)
    }

    /** '내 차 괜찮아?'에 대한 답이자, 알림 문장. */
    fun speech(): String {
        val f = finding()
        if (f != null) return describe(f, powertrain)
        val rem = trackers.values.map { it.remaining() }
        val passes = rem.maxOf { it.first }
        val days = rem.maxOf { it.second }
        val trips = rem.maxOf { it.third }
        if (passes > 0) return "아직 평소 상태를 배우는 중이에요. 똑바른 길을 ${passes}번 더 지나면 비교할 수 있어요."
        if (days > 0) return "아직 평소 상태를 배우는 중이에요. ${days}일 더 타면 비교할 수 있어요."
        if (trips > 0) return "아직 평소 상태를 배우는 중이에요. ${trips}번 더 타면 비교할 수 있어요."
        val energy = if (powertrain == "ev") "전기" else "기름"
        return "핸들 쏠림도, $energy 쓰는 양도 평소와 같아요."
    }

    val learned: Boolean get() = trackers.values.all { it.learned }

    companion object {
        /** 쏠림과 함께 에너지도 이만큼(약 2%) 넘게 꾸준히 더 쓰면 타이어 공기압을 먼저 의심한다. */
        const val TIRE_EFF = 0.02
    }
}

/** 파이썬의 f"{x:.Nf}"와 같게: 정확한 이진 값을 반올림하되 딱 절반이면 짝수 쪽으로. */
private fun fixed(x: Double, digits: Int): String =
    BigDecimal(x).setScale(digits, RoundingMode.HALF_EVEN).toPlainString()

private fun deg(x: Double): String {
    val a = abs(x)
    return if (a >= 1.5) fixed(a, 0) else fixed(a, 1)
}

fun describe(f: Finding, powertrain: String): String {
    val ev = powertrain == "ev"
    val energyObj = if (ev) "전기를" else "기름을"
    val energyAlso = if (ev) "전기도" else "기름도"
    val pct = max(1, round((exp(f.effShift) - 1.0) * 100).toInt())
    val wheel = if (f.steerShift > 0) "오른" else "왼"     // 똑바로 가려고 핸들을 더 돌린 쪽
    val pull = if (f.steerShift > 0) "왼" else "오른"      // 차가 쏠리는 쪽
    return when (f.kind) {
        "pull" -> "차가 ${pull}쪽으로 쏠리고 있어요. 똑바로 가려면 핸들을 평소보다 ${wheel}쪽으로 " +
            "약 ${deg(f.steerShift)}도 더 돌려야 해요. ${pull}쪽 타이어 공기압을 먼저 보고, " +
            "괜찮으면 바퀴 정렬 점검을 권해요."
        "tire" -> "차가 ${pull}쪽으로 쏠리고, $energyAlso 평소보다 약 ${pct}퍼센트 더 써요. " +
            "${pull}쪽 타이어 공기압부터 확인해 보세요. 공기압이 괜찮으면 바퀴 정렬을 점검하세요."
        else -> "길과 날씨 영향을 빼고 보면, $energyObj 평소보다 약 ${pct}퍼센트 더 쓰고 있어요. " +
            "브레이크 끌림이나 타이어 공기압 점검을 권해요."
    }
}

/** 1km당 에너지를 로그로 바꾸고, 기준 속도로 보정한다. */
fun adjustEff(perKm: Double, speedKmh: Double, vRefKmh: Double): Double =
    ln(perKm) - HealthConfig.SPEED_SLOPE_PER_KMH * (speedKmh - vRefKmh)
