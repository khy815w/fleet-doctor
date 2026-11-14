package com.fleetdoctor.engine

import com.fleetdoctor.health.CarHealth
import com.fleetdoctor.health.Finding
import com.fleetdoctor.health.HealthConfig.EFF
import com.fleetdoctor.health.HealthConfig.METRICS
import com.fleetdoctor.health.HealthConfig.STEER
import com.fleetdoctor.health.HealthJson
import com.fleetdoctor.health.TripItem
import com.fleetdoctor.trip.Pass
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import kotlin.math.max
import kotlin.math.round

/** 서버와 주고받는 것. 시험에서는 가짜로 바꿔 끼운다. */
interface FleetApi {
    /** (구간, 날짜)마다 그 차종의 길 몫 [steer, eff]. 순서는 요청과 같다. */
    fun roadShares(model: String, keys: List<Pair<String, Int>>): List<DoubleArray>

    /** 익명 편차 보고. 받아들인 값 수를 돌려준다. */
    fun report(model: String, day: Int, items: JSONArray): Int
}

/** 지난 주행 기록 하나(시연 차 기록 받아 오기용). */
data class HistoryPass(val segment: String, val day: Int, val trip: Int, val steer: Double?, val eff: Double?)

data class TripResult(
    val trip: Int,
    val passes: Int,
    val reported: Int,
    val finding: Finding?,
    val isNew: Boolean,          // 처음 알리는 판단이면 true(같은 판단은 다시 알리지 않는다)
    val waiting: Int,            // 서버에 닿지 않아 판단을 미룬 주행 수
)

/**
 * 주행 한 번이 끝날 때 하는 일. 시뮬레이터(simulator/sim.py의 step_day)와 같은 순서다.
 * 1) 지금 상태로 익명 편차를 보고한다. 2) 그 주행 구간들의 길 몫을 받는다.
 * 3) 주행 단위로 판단한다. 4) 배우는 중이면 하루 한 번, 배운 뒤에는 7일마다 평소 값을 다시 계산한다.
 * 서버에 닿지 않으면 판단을 미뤄 두고 다음 주행 때 차례로 한다. 차 ID·주행 기록은 차 밖으로 나가지 않는다.
 */
class DoctorEngine(private val api: FleetApi, private val file: File?, health: CarHealth) {
    var health: CarHealth = health
        private set
    var lastTrip = -1
        private set
    private var announced: String? = null
    private val waiting = ArrayDeque<Waiting>()

    private data class Waiting(val trip: Int, val day: Int, val passes: List<Pass>)

    /** 아직 아무것도 배우지 않았다(새 차, 또는 정비 뒤 다시 배우기 직후). */
    val isEmpty: Boolean get() = health.trackers.values.all { it.learn.isEmpty() && it.baseline == null }

    fun onTripEnd(passes: List<Pass>, day: Int): TripResult {
        val trip = max(lastTrip + 1, day * 10)
        lastTrip = trip
        val reported = report(passes, day)
        waiting.addLast(Waiting(trip, day, passes))
        drain()
        val f = health.finding()
        val isNew = f != null && f.kind != announced
        announced = f?.kind
        save()
        return TripResult(trip, passes.size, reported, f, isNew, waiting.size)
    }

    /** 미뤄 둔 판단을 다시 해 본다(서버에 다시 닿았을 때). */
    fun retry(): TripResult? {
        if (waiting.isEmpty()) return null
        drain()
        val f = health.finding()
        val isNew = f != null && f.kind != announced
        announced = f?.kind
        save()
        return TripResult(lastTrip, 0, 0, f, isNew, waiting.size)
    }

    /** "정비했어요": 평소 값을 처음부터 다시 배운다. */
    fun serviced() {
        health = CarHealth(health.model, health.powertrain)
        announced = null
        waiting.clear()
        save()
    }

    /**
     * 시연 차의 지난 기록으로 상태를 만든다. 평소와 같은 순서(주행마다 판단 → 하루 끝에 다시 배우기)로 돌린다.
     * 길 몫은 서버의 지금 추정값을 쓰므로, 시뮬레이터가 그날그날 쓴 값과 조금 다를 수 있다.
     */
    fun importHistory(passes: List<HistoryPass>) {
        val shares = fetch(passes.map { it.segment to it.day }.distinct())
        val h = CarHealth(health.model, health.powertrain)
        var last = lastTrip
        for ((day, ofDay) in passes.groupBy { it.day }.toSortedMap()) {
            for ((trip, ofTrip) in ofDay.groupBy { it.trip }.toSortedMap()) {
                for (q in METRICS) {
                    val items = ofTrip.mapNotNull { p ->
                        val y = if (q == STEER) p.steer else p.eff
                        y?.let { TripItem(p.segment, day, it, shares.getValue(p.segment to day)[idx(q)]) }
                    }
                    h.trackers.getValue(q).observeTrip(trip, items)
                }
                last = max(last, trip)
            }
            refitIfNeeded(h, day, shares)
        }
        health = h
        lastTrip = last
        announced = h.finding()?.kind
        save()
    }

    private fun report(passes: List<Pass>, day: Int): Int {
        val items = JSONArray()
        for (p in passes) {
            val o = JSONObject().put("segment", p.segment)
            for (q in METRICS) {
                val y = p.value(q) ?: continue
                health.trackers.getValue(q).reportValue(y)?.let { o.put(q, round(it * 1e5) / 1e5) }
            }
            if (o.length() > 1) items.put(o)
        }
        if (items.length() == 0) return 0
        return try {
            api.report(health.model, day, items)
        } catch (e: Exception) {
            0                       // 보고는 못 보내도 판단은 한다
        }
    }

