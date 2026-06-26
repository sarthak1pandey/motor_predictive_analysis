/* ============================================================
   app.js — Phase 6 rewrite
   Real schema: rpm, current, torque, dc_voltage, temperature, vibration
   + derived power (dc_voltage × current, labeled "derived")
   + settings/account dropdown
   + fault diagnosis panel
   ============================================================ */

const API_BASE = "";

const PARAM_META = {
  rpm:         { label: "Speed",       unit: "RPM",  color: "var(--c-rpm)",         range: [0,   270],  derived: false },
  set_rpm:     { label: "Set Speed",   unit: "RPM",  color: "var(--c-set_rpm)",     range: [0,   270],  derived: false },
  current:     { label: "Current",     unit: "A",    color: "var(--c-current)",      range: [0,   80],   derived: false },
  torque:      { label: "Torque",      unit: "N·m",  color: "var(--c-torque)",       range: [-140, 140], derived: false },
  dc_voltage:  { label: "DC Bus V",    unit: "V",    color: "var(--c-dc_voltage)",   range: [500, 600],  derived: false },
  temperature: { label: "Temperature", unit: "°C",   color: "var(--c-temperature)",  range: [20,  80],   derived: false },
  vibration:   { label: "Vibration",   unit: "mm/s", color: "var(--c-vibration)",    range: [0,   6],    derived: false },
  power:       { label: "Power",       unit: "kW",   color: "var(--c-power)",        range: [0,   50],   derived: true  },
  slip:        { label: "Slip",        unit: "RPM",  color: "var(--c-slip)",         range: [0,   20],   derived: true  },
};

const ICONS = {
  rpm:         '<path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4"/><circle cx="12" cy="12" r="4"/>',
  set_rpm:     '<path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4"/><circle cx="12" cy="12" r="4"/>',
  current:     '<path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z"/>',
  torque:      '<path d="M21 12a9 9 0 1 1-9-9"/><path d="M21 3v6h-6"/>',
  dc_voltage:  '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  temperature: '<path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z"/>',
  vibration:   '<path d="M2 12h2l2-7 3 14 3-10 2 5h8"/>',
  power:       '<path d="M19 14a7 7 0 1 1-14 0M12 2v10"/>',
  slip:        '<path d="M15 14l-3-3-3 3"/><path d="M12 11v10"/><path d="M12 3a9 9 0 0 0-9 9"/>',
};

const FAULT_LABELS = {
  bearing_shaft:       "Bearing / Shaft",
  overload:            "Overload",
  coupling_slip:       "Coupling Slip",
  voltage_supply:      "Voltage Supply",
  winding_overcurrent: "Winding Overcurrent",
  thermal_overload:    "Thermal Overload",
  mechanical_imbalance:"Mech. Imbalance",
  slip_fault:          "Slip Fault",
};

const FAULT_COLORS = {
  bearing_shaft:       "#4FC1E0",
  overload:            "#F2545D",
  coupling_slip:       "#F2A93B",
  voltage_supply:      "#9D8DF1",
  winding_overcurrent: "#E58FB3",
  thermal_overload:    "#FF8C42",
  mechanical_imbalance:"#5FD3A6",
  slip_fault:          "#A2E4B8",
};

function svgIcon(key, color, size = 14) {
  const d = ICONS[key] || '<circle cx="12" cy="12" r="5"/>';
  return `<svg viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2.1" width="${size}" height="${size}">${d}</svg>`;
}

/* ── State ─────────────────────────────────────────────────────── */
let focusParam = "vibration";
let compareKeys = ["current", "vibration"];
let currentRange = "7d";
let trainingDays = 15;
let pollInterval = 2000;
let pollTimer = null;
let settings = {};

/* ── Navigation ─────────────────────────────────────────────────── */
document.querySelectorAll(".nav-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    document.querySelectorAll(".page").forEach(p => p.classList.add("hidden"));
    document.getElementById(`page-${btn.dataset.page}`).classList.remove("hidden");
    if (btn.dataset.page === "history") loadHistory();
    if (btn.dataset.page === "ml")      loadMLStatus();
    if (btn.dataset.page === "thresholds") renderThresholdsUI();
  });
});

