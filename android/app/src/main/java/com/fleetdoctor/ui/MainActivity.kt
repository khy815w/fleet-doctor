package com.fleetdoctor.ui

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.graphics.text.LineBreakConfig
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import com.fleetdoctor.DoctorState
import com.fleetdoctor.service.DoctorService
import java.util.Locale

/**
 * 큰 글자 상태 한 줄, 알림 문장, 버튼 둘. 주행 중 화면 규제에 맞춰 입력란·애니메이션·영상이 없고,
 * 알림은 음성이 먼저다. AAOS에서 주행 중에도 보이도록 manifest에 distractionOptimized를 켰다.
 */
class MainActivity : Activity() {
    private lateinit var headline: TextView
    private lateinit var detail: TextView
    private lateinit var info: TextView
    private val listener: () -> Unit = { runOnUiThread { render() } }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        headline = TextView(this).apply {
            textSize = 40f
            gravity = Gravity.CENTER
            setTextColor(Color.WHITE)
        }
        detail = TextView(this).apply {
            textSize = 22f
            gravity = Gravity.CENTER
            setTextColor(TEXT)
            setPadding(0, 24, 0, 0)
            setLineSpacing(0f, 1.2f)
            // 한국어를 글자가 아니라 어절 단위로 줄바꿈한다("같아/요"처럼 끊지 않게). 기기 언어와 상관없이 한국어로 본다
            textLocale = Locale.KOREAN
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                lineBreakWordStyle = LineBreakConfig.LINE_BREAK_WORD_STYLE_PHRASE
            }
        }
        info = TextView(this).apply {
            textSize = 16f
            gravity = Gravity.CENTER
            setTextColor(MUTED)
            setPadding(0, 20, 0, 0)
        }
        val buttons = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            setPadding(0, 40, 0, 0)
            addView(button("내 차 괜찮아?") { DoctorService.instance?.ask() })
            addView(button("정비했어요") { DoctorService.instance?.serviced() })
        }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(BACKGROUND)
            setPadding(64, 48, 64, 48)
            addView(headline)
            addView(detail)
            addView(info)
            addView(buttons)
        })
        startWhenPermitted()
    }

    override fun onStart() {
        super.onStart()
        DoctorState.addListener(listener)
        render()
    }

    override fun onStop() {
        DoctorState.removeListener(listener)
        super.onStop()
    }

    private fun render() {
        val (title, color) = when (DoctorState.status) {
            DoctorState.Status.CHECK -> "점검이 필요해요" to CAUTION
            DoctorState.Status.LEARNING -> "평소 상태를 배우는 중" to Color.WHITE
            DoctorState.Status.OK -> "평소와 같아요" to GOOD
            DoctorState.Status.STARTING -> "플릿 닥터" to Color.WHITE
        }
        headline.text = title
        headline.setTextColor(color)
        detail.text = DoctorState.speech
        info.text = listOfNotNull(
            if (DoctorState.connected) null else "서버에 연결하는 중",
            DoctorState.learning,
            DoctorState.segment?.let { "지금 구간 $it · 이번 주행 ${DoctorState.tripPasses}개" },
            DoctorState.lastTrip.takeIf { it.isNotEmpty() },
            DoctorState.dialog,
            DoctorState.waypoint?.let { "경유지: $it" },
        ).joinToString("\n")
    }

    private fun button(label: String, onClick: () -> Unit) = Button(this).apply {
        text = label
        textSize = 20f
        isAllCaps = false
        setTextColor(Color.WHITE)
        background = GradientDrawable().apply {
            cornerRadius = 24f
            setColor(BUTTON)
        }
        setPadding(48, 28, 48, 28)
        setOnClickListener { onClick() }
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { setMargins(16, 0, 16, 0) }
    }

    private fun startWhenPermitted() {
        val needed = buildList {
            add(Manifest.permission.ACCESS_FINE_LOCATION)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) add(Manifest.permission.POST_NOTIFICATIONS)
        }.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (needed.isEmpty()) startDoctor() else requestPermissions(needed.toTypedArray(), REQUEST_PERMISSIONS)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED) {
            startDoctor()
        } else {
            headline.text = "위치 권한이 필요해요"
            detail.text = "어느 길을 지나는지 알아야 길 영향을 뺄 수 있어요"
        }
    }

    private fun startDoctor() {
        startForegroundService(Intent(this, DoctorService::class.java))
    }

    private companion object {
        const val REQUEST_PERMISSIONS = 1
        val BACKGROUND = Color.rgb(14, 18, 23)
        val TEXT = Color.rgb(214, 222, 230)
        val MUTED = Color.rgb(139, 152, 165)
        val BUTTON = Color.rgb(38, 48, 60)
        val GOOD = Color.rgb(74, 222, 128)
        val CAUTION = Color.rgb(245, 158, 11)
    }
}
