package com.fleetdoctor

import com.fleetdoctor.engine.Answers
import com.fleetdoctor.engine.DoctorEngine
import com.fleetdoctor.engine.FleetApi
import com.fleetdoctor.engine.HistoryPass
import com.fleetdoctor.trip.Pass
import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.io.IOException
import java.util.Random

/** 주행이 끝날 때의 순서(보고 → 길 몫 → 판단 → 다시 배우기), 서버가 끊겼을 때, 기록 받아 오기, 남기기. */
class DoctorEngineTest {
    /** 구간마다 길 몫이 다른 가짜 서버. */
    private class FakeApi : FleetApi {
        var down = false
        val reports = mutableListOf<JSONArray>()
        var queries = 0

        fun share(seg: String) = doubleArrayOf(if (seg.endsWith("N")) 0.4 else -0.3, if (seg.startsWith("V")) 0.05 else -0.02)

        override fun roadShares(model: String, keys: List<Pair<String, Int>>): List<DoubleArray> {
            if (down) throw IOException("down")
            queries++
            return keys.map { share(it.first) }
        }

        override fun report(model: String, day: Int, items: JSONArray): Int {
            if (down) throw IOException("down")
            reports.add(items)
            return (0 until items.length()).sumOf { items.getJSONObject(it).length() - 1 }
        }
    }

    private val segs = listOf("V2-1N", "V2-2N", "H4-2E", "V4-3S")
    private val rng = Random(5)

    /** 길 몫 + 내 차 몫(평소 0.6도, 에너지 −1.9) + 고장 + 흔들림. */
    private fun trip(api: FakeApi, steerFault: Double = 0.0): List<Pass> = segs.map { s ->
        val r = api.share(s)
        Pass(s, 0.6 + r[0] + steerFault + rng.nextGaussian() * 0.3, -1.9 + r[1] + rng.nextGaussian() * 0.03)
    }

    @Test
    fun learnsThenFindsPullOnceAndSpeaksIt() {
        val api = FakeApi()
        val e = DoctorEngine.open(api, null, "C", "ev")
        for (day in 0 until 12) repeat(2) { e.onTripEnd(trip(api), day) }
        assertTrue(e.health.learned)
        assertEquals(0.6, e.health.trackers.getValue("steer").baseline!!, 0.15)   // 길 몫을 빼고 내 차 몫만 배운다
        assertTrue(api.reports.isNotEmpty())                                     // 10번 지난 뒤부터 익명 보고
        val news = (0 until 4).map { e.onTripEnd(trip(api, steerFault = 2.0), 12) }
        assertEquals(1, news.count { it.isNew })                                 // 같은 판단은 한 번만 알린다
        assertEquals("pull", news.last().finding?.kind)
        assertTrue(e.health.speech().startsWith("차가 왼쪽으로 쏠리고 있어요"))
    }

    @Test
    fun tripsWaitWhileServerIsDownThenCatchUp() {
        val api = FakeApi()
        val e = DoctorEngine.open(api, null, "C", "ev")
        api.down = true
        val r = e.onTripEnd(trip(api), 0)
        assertEquals(1, r.waiting)
        assertTrue(e.isEmpty)
        api.down = false
        val r2 = e.onTripEnd(trip(api), 0)
        assertEquals(0, r2.waiting)
        assertEquals(8, e.health.trackers.getValue("eff").learn.size)            // 미뤘던 주행도 판단했다
        assertEquals(listOf(0, 1), e.health.trackers.getValue("eff").learn.map { it.trip }.distinct())
    }

    @Test
    fun savesAndRestoresIncludingWaitingTrips() {
        val f = File.createTempFile("health", ".json").apply { delete() }
        val api = FakeApi()
        val e = DoctorEngine.open(api, f, "C", "ev")
        for (day in 0 until 12) repeat(2) { e.onTripEnd(trip(api), day) }
        api.down = true
        e.onTripEnd(trip(api), 12)
        val back = DoctorEngine.open(api, f, "C", "ev")
        assertEquals(e.health.speech(), back.health.speech())
        assertEquals(e.lastTrip, back.lastTrip)
        assertEquals(e.health.trackers.getValue("steer").baseline, back.health.trackers.getValue("steer").baseline)
        api.down = false
        assertEquals(0, back.retry()!!.waiting)
        assertTrue(DoctorEngine.open(api, f, "E", "ice").isEmpty)                // 다른 차종이면 새로 배운다
        f.delete()
    }

    @Test
    fun importHistoryReplaysByDayAndServicedResets() {
        val api = FakeApi()
        val passes = (0 until 12).flatMap { day ->
            (0 until 2).flatMap { k -> trip(api).map { HistoryPass(it.segment, day, day * 10 + k, it.steer, it.eff) } }
        }
        val e = DoctorEngine.open(api, null, "C", "ev")
        e.importHistory(passes)
        assertTrue(e.health.learned)
        assertEquals(111, e.lastTrip)
        assertEquals(120, e.onTripEnd(trip(api), 12).trip)                       // 다음 주행 번호는 날짜 × 10부터
        e.serviced()
        assertTrue(e.isEmpty)
        assertNull(e.health.finding())
    }

    @Test
    fun answers() {
        assertEquals(true, Answers.parse("응"))
        assertEquals(true, Answers.parse("네, 넣어 주세요"))
        assertEquals(false, Answers.parse("아니"))
        assertEquals(false, Answers.parse("괜찮아요"))
        assertEquals(false, Answers.parse("네 괜찮아요"))
        assertNull(Answers.parse("어디 있는데?"))
        assertNull(Answers.parse(null))
        assertFalse(Answers.parse("나중에") == true)
    }
}
