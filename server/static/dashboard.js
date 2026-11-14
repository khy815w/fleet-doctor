// 플릿 닥터 관제 화면. /v1/state를 3초마다 읽어 구간 지도와 날짜별 에너지 몫 그래프를 그린다.
// 서버가 가진 것(익명 보고로 잡은 길 몫)만 보여 준다. 차마다의 상태는 서버에 없다.
(() => {
  const $ = (id) => document.getElementById(id);
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const NS = 'http://www.w3.org/2000/svg';
  const EPOCH_KST = 1790780400;                 // day 0 = 2026-10-01 00:00 KST
  const SERIES = ['--s1', '--s2', '--s3', '--s4', '--s5', '--s6'];   // 차종 A~F에 고정
  const STRATEGY_ORDER = ['own', 'fleet_all', 'fleet_model', 'oracle'];
  const st = { metric: 'steer', model: 'C', data: null, compare: null, models: [] };

  // ── 색: 파랑(−) · 회색(0) · 빨강(+)을 OKLab에서 섞어 7칸으로 ─────────────
  const lin = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  const gam = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);
  function toLab(hex) {
    const n = parseInt(hex.slice(1), 16);
    const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => lin(v / 255));
    const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
    const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
    const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
    return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
      1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
      0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
  }
  function fromLab([L, a, b]) {
    const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
    const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
    const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
    const rgb = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
      -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
      -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s];
    return '#' + rgb.map((c) => Math.round(Math.min(1, Math.max(0, gam(c))) * 255).toString(16).padStart(2, '0')).join('');
  }
  const mix = (h1, h2, t) => { const a = toLab(h1), b = toLab(h2); return fromLab(a.map((v, i) => v + (b[i] - v) * t)); };
  function divColor(v, vmax) {
    const bin = Math.round(Math.max(-1, Math.min(1, v / vmax)) * 3);
    if (bin === 0) return css('--mid');
    return mix(css('--mid'), css(bin < 0 ? '--neg' : '--pos'), Math.abs(bin) / 3);
  }

  // ── 글자 ────────────────────────────────────────────────────
  const pct = (v) => (Math.exp(v) - 1) * 100;
  const sign = (x) => (x > 0 ? '+' : x < 0 ? '−' : '');
  const fmt = (q, v) => (q === 'steer'
    ? `${sign(v)}${Math.abs(v).toFixed(2)}°`
    : `${sign(pct(v))}${Math.abs(pct(v)).toFixed(1)}%`);
  const dayLabel = (d) => { const t = new Date((EPOCH_KST + d * 86400 + 9 * 3600) * 1000); return `${t.getUTCMonth() + 1}/${t.getUTCDate()}`; };
  const modelName = (id) => (st.models.find((m) => m.id === id) || { name: id }).name;
  const isEv = (id) => (st.models.find((m) => m.id === id) || {}).powertrain === 'ev';
  const el = (tag, attrs = {}, parent) => {
    const e = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
    if (parent) parent.appendChild(e);
    return e;
  };

  // ── 툴팁 ────────────────────────────────────────────────────
  const tip = $('tip');
  function showTip(html, ev) {
    tip.innerHTML = html;
    tip.style.display = 'block';
    const w = tip.offsetWidth, h = tip.offsetHeight;
    let x = ev.clientX + 14, y = ev.clientY + 14;
    if (x + w > window.innerWidth - 8) x = ev.clientX - w - 14;
    if (y + h > window.innerHeight - 8) y = ev.clientY - h - 14;
    tip.style.left = `${x}px`; tip.style.top = `${y}px`;
  }
  const hideTip = () => { tip.style.display = 'none'; };

  // ── 지도 ────────────────────────────────────────────────────
  function drawMap() {
    const d = st.data, q = st.metric, svg = $('map');
    svg.replaceChildren();
    const segs = d.segments;
    const lat0 = segs[0].points[0][0], kx = Math.cos(lat0 * Math.PI / 180);
    const xy = (p) => [p[1] * kx, p[0]];
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    segs.forEach((s) => s.points.forEach((p) => { const [x, y] = xy(p); x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }));
    const pad = 28, size = 600 - 2 * pad, sc = size / Math.max(x1 - x0, y1 - y0);
    const scr = (p) => { const [x, y] = xy(p); return [pad + (x - x0) * sc, pad + (y1 - y) * sc]; };
    const usable = segs.filter((s) => q !== 'steer' || s.straight);
    const mags = usable.map((s) => Math.abs(s[q])).sort((a, b) => a - b);
    const vmax = Math.max(q === 'steer' ? 0.3 : 0.02, mags[Math.floor(0.95 * (mags.length - 1))] || 0);
    const g = el('g', {}, svg), hits = el('g', {}, svg);
    segs.forEach((s) => {
      const pts = s.points.map(scr);
      const [ax, ay] = pts[0], [bx, by] = pts[pts.length - 1];
      const len = Math.hypot(bx - ax, by - ay) || 1;
      const ox = (-(by - ay) / len) * 4.5, oy = ((bx - ax) / len) * 4.5;   // 진행 방향 오른쪽으로 비켜 그린다
      const path = pts.map(([x, y]) => `${(x + ox).toFixed(1)},${(y + oy).toFixed(1)}`).join(' ');
      const measured = q !== 'steer' || s.straight;
      el('polyline', { points: path, fill: 'none', 'stroke-linecap': 'round', 'stroke-width': 4,
        stroke: measured ? divColor(s[q], vmax) : css('--none'), 'stroke-dasharray': measured ? '' : '3 6' }, g);
      const hit = el('polyline', { points: path, fill: 'none', stroke: 'transparent', 'stroke-width': 14, 'pointer-events': 'stroke' }, hits);
      hit.addEventListener('mousemove', (ev) => {
        const body = measured
          ? `<div class="row"><span>차종 ${st.model} 길 몫</span><b>${fmt(q, s[q])}</b></div>
             <div class="row"><span>전체 차종 평균</span><span>${fmt(q, s[`${q}_all`])}</span></div>
             <div class="row"><span>같은 차종 보고</span><span>${s[`n_${q}`].toLocaleString()}건</span></div>`
          : '<div>굽은 길이라 핸들 각도를 재지 않아요</div>';
        showTip(`<b>${s.id}</b>${body}`, ev);
      });
      hit.addEventListener('mouseleave', hideTip);
    });
    // 범례
    const ramp = $('ramp');
    ramp.replaceChildren();
    const lo = document.createElement('span'); lo.textContent = q === 'steer' ? `왼쪽 ${fmt(q, -vmax)}` : `덜 씀 ${fmt(q, -vmax)}`;
    ramp.appendChild(lo);
    [-3, -2, -1, 0, 1, 2, 3].forEach((b) => {
      const sw = document.createElement('span'); sw.className = 'sw'; sw.style.background = divColor((b / 3) * vmax, vmax); ramp.appendChild(sw);
    });
    const hi = document.createElement('span'); hi.textContent = q === 'steer' ? `오른쪽 ${fmt(q, vmax)}` : `더 씀 ${fmt(q, vmax)}`;
    ramp.appendChild(hi);
    if (q === 'steer') { const n = document.createElement('span'); n.textContent = '· 점선: 굽은 길(재지 않음)'; ramp.appendChild(n); }
    $('mapTitle').textContent = q === 'steer' ? '구간마다 핸들을 돌리게 만드는 길 몫' : '구간마다 에너지를 더·덜 쓰게 만드는 길 몫';
    $('mapDesc').textContent = q === 'steer'
      ? `차종 ${st.model}(${modelName(st.model)}) 기준. 빗물이 빠지게 기운 길에서는 똑바로 갈 때도 핸들을 조금 돌립니다. 그 몫을 빼야 '내 차 몫'이 남아요. 방향마다 진행 방향 오른쪽에 그렸어요.`
      : `차종 ${st.model}(${modelName(st.model)}) 기준. 오르막·내리막처럼 그 구간에 늘 있는 몫이에요. 방향마다 진행 방향 오른쪽에 그렸어요.`;
  }

  // ── 날짜별 에너지 몫 ──────────────────────────────────────────
  function niceTicks(lo, hi, n = 5) {
    const step0 = (hi - lo) / n, p = 10 ** Math.floor(Math.log10(step0));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= step0) || step0;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
    return out;
  }
  function drawDaily() {
    const svg = $('daily'), daily = st.data.daily.eff;
    svg.replaceChildren();
    $('dailyLegend').replaceChildren();
    if (!daily) { $('dailyDesc').textContent = '아직 보고가 없어요.'; return; }
    const ids = st.models.map((m) => m.id);
    const T = daily.values[ids[0]].length;
    const series = ids.map((id) => daily.values[id].map((v, t) => (daily.counts[id][t] > 0 ? pct(v) : null)));
    const all = series.flat().filter((v) => v !== null);
    if (!all.length) return;
    let lo = Math.min(0, ...all), hi = Math.max(0, ...all);
    const padv = (hi - lo) * 0.08 || 1; lo -= padv; hi += padv;
    const W = 560, H = 300, L = 44, R = 12, Tp = 10, B = 28;
    const x = (t) => L + (T <= 1 ? 0 : (t / (T - 1)) * (W - L - R));
    const y = (v) => Tp + ((hi - v) / (hi - lo)) * (H - Tp - B);
    niceTicks(lo, hi).forEach((v) => {
      el('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: v === 0 ? css('--axis') : css('--grid'), 'stroke-width': 1 }, svg);
      const t = el('text', { x: L - 6, y: y(v) + 4, 'text-anchor': 'end', 'font-size': 11, fill: css('--muted') }, svg);
      t.textContent = `${v > 0 ? '+' : ''}${v}%`;
    });
    for (let t = 0; t < T; t += 7) {
      const tx = el('text', { x: x(t), y: H - 8, 'text-anchor': 'middle', 'font-size': 11, fill: css('--muted') }, svg);
      tx.textContent = dayLabel(daily.day0 + t);
    }
    ids.forEach((id, i) => {
      const pts = series[i].map((v, t) => (v === null ? null : `${x(t).toFixed(1)},${y(v).toFixed(1)}`)).filter(Boolean);
      el('polyline', { points: pts.join(' '), fill: 'none', stroke: css(SERIES[i]), 'stroke-width': id === st.model ? 3 : 2,
        'stroke-linejoin': 'round', 'stroke-linecap': 'round', 'stroke-dasharray': isEv(id) ? '' : '6 4' }, svg);
      const lg = document.createElement('span');
      lg.innerHTML = `<i style="background:${css(SERIES[i])}"></i>${id} ${modelName(id)}${isEv(id) ? '' : ' (점선)'}`;
      $('dailyLegend').appendChild(lg);
    });
    const cross = el('line', { y1: Tp, y2: H - B, stroke: css('--axis'), 'stroke-width': 1, visibility: 'hidden' }, svg);
    const hit = el('rect', { x: L, y: Tp, width: W - L - R, height: H - Tp - B, fill: 'transparent' }, svg);
    hit.addEventListener('mousemove', (ev) => {
      const box = svg.getBoundingClientRect();
      const px = ((ev.clientX - box.left) / box.width) * W;
      const t = Math.max(0, Math.min(T - 1, Math.round(((px - L) / (W - L - R)) * (T - 1))));
      cross.setAttribute('x1', x(t)); cross.setAttribute('x2', x(t)); cross.setAttribute('visibility', 'visible');
      const rows = ids.map((id, i) => `<div class="row"><span><i style="background:${css(SERIES[i])}"></i>${id} ${modelName(id)}</span>
        <b>${series[i][t] === null ? '–' : `${series[i][t] > 0 ? '+' : ''}${series[i][t].toFixed(1)}%`}</b></div>`).join('');
      showTip(`<b>${dayLabel(daily.day0 + t)}</b>${rows}`, ev);
    });
    hit.addEventListener('mouseleave', () => { cross.setAttribute('visibility', 'hidden'); hideTip(); });
    $('dailyTitle').textContent = '날짜별 에너지 몫 (차종마다)';
    $('dailyDesc').textContent = '기간 평균 대비. 날씨가 추워질수록 전기차(실선)는 에너지를 더 쓰고 내연기관(점선)은 거의 그대로예요. '
      + '차종을 가리지 않고 한 값으로 빼면 이 차이가 그대로 오경보가 됩니다.';
    // 표
    const head = `<tr><th>날짜</th>${ids.map((id) => `<th>${id}</th>`).join('')}</tr>`;
    const body = [...Array(T).keys()].map((t) => `<tr><td>${dayLabel(daily.day0 + t)}</td>${
      ids.map((_, i) => `<td>${series[i][t] === null ? '–' : `${series[i][t].toFixed(1)}%`}</td>`).join('')}</tr>`).join('');
    $('dailyTable').innerHTML = `<table>${head}${body}</table>`;
  }

  function drawScales() {
    const s = st.data.scales;
    if (!s.steer || !s.eff) { $('scales').innerHTML = '<div class="empty">보고가 쌓이면 나와요.</div>'; return; }
    const rows = st.models.map((m) => `<tr class="${m.id === st.model ? 'me' : ''}"><td>${m.id} ${m.name}</td>
      <td>${s.steer.static[m.id].toFixed(2)}×</td><td>${s.eff.static[m.id].toFixed(2)}×</td><td>${s.eff.daily[m.id].toFixed(2)}×</td></tr>`).join('');
    $('scales').innerHTML = `<table><tr><th>차종</th><th>길 기울기 → 핸들</th><th>오르막 → 에너지</th><th>날씨 → 에너지</th></tr>${rows}</table>`;
  }

  function drawSim() {
    const c = st.compare;
    if (!c) return;
    const fmtDays = (v) => (v === null || v === undefined ? '–' : `${v}일`);
    const rows = STRATEGY_ORDER.filter((k) => c.strategies[k]).map((k) => {
      const r = c.strategies[k], a = r.all;
      return `<tr class="${k === 'fleet_model' ? 'me' : ''}"><td>${r.label}</td><td>${a.detected}/${a.faults}</td>
        <td>${fmtDays(a.median_days)}</td><td>${Math.round(100 * r.alarm_precision)}%</td>
        <td>${r.false_per_1000_car_days}</td><td>${r.false_per_1000_by_powertrain.ev} / ${r.false_per_1000_by_powertrain.ice}</td></tr>`;
    }).join('');
    $('sim').innerHTML = `<table><tr><th>길 몫을 구하는 방식</th><th>찾은 고장</th><th>찾기까지(중앙값)</th>
      <th>알림 중 진짜 고장</th><th>오경보 (1,000대·일당)</th><th>전기차 / 내연기관</th></tr>${rows}</table>`;
    $('simMeta').textContent = `차 ${c.cars}대 × ${c.days}일, 고장 ${c.strategies.fleet_model?.all.faults ?? '–'}대`;
  }

  function render() {
    if (!st.data) return;
    const d = st.data;
    $('kReports').textContent = ((d.reports.steer || 0) + (d.reports.eff || 0)).toLocaleString();
    $('kDay').textContent = dayLabel(d.server_day);
    $('kSegs').textContent = d.segments.length;
    drawMap(); drawDaily(); drawScales(); drawSim();
  }

  async function refresh() {
    try {
      const r = await fetch(`/v1/state?model=${encodeURIComponent(st.model)}`);
      if (!r.ok) return;
      st.data = await r.json();
      if (!st.models.length) {
        st.models = st.data.models;
        $('model').innerHTML = st.models.map((m) => `<option value="${m.id}">${m.id} ${m.name}</option>`).join('');
        $('model').value = st.model;
      }
      render();
    } catch (e) { /* 서버가 잠깐 없을 때는 다음 차례에 다시 */ }
  }
  async function refreshCompare() {
    try { const r = await fetch('/v1/demo/compare'); if (r.ok) { st.compare = await r.json(); drawSim(); } } catch (e) { /* 없음 */ }
  }

  $('mSteer').addEventListener('click', () => { st.metric = 'steer'; $('mSteer').setAttribute('aria-pressed', 'true'); $('mEff').setAttribute('aria-pressed', 'false'); render(); });
  $('mEff').addEventListener('click', () => { st.metric = 'eff'; $('mEff').setAttribute('aria-pressed', 'true'); $('mSteer').setAttribute('aria-pressed', 'false'); render(); });
  $('model').addEventListener('change', (e) => { st.model = e.target.value; refresh(); });
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', render);
  refresh(); refreshCompare();
  setInterval(refresh, 3000);
  setInterval(refreshCompare, 30000);
})();