/* ── Account / Settings dropdown ────────────────────────────────── */
const accountBtn      = document.getElementById("account-btn");
const accountDropdown = document.getElementById("account-dropdown");
const accountMotorId  = document.getElementById("account-motor-id");

accountBtn.addEventListener("click", e => {
  e.stopPropagation();
  accountDropdown.classList.toggle("hidden");
});
document.addEventListener("click", () => accountDropdown.classList.add("hidden"));
accountDropdown.addEventListener("click", e => e.stopPropagation());

async function loadSettings() {
  try {
    const r = await fetch(`${API_BASE}/api/settings`);
    settings = await r.json();
    document.getElementById("set-motor-id").value    = settings.motor_id    || "";
    document.getElementById("set-location").value    = settings.location    || "";
    document.getElementById("set-alert-email").value = settings.alert_email || "";
    document.getElementById("set-poll").value        = settings.poll_interval_s || 2;
    accountMotorId.textContent = settings.motor_id || "M-014";
    document.getElementById("live-subtitle").textContent =
      `Motor unit ${settings.motor_id || "M-014"} · ${settings.location || "local SQL"} · polled every ${settings.poll_interval_s || 2}s`;
  } catch {}
}

document.getElementById("settings-save-btn").addEventListener("click", async () => {
  const body = {
    motor_id:        document.getElementById("set-motor-id").value.trim(),
    location:        document.getElementById("set-location").value.trim(),
    alert_email:     document.getElementById("set-alert-email").value.trim(),
    poll_interval_s: parseInt(document.getElementById("set-poll").value, 10) || 2,
  };
  await fetch(`${API_BASE}/api/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  settings = body;
  accountMotorId.textContent = body.motor_id || "M-014";
  document.getElementById("live-subtitle").textContent =
    `Motor unit ${body.motor_id} · ${body.location} · polled every ${body.poll_interval_s}s`;
  pollInterval = (body.poll_interval_s || 2) * 1000;
  restartPoll();

  const savedEl = document.getElementById("dropdown-saved");
  savedEl.classList.remove("hidden");
  setTimeout(() => savedEl.classList.add("hidden"), 1800);
});

document.getElementById("db-reset-btn").addEventListener("click", async () => {
  if (!confirm("Are you sure you want to completely wipe the database and reset the ML model state?")) return;
  await fetch(`${API_BASE}/api/db/reset`, { method: "POST" });
  window.location.reload();
});

/* ── Thresholds ─────────────────────────────────────────────────── */
function renderThresholdsUI() {
  const grid = document.getElementById("thresholds-grid");
  grid.innerHTML = "";
  const thresholds = settings.thresholds || {};

  Object.entries(PARAM_META).forEach(([key, meta]) => {
    if (key === "set_rpm" || key === "slip") return;
    const t = thresholds[key] || { min: meta.range[0], max: meta.range[1] };
    const card = document.createElement("div");
    card.className = "threshold-card";
    card.innerHTML = `
      <div class="threshold-card-head">
        ${svgIcon(key, colorFromVar(meta.color))}
        ${meta.label} (${meta.unit})
      </div>
      <div class="threshold-inputs">
        <label>Min <input type="number" step="any" id="t-min-${key}" value="${t.min}"></label>
        <label>Max <input type="number" step="any" id="t-max-${key}" value="${t.max}"></label>
      </div>
    `;
    grid.appendChild(card);
  });
}

document.getElementById("save-thresholds-btn").addEventListener("click", async () => {
  const btn = document.getElementById("save-thresholds-btn");
  btn.textContent = "Saving...";
  
  const thresholds = {};
  Object.keys(PARAM_META).forEach(key => {
    if (key === "set_rpm" || key === "slip") return;
    const minVal = parseFloat(document.getElementById(`t-min-${key}`)?.value);
    const maxVal = parseFloat(document.getElementById(`t-max-${key}`)?.value);
    if (!isNaN(minVal) && !isNaN(maxVal)) {
      thresholds[key] = { min: minVal, max: maxVal };
    }
  });

  await fetch(`${API_BASE}/api/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ thresholds }),
  });
  
  settings.thresholds = thresholds;
  
  setTimeout(() => {
    btn.textContent = "Save Thresholds";
  }, 1000);
});