    private fun drain() {
        while (waiting.isNotEmpty()) {
            val w = waiting.first()
            val shares = try {
                fetch(w.passes.map { it.segment to w.day }.distinct())
            } catch (e: Exception) {
                return                      // 서버에 닿지 않는다: 다음에 다시
            }
            for (q in METRICS) {
                val items = w.passes.mapNotNull { p ->
                    p.value(q)?.let { TripItem(p.segment, w.day, it, shares.getValue(p.segment to w.day)[idx(q)]) }
                }
                health.trackers.getValue(q).observeTrip(w.trip, items)
            }
            waiting.removeFirst()
            try {
                refitIfNeeded(health, w.day, shares)
            } catch (e: Exception) {
                return                      // 다시 계산은 다음 주행 때 다시 해 본다
            }
        }
    }

    private fun refitIfNeeded(h: CarHealth, day: Int, cache: MutableMap<Pair<String, Int>, DoubleArray>) {
        for (q in METRICS) {
            val t = h.trackers.getValue(q)
            if (!t.needsRefit(day)) continue
            val keys = t.learnKeys()
            val missing = keys.filter { it !in cache }.distinct()
            if (missing.isNotEmpty()) cache.putAll(fetch(missing))
            t.refit(keys.map { cache.getValue(it)[idx(q)] }, day)
        }
    }

    private fun fetch(keys: List<Pair<String, Int>>): MutableMap<Pair<String, Int>, DoubleArray> {
        val out = HashMap<Pair<String, Int>, DoubleArray>()
        for (chunk in keys.chunked(500)) {
            val vals = api.roadShares(health.model, chunk)
            chunk.forEachIndexed { i, k -> out[k] = vals[i] }
        }
        return out
    }

    private fun idx(q: String) = if (q == STEER) 0 else 1

    // ── 남기기 ──────────────────────────────────────────────────
    fun toJson(): JSONObject {
        val w = JSONArray()
        for (x in waiting) {
            val ps = JSONArray()
            for (p in x.passes) {
                ps.put(JSONObject().put("segment", p.segment).put(STEER, p.steer ?: JSONObject.NULL).put(EFF, p.eff ?: JSONObject.NULL))
            }
            w.put(JSONObject().put("trip", x.trip).put("day", x.day).put("passes", ps))
        }
        return JSONObject()
            .put("version", 1)
            .put("health", HealthJson.toJson(health))
            .put("last_trip", lastTrip)
            .put("announced", announced ?: JSONObject.NULL)
            .put("waiting", w)
    }

    private fun save() {
        val f = file ?: return
        val tmp = File(f.path + ".tmp")
        tmp.writeText(toJson().toString())
        if (!tmp.renameTo(f)) {
            f.delete()
            tmp.renameTo(f)
        }
    }

    companion object {
        /** 남겨 둔 상태가 있으면 되살린다. 차종이 바뀌었으면 새로 시작한다. */
        fun open(api: FleetApi, file: File?, model: String, powertrain: String): DoctorEngine {
            val fresh = DoctorEngine(api, file, CarHealth(model, powertrain))
            if (file == null || !file.exists()) return fresh
            return try {
                val o = JSONObject(file.readText())
                val h = HealthJson.fromJson(o.getJSONObject("health"))
                if (h.model != model || h.powertrain != powertrain) return fresh
                DoctorEngine(api, file, h).apply {
                    lastTrip = o.getInt("last_trip")
                    announced = if (o.isNull("announced")) null else o.getString("announced")
                    val w = o.getJSONArray("waiting")
                    for (i in 0 until w.length()) {
                        val x = w.getJSONObject(i)
                        val ps = x.getJSONArray("passes")
                        val passes = (0 until ps.length()).map { j ->
                            val p = ps.getJSONObject(j)
                            Pass(
                                p.getString("segment"),
                                if (p.isNull(STEER)) null else p.getDouble(STEER),
                                if (p.isNull(EFF)) null else p.getDouble(EFF),
                            )
                        }
                        waiting.addLast(Waiting(x.getInt("trip"), x.getInt("day"), passes))
                    }
                }
            } catch (e: Exception) {
                fresh                       // 파일이 깨졌다: 처음부터 다시 배운다
            }
        }

        fun parseHistory(o: JSONObject): List<HistoryPass> {
            val arr = o.getJSONArray("passes")
            return (0 until arr.length()).map { i ->
                val p = arr.getJSONObject(i)
                HistoryPass(
                    p.getString("segment"), p.getInt("day"), p.getInt("trip"),
                    if (p.isNull(STEER)) null else p.getDouble(STEER),
                    if (p.isNull(EFF)) null else p.getDouble(EFF),
                )
            }
        }

        /** 남은 배우기 양을 한 줄로(화면용). */
        fun learningLine(h: CarHealth): String? {
            if (h.learned) return null
            val rem = h.trackers.values.map { it.remaining() }
            return "배우는 중: 똑바른 길 ${rem.maxOf { it.first }}번 · ${rem.maxOf { it.second }}일 · 주행 ${rem.maxOf { it.third }}번 더"
        }
    }
}
