package com.fleetdoctor.world

import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.sin
import kotlin.math.sqrt

private const val EARTH_R = 6_371_000.0
private const val BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"

/** 거리·방위·지오해시. 서버의 fleetdoc/geo.py와 같은 계산이다. */
object Geo {
    fun haversineM(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
        val p1 = Math.toRadians(lat1)
        val p2 = Math.toRadians(lat2)
        val sp = sin((p2 - p1) / 2)
        val sl = sin(Math.toRadians(lon2 - lon1) / 2)
        return 2 * EARTH_R * asin(sqrt(sp * sp + cos(p1) * cos(p2) * sl * sl))
    }

    fun angleDiff(a: Double, b: Double): Double {
        val d = Math.abs(a - b) % 360.0
        return if (d > 180.0) 360.0 - d else d
    }

    fun geohash(lat: Double, lon: Double, precision: Int = 5): String {
        val latRange = doubleArrayOf(-90.0, 90.0)
        val lonRange = doubleArrayOf(-180.0, 180.0)
        val out = StringBuilder()
        var bits = 0
        var ch = 0
        var even = true
        while (out.length < precision) {
            val range = if (even) lonRange else latRange
            val value = if (even) lon else lat
            val mid = (range[0] + range[1]) / 2
            if (value >= mid) {
                ch = (ch shl 1) or 1
                range[0] = mid
            } else {
                ch = ch shl 1
                range[1] = mid
            }
            even = !even
            if (++bits == 5) {
                out.append(BASE32[ch])
                bits = 0
                ch = 0
            }
        }
        return out.toString()
    }

    /** 자기 칸과 주변 8칸, 모두 9칸. */
    fun neighbors(hash: String): List<String> {
        val latRange = doubleArrayOf(-90.0, 90.0)
        val lonRange = doubleArrayOf(-180.0, 180.0)
        var even = true
        for (c in hash) {
            val v = BASE32.indexOf(c)
            for (shift in 4 downTo 0) {
                val range = if (even) lonRange else latRange
                val mid = (range[0] + range[1]) / 2
                if ((v shr shift) and 1 == 1) range[0] = mid else range[1] = mid
                even = !even
            }
        }
        val cLat = (latRange[0] + latRange[1]) / 2
        val cLon = (lonRange[0] + lonRange[1]) / 2
        val h = latRange[1] - latRange[0]
        val w = lonRange[1] - lonRange[0]
        return buildList {
            for (dy in -1..1) for (dx in -1..1) add(geohash(cLat + dy * h, cLon + dx * w, hash.length))
        }
    }
}

/** 기준점 주변을 평면(m)으로 펴는 등장방형 투영. 수십 km 안에서는 오차가 작다. */
class LocalFrame(private val lat0: Double, private val lon0: Double) {
    private val kx = Math.toRadians(1.0) * EARTH_R * cos(Math.toRadians(lat0))
    private val ky = Math.toRadians(1.0) * EARTH_R

    fun toXy(lat: Double, lon: Double): Pair<Double, Double> = (lon - lon0) * kx to (lat - lat0) * ky
}

/** 점 P에서 선분 AB까지의 거리와, 수선의 발이 놓인 위치(0~1). */
fun projectOnSegment(px: Double, py: Double, ax: Double, ay: Double, bx: Double, by: Double): Pair<Double, Double> {
    val dx = bx - ax
    val dy = by - ay
    val l2 = dx * dx + dy * dy
    val t = if (l2 == 0.0) 0.0 else (((px - ax) * dx + (py - ay) * dy) / l2).coerceIn(0.0, 1.0)
    return hypot(px - (ax + t * dx), py - (ay + t * dy)) to t
}