/* ── SVG chart helpers ──────────────────────────────────────────── */

function buildLinePath(values, w, h, padX = 4, padY = 6, domain = null) {
  if (!values.length) return { line: "", area: "" };
  const min = domain ? domain[0] : Math.min(...values);
  const max = domain ? domain[1] : Math.max(...values);
  const span = max - min || 1;
  const stepX = (w - padX * 2) / (values.length - 1 || 1);

  const pts = values.map((v, i) => {
    const x = padX + i * stepX;
    const y = padY + (1 - (v - min) / span) * (h - padY * 2);
    return [x, y];
  });

  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const area = `${line} L${pts.at(-1)[0].toFixed(1)},${h - padY} L${pts[0][0].toFixed(1)},${h - padY} Z`;
  return { line, area, pts };
}

function renderSparkline(svgEl, values, color) {
  const w = 200, h = 36;
  const { line, area } = buildLinePath(values, w, h, 2, 3);
  const gid = `grad-${color.replace(/[^a-z0-9]/gi, "")}`;
  svgEl.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svgEl.innerHTML = `
    <defs>
      <linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${color}" stop-opacity="0.35"/>
        <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
      </linearGradient>
    </defs>
    <path d="${area}" fill="url(#${gid})" stroke="none"/>
    <path d="${line}" fill="none" stroke="${color}" stroke-width="1.6"/>
  `;
}

