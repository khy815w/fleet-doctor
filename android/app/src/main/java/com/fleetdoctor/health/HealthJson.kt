package com.fleetdoctor.health

import org.json.JSONArray
import org.json.JSONObject

/** 차 상태를 파일에 남기고 다시 읽는다. 값은 그대로 되살아나야 한다(시험이 매 단계 확인한다). */
object HealthJson {
    fun toJson(h: CarHealth): JSONObject {
        val trackers = JSONObject()
        for ((q, t) in h.trackers) trackers.put(q, tracker(t))
        return JSONObject().put("model", h.model).put("powertrain", h.powertrain).put("trackers", trackers)
    }

    fun fromJson(o: JSONObject): CarHealth {
        val h = CarHealth(o.getString("model"), o.getString("powertrain"))
        val trackers = o.getJSONObject("trackers")
        for ((q, t) in h.trackers) if (trackers.has(q)) restore(t, trackers.getJSONObject(q))
        return h
    }

    private fun tracker(t: MetricTracker): JSONObject {
        val learn = JSONArray()
        for (s in t.learn) learn.put(JSONArray().put(s.segment).put(s.day).put(s.trip).put(s.y))
        val recent = JSONArray()
        for (r in t.recent) recent.put(JSONArray().put(r.m).put(r.n).put(r.mRaw))
        return JSONObject()
            .put("learned", t.learned)
            .put("baseline", t.baseline ?: JSONObject.NULL)
            .put("sigma", t.sigma ?: JSONObject.NULL)
            .put("tau", t.tau ?: JSONObject.NULL)
            .put("fit_day", t.fitDay ?: JSONObject.NULL)
            .put("s_pos", t.sPos)
            .put("s_neg", t.sNeg)
            .put("alarm_shift", t.alarmShift ?: JSONObject.NULL)
            .put("calm", t.calm)
            .put("recent", recent)
            .put("learn", learn)
    }

    private fun restore(t: MetricTracker, o: JSONObject) {
        t.learned = o.getBoolean("learned")
        t.baseline = o.dbl("baseline")
        t.sigma = o.dbl("sigma")
        t.tau = o.dbl("tau")
        t.fitDay = if (o.isNull("fit_day")) null else o.getInt("fit_day")
        t.sPos = o.getDouble("s_pos")
        t.sNeg = o.getDouble("s_neg")
        t.alarmShift = o.dbl("alarm_shift")
        t.calm = o.getInt("calm")
        t.recent.clear()
        val recent = o.getJSONArray("recent")
        for (i in 0 until recent.length()) {
            val r = recent.getJSONArray(i)
            t.recent.add(RecentTrip(r.getDouble(0), r.getInt(1), r.getDouble(2)))
        }
        t.learn.clear()
        val learn = o.getJSONArray("learn")
        for (i in 0 until learn.length()) {
            val s = learn.getJSONArray(i)
            t.learn.add(Sample(s.getString(0), s.getInt(1), s.getInt(2), s.getDouble(3)))
        }
    }

    private fun JSONObject.dbl(key: String): Double? = if (isNull(key)) null else getDouble(key)
}
