package com.fleetdoctor

import com.fleetdoctor.health.CarHealth
import com.fleetdoctor.health.HealthConfig
import com.fleetdoctor.health.HealthConfig.METRICS
import com.fleetdoctor.health.HealthJson
import com.fleetdoctor.health.MetricTracker
import com.fleetdoctor.health.TripItem
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** 기준 구현(server/fleetdoc/carmodel.py)이 만든 시험 자료를 앱 구현으로 다시 돌려, 매 단계 같은지 본다. */
class CarHealthVectorsTest {
    private val data = JSONObject(javaClass.classLoader!!.getResource("carhealth.json")!!.readText())

    @Test
    fun configMatchesServer() {
        val c = data.getJSONObject("config")
        val ints = mapOf(
            "LEARN_PASSES" to HealthConfig.LEARN_PASSES, "LEARN_MIN_DAYS" to HealthConfig.LEARN_MIN_DAYS,
            "LEARN_MIN_TRIPS" to HealthConfig.LEARN_MIN_TRIPS, "REPORT_MIN_PASSES" to HealthConfig.REPORT_MIN_PASSES,
            "RECOMPUTE_EVERY_DAYS" to HealthConfig.RECOMPUTE_EVERY_DAYS, "RECENT_TRIPS" to HealthConfig.RECENT_TRIPS,
            "MIN_WINDOW_PASSES" to HealthConfig.MIN_WINDOW_PASSES, "AGREE_TRIPS" to HealthConfig.AGREE_TRIPS,
            "CLEAR_TRIPS" to HealthConfig.CLEAR_TRIPS, "HEADING_BINS" to HealthConfig.HEADING_BINS,
        )
        for ((k, v) in ints) assertEquals(k, c.getInt(k), v)
        val doubles = mapOf(
            "CLIP_SIGMA" to HealthConfig.CLIP_SIGMA, "MIN_SHIFT_RATIO" to HealthConfig.MIN_SHIFT_RATIO,
            "CLEAR_RATIO" to HealthConfig.CLEAR_RATIO, "CUSUM_CAP_RATIO" to HealthConfig.CUSUM_CAP_RATIO,
            "MATCH_MAX_OFFSET_M" to HealthConfig.MATCH_MAX_OFFSET_M, "MATCH_MAX_HEADING_DIFF" to HealthConfig.MATCH_MAX_HEADING_DIFF,
            "MIN_COVERAGE" to HealthConfig.MIN_COVERAGE, "STEADY_SPEED_TOL_KMH" to HealthConfig.STEADY_SPEED_TOL_KMH,
            "STEADY_SPEED_SD_KMH" to HealthConfig.STEADY_SPEED_SD_KMH, "SPEED_SLOPE_PER_KMH" to HealthConfig.SPEED_SLOPE_PER_KMH,
            "STEER_RATIO_NOMINAL" to HealthConfig.STEER_RATIO_NOMINAL, "TRIP_GAP_S" to HealthConfig.TRIP_GAP_S,
        )
        for ((k, v) in doubles) assertEquals(k, c.getDouble(k), v, 0.0)
        val maps = mapOf(
            "SIGMA_FLOOR" to HealthConfig.SIGMA_FLOOR, "TAU_FLOOR" to HealthConfig.TAU_FLOOR,
            "TARGET" to HealthConfig.TARGET, "CUSUM_H" to HealthConfig.CUSUM_H,
        )
        for ((k, m) in maps) for (q in METRICS) assertEquals("$k.$q", c.getJSONObject(k).getDouble(q), m.getValue(q), 0.0)
        assertEquals(c.getLong("EPOCH_KST_UNIX"), HealthConfig.EPOCH_KST_UNIX)
    }

    @Test
    fun replayMatchesReferenceImplementation() {
        var h = CarHealth(data.getString("model"), data.getString("powertrain"))
        val ops = data.getJSONArray("ops")
        for (i in 0 until ops.length()) {
            val op = ops.getJSONObject(i)
            if (op.getString("op") == "trip") {
                val reports = op.getJSONArray("reports")
                for (j in 0 until reports.length()) {
                    val rep = reports.getJSONObject(j)
                    for (q in METRICS) {
                        if (!rep.has(q)) continue
                        val r = rep.getJSONObject(q)
                        val v = h.trackers.getValue(q).reportValue(r.getDouble("y"))
                        if (r.isNull("v")) assertNull("op $i $q report", v) else assertEquals("op $i $q report", r.getDouble("v"), v!!, 1e-12)
                    }
                }
                val items = op.getJSONObject("items")
                for (q in METRICS) {
                    val arr = items.getJSONArray(q)
                    val list = (0 until arr.length()).map { k ->
                        val a = arr.getJSONArray(k)
                        TripItem(a.getString(0), a.getInt(1), a.getDouble(2), a.getDouble(3))
                    }
                    h.trackers.getValue(q).observeTrip(op.getInt("trip"), list)
                }
            } else {
                val rs = op.getJSONArray("rs")
                h.trackers.getValue(op.getString("metric")).refit((0 until rs.length()).map { rs.getDouble(it) }, op.getInt("day"))
            }
            h = HealthJson.fromJson(JSONObject(HealthJson.toJson(h).toString()))   // 파일에 남겼다 되살려도 그대로여야 한다
            val exp = op.getJSONObject("expect")
            for (q in METRICS) check("op $i $q", h.trackers.getValue(q), exp.getJSONObject(q))
            assertEquals("op $i finding", if (exp.isNull("finding")) null else exp.getString("finding"), h.finding()?.kind)
            assertEquals("op $i speech", exp.getString("speech"), h.speech())
        }
    }

    private fun check(where: String, t: MetricTracker, e: JSONObject) {
        assertEquals("$where learned", e.getBoolean("learned"), t.learned)
        assertEquals("$where calm", e.getInt("calm"), t.calm)
        val got = mapOf("baseline" to t.baseline, "sigma" to t.sigma, "tau" to t.tau, "s_pos" to t.sPos,
            "s_neg" to t.sNeg, "alarm_shift" to t.alarmShift)
        for ((k, v) in got) {
            if (e.isNull(k)) assertNull("$where $k", v) else assertEquals("$where $k", e.getDouble(k), v!!, 1e-12)
        }
    }
}