function colorFromVar(varExpr) {
  const name = varExpr.match(/--[\w-]+/)[0];
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function niceTicks(min, max, count = 5) {
  const range = max - min || 1;
  const raw = range / (count - 1);
  const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
  const nice = [1, 2, 2.5, 5, 10].find(f => f * magnitude >= raw) * magnitude;
  const lo = Math.floor(min / nice) * nice;
  const ticks = [];
  for (let v = lo; v <= max + nice * 0.01; v = parseFloat((v + nice).toFixed(10))) {
    if (v >= min - nice * 0.01) ticks.push(parseFloat(v.toFixed(6)));
    if (ticks.length >= count + 2) break;
  }
  return ticks.filter(t => t >= min - nice && t <= max + nice);
}

function renderTimeseriesChart(svgEl, series, colors, opts = {}) {
  const w = 880, h = opts.height || 220;
  const padX = 48, padY = 16, padBottom = 28;
  const chartW = w - padX * 2;
  const chartH = h - padY - padBottom;

  const allValues = series.flatMap(s => s.values);
  if (!allValues.length) { svgEl.innerHTML = ""; return; }

  const allTimes = series[0].times;
  const minV = Math.min(...allValues);
  const maxV = Math.max(...allValues);
  const minT = 0;
  const maxT = allTimes.length - 1 || 1;

  const px = i => padX + (i / maxT) * chartW;
  const py = v => padY + (1 - (v - minV) / (maxV - minV || 1)) * chartH;

  let svg = `<g class="chart-gridlines">`;

  // Y ticks
  const yTicks = niceTicks(minV, maxV, 5);
  yTicks.forEach(tick => {
    const y = py(tick);
    if (y < padY || y > padY + chartH) return;
    svg += `<line x1="${padX}" y1="${y.toFixed(1)}" x2="${w - padX}" y2="${y.toFixed(1)}" stroke="#2C343C" stroke-width="1"/>`;
    svg += `<text x="${padX - 6}" y="${(y + 4).toFixed(1)}" text-anchor="end" fill="#5C6671" font-size="9" font-family="IBM Plex Mono,monospace">${tick % 1 === 0 ? tick : tick.toFixed(1)}</text>`;
  });

  // X ticks (5 labels)
  const xTickCount = 5;
  for (let i = 0; i <= xTickCount; i++) {
    const idx = Math.round((i / xTickCount) * (allTimes.length - 1));
    const x = px(idx);
    const label = allTimes[idx] ? new Date(allTimes[idx]).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
    svg += `<line x1="${x.toFixed(1)}" y1="${padY}" x2="${x.toFixed(1)}" y2="${padY + chartH}" stroke="#2C343C" stroke-width="1"/>`;
    svg += `<text x="${x.toFixed(1)}" y="${padY + chartH + 14}" text-anchor="middle" fill="#5C6671" font-size="9" font-family="IBM Plex Mono,monospace">${label}</text>`;
  }

  svg += `</g>`;

  series.forEach((s, idx) => {
    const color = typeof colors[idx] === "string" && colors[idx].startsWith("var(")
      ? colorFromVar(colors[idx]) : colors[idx];
    const { line, area } = buildLinePath(s.values, w, h + padY - padBottom, padX, padY, [minV, maxV]);
    const gid = `g-${idx}-${color.replace(/[^a-z0-9]/gi, "")}`;
    svg += `
      <defs>
        <linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${color}" stop-opacity="0.25"/>
          <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <path d="${area}" fill="url(#${gid})" stroke="none"/>
      <path d="${line}" fill="none" stroke="${color}" stroke-width="1.7"/>
    `;
  });

  svgEl.innerHTML = svg;
}

function renderScatterChart(svgEl, sX, sY, colorX, colorY, labelX, labelY) {
  const w = 880, h = 260;
  const padX = 48, padY = 16, padBottom = 28;
  const chartW = w - padX * 2;
  const chartH = h - padY - padBottom;

  if (!sX.values.length || !sY.values.length) { svgEl.innerHTML = ""; return; }

  const minX = Math.min(...sX.values);
  const maxX = Math.max(...sX.values);
  const minY = Math.min(...sY.values);
  const maxY = Math.max(...sY.values);

  const px = v => padX + ((v - minX) / (maxX - minX || 1)) * chartW;
  const py = v => padY + (1 - (v - minY) / (maxY - minY || 1)) * chartH;

  let svg = `<g class="chart-gridlines">`;

  // Y ticks
  const yTicks = niceTicks(minY, maxY, 5);
  yTicks.forEach(tick => {
    const y = py(tick);
    if (y < padY || y > padY + chartH) return;
    svg += `<line x1="${padX}" y1="${y.toFixed(1)}" x2="${w - padX}" y2="${y.toFixed(1)}" stroke="#2C343C" stroke-width="1"/>`;
    svg += `<text x="${padX - 6}" y="${(y + 4).toFixed(1)}" text-anchor="end" fill="#5C6671" font-size="9" font-family="IBM Plex Mono,monospace">${tick % 1 === 0 ? tick : tick.toFixed(1)}</text>`;
  });

  // X ticks
  const xTicks = niceTicks(minX, maxX, 7);
  xTicks.forEach(tick => {
    const x = px(tick);
    if (x < padX || x > padX + chartW) return;
    svg += `<line x1="${x.toFixed(1)}" y1="${padY}" x2="${x.toFixed(1)}" y2="${padY + chartH}" stroke="#2C343C" stroke-width="1"/>`;
    svg += `<text x="${x.toFixed(1)}" y="${padY + chartH + 14}" text-anchor="middle" fill="#5C6671" font-size="9" font-family="IBM Plex Mono,monospace">${tick % 1 === 0 ? tick : tick.toFixed(1)}</text>`;
  });
  
  // Axis labels
  svg += `<text x="${w/2}" y="${h - 2}" text-anchor="middle" fill="#8A95A1" font-size="10" font-family="Inter,sans-serif">${labelX}</text>`;
  svg += `<text x="${12}" y="${h/2}" text-anchor="middle" fill="#8A95A1" font-size="10" font-family="Inter,sans-serif" transform="rotate(-90 12 ${h/2})">${labelY}</text>`;

  svg += `</g>`;

  // Draw points
  let pointsStr = "";
  const len = Math.min(sX.values.length, sY.values.length);
  for (let i = 0; i < len; i++) {
    const cx = px(sX.values[i]);
    const cy = py(sY.values[i]);
    pointsStr += `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="3" fill="${colorY}" opacity="0.6"/>`;
  }

  svgEl.innerHTML = svg + pointsStr;
}

function renderAnomalyChart(svgEl, points) {
  const w = 600, h = 180, padX = 10, padY = 12, padBottom = 8;
  const chartH = h - padY - padBottom;
  const values = points.map(p => p.score);
  if (!values.length) return;

  const { line, area } = buildLinePath(values, w, h, padX, padY, [0, 1]);
  const warnY  = padY + (1 - 0.35) * chartH;
  const anomY  = padY + (1 - 0.60) * chartH;
  const critY  = padY + (1 - 0.80) * chartH;

  svgEl.innerHTML = `
    <defs>
      <linearGradient id="ag" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#F2545D" stop-opacity="0.4"/>
        <stop offset="100%" stop-color="#F2545D" stop-opacity="0"/>
      </linearGradient>
    </defs>
    <line x1="${padX}" y1="${warnY.toFixed(1)}" x2="${w-padX}" y2="${warnY.toFixed(1)}" stroke="#F2A93B" stroke-width="1" stroke-dasharray="3,3"/>
    <line x1="${padX}" y1="${anomY.toFixed(1)}" x2="${w-padX}" y2="${anomY.toFixed(1)}" stroke="#F2545D" stroke-width="1" stroke-dasharray="3,3"/>
    <line x1="${padX}" y1="${critY.toFixed(1)}" x2="${w-padX}" y2="${critY.toFixed(1)}" stroke="#FF3B30" stroke-width="1" stroke-dasharray="3,3"/>
    <path d="${area}" fill="url(#ag)" stroke="none"/>
    <path d="${line}" fill="none" stroke="#F2545D" stroke-width="2"/>
    <text x="${w-padX-2}" y="${warnY-3}" text-anchor="end" fill="#F2A93B" font-size="8" font-family="IBM Plex Mono,monospace">warn</text>
    <text x="${w-padX-2}" y="${anomY-3}" text-anchor="end" fill="#F2545D" font-size="8" font-family="IBM Plex Mono,monospace">anomaly</text>
    <text x="${w-padX-2}" y="${critY-3}" text-anchor="end" fill="#FF3B30" font-size="8" font-family="IBM Plex Mono,monospace">critical</text>
  `;
}

/* ── Live Monitoring ────────────────────────────────────────────── */

function buildParamCards(series) {
  const grid = document.getElementById("param-grid");
  if (grid.children.length === Object.keys(series).length) return; // already built

  grid.innerHTML = "";
  Object.entries(PARAM_META).forEach(([key, meta]) => {
    if (!series[key] || key === "set_rpm") return;
    const card = document.createElement("div");
    card.className = "param-card";
    card.dataset.key = key;
    const col = colorFromVar(meta.color);
    const derivedBadge = meta.derived ? `<span class="derived-badge">DERIVED</span>` : "";
    card.innerHTML = `
      <div class="panel-head" style="margin-bottom:0;">
        <div class="panel-title">
          ${svgIcon(key, col)}
          ${meta.label}${derivedBadge}
        </div>
        <div style="text-align:right">
          <span class="param-card-value" style="margin:0;"><span class="num" id="val-${key}">—</span> <span class="unit">${meta.unit}</span></span>
        </div>
      </div>
      <svg class="chart-svg" id="chart-${key}" viewBox="0 0 880 220" preserveAspectRatio="none" style="height:160px; margin-top:10px;"></svg>
    `;
    grid.appendChild(card);
  });
}

async function loadLive() {
  try {
    const r = await fetch(`${API_BASE}/api/live?window=60`);
    const data = await r.json();

    buildParamCards(data.series || {});

    Object.entries(data.series || {}).forEach(([key, s]) => {
      if (key === "set_rpm") return;
      const valEl = document.getElementById(`val-${key}`);
      if (valEl) valEl.textContent = s.latest != null ? s.latest : "—";

      const chartEl = document.getElementById(`chart-${key}`);
      if (chartEl) {
        let toRender = [{ values: s.points.map(p => p.value), times: s.points.map(p => p.time) }];
        let colors = [colorFromVar(PARAM_META[key].color)];
        
        if (key === "rpm" && data.series["set_rpm"]) {
          const sr = data.series["set_rpm"];
          toRender.push({ values: sr.points.map(p => p.value), times: sr.points.map(p => p.time) });
          colors.push(colorFromVar(PARAM_META["set_rpm"].color));
        }
        
        renderTimeseriesChart(chartEl, toRender, colors, { height: 160 });
      }
      
      // Threshold check
      const cardEl = document.querySelector(`.param-card[data-key="${key}"]`);
      if (cardEl && settings.thresholds && settings.thresholds[key] && s.latest != null) {
        const t = settings.thresholds[key];
        if (s.latest < t.min || s.latest > t.max) {
          cardEl.classList.add("alert");
        } else {
          cardEl.classList.remove("alert");
        }
      }
    });

    // status pill
    const pill = document.getElementById("motor-status-pill");
    pill.className = "status-pill " + (data.motor_on ? "on" : "off");
    pill.innerHTML = `<span class="dot"></span> MOTOR ${data.motor_on ? "ON" : "OFF"}`;

    // clock
    const ts = new Date(data.timestamp);
    document.getElementById("live-clock").innerHTML =
      `<span class="dot amber"></span> LIVE · ${ts.toLocaleTimeString()}`;

  } catch (e) {
    console.error("live fetch failed", e);
  }
}

function startLivePoll() {
  loadLive();
  pollTimer = setInterval(loadLive, pollInterval);
}

function restartPoll() {
  clearInterval(pollTimer);
  pollInterval = (settings.poll_interval_s || 2) * 1000;
  pollTimer = setInterval(loadLive, pollInterval);
}

/* ── Historical Analytics ───────────────────────────────────────── */

let singleParamKey   = "rpm";
let corrXKey         = "rpm";
let corrYKey         = "temperature";

function buildHistorySelectors(series) {
  const paramKeys = Object.keys(PARAM_META).filter(k => k !== "set_rpm" && series[k]);

  function buildRow(containerId, activeKey, onSelect) {
    const container = document.getElementById(containerId);
    container.innerHTML = "";
    paramKeys.forEach(key => {
      const meta = PARAM_META[key];
      const col  = colorFromVar(meta.color);
      const btn  = document.createElement("button");
      btn.className = "hist-chip" + (key === activeKey ? " active" : "");
      btn.style.setProperty("--chip-color", col);
      btn.textContent = meta.label + (meta.derived ? " ◇" : "");
      btn.dataset.key = key;
      btn.addEventListener("click", () => {
        container.querySelectorAll(".hist-chip").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        onSelect(key);
      });
      container.appendChild(btn);
    });
  }

  // Section 1 — single param
  buildRow("single-param-chips", singleParamKey, key => {
    singleParamKey = key;
    renderSingleParamChart(window._historyData);
  });

  // Section 2 — correlation X
  buildRow("corr-x-chips", corrXKey, key => {
    corrXKey = key;
    renderCorrChart(window._historyData);
  });

  // Section 2 — correlation Y
  buildRow("corr-y-chips", corrYKey, key => {
    corrYKey = key;
    renderCorrChart(window._historyData);
  });
}

function renderSingleParamChart(data) {
  if (!data || !data.series) return;
  const key  = singleParamKey;
  const s    = data.series[key];
  if (!s) return;

  const meta = PARAM_META[key];
  const col  = colorFromVar(meta.color);

  let toRender = [{ values: s.points.map(p => p.value), times: s.points.map(p => p.time) }];
  let colors   = [col];
  let title    = meta.label + " over time";

  // Overlay set_rpm on speed chart
  if (key === "rpm" && data.series["set_rpm"]) {
    const sr = data.series["set_rpm"];
    toRender.push({ values: sr.points.map(p => p.value), times: sr.points.map(p => p.time) });
    colors.push(colorFromVar(PARAM_META["set_rpm"].color));
    title = "Speed & Set Speed over time";
  }

  document.getElementById("history-title-single").textContent = title;
  document.getElementById("history-meta-single").textContent  =
    s.points.length ? `${s.points.length} data points` : "";
  renderTimeseriesChart(
    document.getElementById("history-chart-single"),
    toRender, colors, { height: 240 }
  );
}

function renderCorrChart(data) {
  if (!data || !data.series) return;
  const sx = data.series[corrXKey];
  const sy = data.series[corrYKey];
  if (!sx || !sy) return;

  const metaX = PARAM_META[corrXKey];
  const metaY = PARAM_META[corrYKey];
  const colX  = colorFromVar(metaX.color);
  const colY  = colorFromVar(metaY.color);

  document.getElementById("history-title-corr").textContent =
    `${metaX.label} (${metaX.unit}) vs ${metaY.label} (${metaY.unit})`;

  renderScatterChart(
    document.getElementById("history-chart-corr"),
    { values: sx.points.map(p => p.value) },
    { values: sy.points.map(p => p.value) },
    colX, colY, `${metaX.label} (${metaX.unit})`, `${metaY.label} (${metaY.unit})`
  );
}

function buildStatGrid(stats) {
  const grid = document.getElementById("stat-grid");
  grid.innerHTML = "";
  Object.entries(stats).forEach(([key, s]) => {
    const meta = PARAM_META[key] || { label: key, unit: "", derived: false };
    if (key === "set_rpm") return;
    const derivedBadge = meta.derived ? `<span class="derived-badge">DERIVED</span>` : "";
    const card = document.createElement("div");
    card.className = "stat";
    card.innerHTML = `
      <div class="stat-label">${meta.label}${derivedBadge}</div>
      <div class="stat-value">${s.avg != null ? s.avg : "—"} <span style="font-size:10px;color:var(--text-faint)">${meta.unit}</span></div>
      <div style="font-size:10px;color:var(--text-faint);margin-top:2px">min ${s.min} · max ${s.max}</div>
    `;
    grid.appendChild(card);
  });
}

async function loadHistory() {
  const r    = await fetch(`${API_BASE}/api/history?range=${currentRange}`);
  const data = await r.json();
  window._historyData = data;
  buildHistorySelectors(data.series || {});
  renderSingleParamChart(data);
  renderCorrChart(data);
  buildStatGrid(data.stats || {});
}

// range buttons
document.getElementById("range-group").addEventListener("click", e => {
  const btn = e.target.closest("[data-range]");
  if (!btn) return;
  currentRange = btn.dataset.range;
  document.querySelectorAll("#range-group .chip").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  loadHistory();
});

// export CSV
document.getElementById("export-csv-btn").addEventListener("click", () => {
  window.location.href = `${API_BASE}/api/history/export?range=${currentRange}`;
});

/* ── ML / Predictive Maintenance ────────────────────────────────── */

function renderFaultBars(diagnosis) {
  const container = document.getElementById("fault-bars");
  const pill = document.getElementById("top-fault-pill");
  if (!diagnosis || !diagnosis.scores) {
    container.innerHTML = `<div style="color:var(--text-faint);font-size:12px">Train model to enable fault diagnosis</div>`;
    pill.textContent = "—";
    return;
  }

  const top = diagnosis.top_fault;
  pill.textContent = top ? (FAULT_LABELS[top] || top) : "—";

  const sorted = Object.entries(diagnosis.scores).sort((a, b) => b[1] - a[1]);
  container.innerHTML = sorted.map(([fault, score]) => {
    const color = FAULT_COLORS[fault] || "#888";
    const pct = (score * 100).toFixed(1);
    const isTop = fault === top;
    return `
      <div class="fault-bar-row ${isTop ? "top-fault" : ""}">
        <div class="fault-bar-label">${FAULT_LABELS[fault] || fault}</div>
        <div class="fault-bar-track">
          <div class="fault-bar-fill" style="width:${pct}%;background:${color}${isTop ? "" : "88"}"></div>
        </div>
        <div class="fault-bar-value">${pct}%</div>
      </div>
    `;
  }).join("");
}

async function loadMLStatus() {
  try {
    const r = await fetch(`${API_BASE}/api/ml/status`);
    const data = await r.json();

    const deployed = data.status === "MODEL_DEPLOYED";
    document.getElementById("training-status-badge").textContent =
      deployed ? "MODEL DEPLOYED" : "MACHINE UNDER TRAINING";
    document.getElementById("training-status-badge").style.background =
      deployed ? "rgba(74,222,128,0.12)" : "rgba(242,169,59,0.12)";
    document.getElementById("training-status-badge").style.color =
      deployed ? "var(--healthy)" : "var(--warning)";
    document.getElementById("training-status-badge").style.borderColor =
      deployed ? "rgba(74,222,128,0.3)" : "rgba(242,169,59,0.3)";

    const pct = data.progress_pct || 0;
    document.getElementById("training-progress-fill").style.width = `${pct}%`;
    document.getElementById("stat-progress").textContent = `${pct.toFixed(1)}%`;
    document.getElementById("stat-records").textContent = (data.records_collected || 0).toLocaleString();
    document.getElementById("stat-window").textContent = `${data.duration_hours} hours`;

    const comp = data.completion_date ? new Date(data.completion_date) : null;
    document.getElementById("stat-completes").textContent = comp
      ? comp.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })
      : "—";

    const lock = document.getElementById("inference-lock");
    if (!deployed) lock.classList.remove("hidden"); else lock.classList.add("hidden");

    if (data.ready_to_train && !deployed) {
      await fetch(`${API_BASE}/api/ml/train`, { method: "POST" });
      loadMLStatus();
      return;
    }

    if (deployed) {
      const [scoreR, histR, faultR] = await Promise.all([
        fetch(`${API_BASE}/api/ml/live-score`),
        fetch(`${API_BASE}/api/ml/anomaly-history`),
        fetch(`${API_BASE}/api/ml/fault-diagnosis`),
      ]);
      const score = await scoreR.json();
      const hist  = await histR.json();
      const fault = await faultR.json();

      document.getElementById("score-value").textContent = score.score != null ? score.score.toFixed(4) : "—";

      const pill = document.getElementById("health-pill");
      pill.textContent = score.health || "—";
      const healthColor = {
        "Healthy": "var(--healthy)", "Warning": "var(--warning)",
        "Anomaly Detected": "var(--critical)", "Critical Condition": "#FF3B30"
      };
      pill.style.background = "transparent";
      pill.style.color = healthColor[score.health] || "var(--text-dim)";
      pill.style.borderColor = healthColor[score.health] || "var(--hairline)";

      if (hist.points && hist.points.length) renderAnomalyChart(document.getElementById("anomaly-chart"), hist.points);

      renderFaultBars(fault);
    } else {
      renderFaultBars(null);
    }
  } catch (e) {
    console.error("ML status failed", e);
  }
}



/* ── Boot ────────────────────────────────────────────────────────── */

// Set default active range chip
document.querySelector('#range-group [data-range="7d"]')?.classList.add("active");

loadSettings();
startLivePoll();
