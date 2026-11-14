package com.fleetdoctor

import com.fleetdoctor.health.HealthConfig
import com.fleetdoctor.trip.DriveSample
import com.fleetdoctor.trip.Pass
import com.fleetdoctor.trip.PassBuilder
import com.fleetdoctor.trip.TripSplitter
import com.fleetdoctor.world.Geo
import com.fleetdoctor.world.World
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.ln

/** 구간 맞추기, 구간 값 만들기, 주행 나누기. 지도는 서버와 같은 world_demo.json이다. */
class WorldTest {
    private val world = World.fromJson(listOf(JSONObject(javaClass.classLoader!!.getResource("world_demo.json")!!.readText())))

    private fun along(id: String, f: Double): Pair<Double, Double> {
        val pts = world.byId.getValue(id).points
        val (a, b) = pts.first() to pts.last()
        return (a.first + (b.first - a.first) * f) to (a.second + (b.second - a.second) * f)
    }

    @Test
    fun matchFindsSameDirectionSegment() {
        val seg = world.byId.getValue("V2-1N")
        val (lat, lon) = along("V2-1N", 0.4)
        val hit = world.match(lat, lon, 2.0)
        assertEquals("V2-1N", hit?.segment?.id)
        assertEquals(0.4 * seg.lengthM, hit!!.alongM, 5.0)
        assertEquals("V2-1S", world.match(lat, lon, 181.0)?.segment?.id)      // 반대 방향은 다른 구간
        assertNull(world.match(lat, lon, 90.0))                               // 옆으로 가면 아무 구간도 아니다
    }

    @Test
    fun servicePointsAndCells() {
        assertEquals(listOf("SP1", "SP2"), world.servicePoints.map { it.id })
        val (lat, lon) = along("V2-1N", 0.5)
        assertEquals("SP1", world.nearestServicePoint(lat, lon)?.id)
        val cell = Geo.geohash(lat, lon, 5)
        assertEquals(9, Geo.neighbors(cell).toSet().size)
        assertTrue(cell in Geo.neighbors(cell))
    }

    /** 구간들을 1초마다 지나간다. speed(구간 시작부터 거리, 구간 길이) = 그곳 속도. */
    private fun drive(
        ids: List<String>,
        speed: (Double, Double) -> Double = { _, _ -> 80.0 },
        wheel: Double? = 0.3 * 15,
        perKm: Double? = 0.15,
    ): List<Pass> {
        val b = PassBuilder { world }
        val out = mutableListOf<Pass>()
        var t = 0.0
        for (id in ids) {
            val seg = world.byId.getValue(id)
            var d = 0.0
            while (d < seg.lengthM) {
                val v = speed(d, seg.lengthM)
                val (lat, lon) = along(id, d / seg.lengthM)
                b.update(DriveSample(t, lat, lon, seg.heading, v, wheel, perKm))?.let { out.add(it) }
                d += v / 3.6
                t += 1.0
            }
        }
        b.flush()?.let { out.add(it) }
        return out
    }

    @Test
    fun passBuilderMakesOneValuePerSegment() {
        val ps = drive(listOf("V2-1N", "V2-2N"))                              // 둘 다 똑바른 구간
        assertEquals(listOf("V2-1N", "V2-2N"), ps.map { it.segment })       // 마지막 구간은 주행이 끝날 때 나온다
        assertEquals(4.5, ps[0].steer!!, 1e-9)
        assertEquals(ln(0.15), ps[0].eff!!, 1e-9)                            // 기준 속도 80이라 속도 보정 0
    }

    @Test
    fun slowingForTurnsAtSegmentEndsIsIgnored() {
        // 교차로 앞뒤 100m에서는 시속 35km로 줄인다. 가운데만 재므로 값은 그대로 나온다
        val ps = drive(listOf("V2-1N", "V2-2N"), speed = { d, len -> if (d < 100 || d > len - 100) 35.0 else 80.0 })
        assertEquals(listOf("V2-1N", "V2-2N"), ps.map { it.segment })
    }

    @Test
    fun unsteadySpeedIsDropped() {
        val ps = drive(listOf("V2-1N", "V2-2N"), speed = { d, _ -> if ((d / 200).toInt() % 2 == 0) 95.0 else 60.0 })
        assertTrue(ps.isEmpty())
    }

    @Test
    fun curvedSegmentHasNoSteerAndSpeedIsCorrected() {
        val curved = world.segments.first { !it.straight }
        val next = world.segments.first { it.id != curved.id && it.straight }
        val b = PassBuilder { world }
        var t = 0.0
        var got: Pass? = null
        // 굽은 구간은 점이 여러 개라 선분마다 따라간다
        for (i in 0 until curved.points.size - 1) {
            val (a, c) = curved.points[i] to curved.points[i + 1]
            val h = (Math.toDegrees(kotlin.math.atan2((c.second - a.second) * kotlin.math.cos(Math.toRadians(a.first)), c.first - a.first)) + 360) % 360
            for (s in 0 until 20) {
                val f = s / 20.0
                b.update(DriveSample(t, a.first + (c.first - a.first) * f, a.second + (c.second - a.second) * f, h, 90.0, 1.0, 0.2))?.let { got = it }
                t += 1.0
            }
        }
        val (lat, lon) = next.points.first()
        b.update(DriveSample(t, lat, lon, next.heading, 90.0, 1.0, 0.2))?.let { got = it }
        assertNotNull(got)
        assertEquals(curved.id, got!!.segment)
        assertNull(got!!.steer)
        assertEquals(ln(0.2) - HealthConfig.SPEED_SLOPE_PER_KMH * 10.0, got!!.eff!!, 1e-9)
    }

    @Test
    fun tripEndsAfterLongStopOrGap() {
        val s = TripSplitter(180.0)
        assertFalse(s.update(0.0, 0.0))
        assertFalse(s.update(1.0, 50.0))
        assertTrue(s.inTrip)
        assertFalse(s.update(100.0, 0.0))                                    // 신호 대기
        assertFalse(s.update(150.0, 30.0))
        assertFalse(s.update(320.0, 0.0))
        assertTrue(s.update(331.0, 0.0))                                     // 마지막으로 움직인 뒤 3분
        assertFalse(s.inTrip)
        assertFalse(s.update(340.0, 40.0))
        assertTrue(s.update(900.0, 40.0))                                    // 표본이 10분 끊겼다: 앞 주행은 끝, 새 주행
        assertTrue(s.inTrip)
    }

    @Test
    fun dayOfMatchesServerCalendar() {
        assertEquals(0, HealthConfig.dayOf(HealthConfig.EPOCH_KST_UNIX.toDouble()))
        assertEquals(31, HealthConfig.dayOf(HealthConfig.EPOCH_KST_UNIX + 31 * 86400.0 + 3600))
        assertEquals(-1, HealthConfig.dayOf(HealthConfig.EPOCH_KST_UNIX - 0.5))
    }
}
