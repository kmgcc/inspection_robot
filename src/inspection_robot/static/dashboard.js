/**
 * dashboard.js — 仓库巡逻看板前端逻辑
 *
 * 功能分区：
 * 1. 标签页切换
 * 2. 主状态轮询（/api/status）
 * 3. 测试状态轮询（/api/test/status）
 * 4. 运动测试控制（直行 / 转向 / 寻线）
 * 5. 标定参数读写
 * 6. 急停（任意位置）
 * 7. 事件表、货架卡片、拓扑图渲染
 */

"use strict";

(() => {

// ============================================================
// 常量与标签映射
// ============================================================

const STATUS_LABELS = {
  IDLE: "待命", STARTING: "启动中", GIMBAL_INIT: "云台初始化",
  PATROLLING: "巡逻中", PLANNING: "规划中", PLAN_READY: "路径就绪",
  MOVING: "移动中", TURNING_AT_BOUNDARY: "列端转向", ALIGNING_SHELF: "对准货架",
  SCANNING_SHELF: "扫描货架", ANALYZING: "分析中", FIRST_PASS_LEARNING: "第一轮学习",
  NORMAL_LOGGED: "正常已记录", ABNORMAL_ALARM: "异常告警", WAIT_CONFIRM: "等待确认",
  CONFIRMED: "已确认", OBSTACLE_WAIT: "障碍等待", AVOIDING_OBSTACLE: "绕行避障",
  NESTED_AVOIDANCE: "嵌套避障", REROUTING: "重规划", FORBIDDEN_ZONE_WAIT: "禁区等待",
  MANUAL_CONTROL: "手动控制", FINISHED: "任务完成", STOPPED: "已停止", ERROR: "错误",
};

const EVENT_TYPE_LABELS = {
  system: "系统", runtime_started: "运行启动", runtime_stopped: "运行停止",
  manual_control: "手动控制", motion_debug: "运动调试", gimbal_initialized: "云台初始化",
  cycle_started: "轮次开始", cycle_completed: "轮次完成",
  boundary_full_black: "四路全黑", boundary_turn: "列端转向", unexpected_boundary: "非预期黑胶带",
  obstacle_avoidance_started: "开始绕行", obstacle_avoidance_step: "绕行动作",
  audio_cue: "音频提示", path_planned: "路径规划", path_step: "路径推进",
  forbidden_zone_detected: "禁区触发", obstacle_wait: "障碍等待", obstacle_clear: "障碍解除",
  shelf_arrived: "到达货架", shelf_scanned: "货架扫描", normal_item: "正常物品",
  unknown_item: "未知物品", wrong_shelf: "错放", missing_item: "缺失",
  duplicate_item: "重复", evidence_mismatch: "证据冲突", untagged_evidence: "无二维码证据",
  scan_failed: "扫描失败", light_cue: "灯光提示", manual_confirm: "人工确认",
};

const DIRECTION_LABELS = {
  forward: "前进", backward: "后退", cw: "顺时针(CW)", ccw: "逆时针(CCW)",
  left: "左转", right: "右转",
};

const PATROL_ORDER = ["A1","A2","A3","A4","B4","B3","B2","B1"];

let latestEventId = null;
let latestPendingEvent = null;
let testPollingActive = false;
let testPollTimer = null;
let latestVideoFrameId = null;

// ============================================================
// 工具函数
// ============================================================

function byId(id) { return document.getElementById(id); }

function textOrDash(v) {
  if (v === null || v === undefined || v === "") return "-";
  if (Array.isArray(v)) return v.length > 0 ? v.join(", ") : "-";
  return String(v);
}

function setText(id, value) {
  const el = byId(id);
  if (el) el.textContent = textOrDash(value);
}

function showEl(id, show) {
  const el = byId(id);
  if (el) el.hidden = !show;
}

function labelFrom(map, v) { return map[v] || textOrDash(v); }
function asArray(v) { return Array.isArray(v) ? v : []; }
function isPending(e) { return e && e.status === "waiting_confirm"; }

function normalizeShelfId(v) {
  const t = textOrDash(v);
  if (t === "-") return null;
  const m = t.toUpperCase().match(/[A-Z][0-9]+/);
  return m ? m[0] : t;
}

async function postJson(url, body = {}) {
  let res;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    throw new Error(`网络请求失败：${err.message || err}`);
  }
  let payload;
  try { payload = await res.json(); } catch (_) { payload = {}; }
  if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);
  return payload;
}

function formatElapsed(seconds) {
  if (seconds === null || seconds === undefined) return "-";
  const s = Math.round(Number(seconds));
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m${s % 60}s`;
}

function formatVector3(v, unit = "") {
  if (!v || typeof v !== "object") return "-";
  const parts = ["x", "y", "z"].map((axis) => {
    const value = v[axis];
    if (value === null || value === undefined || Number.isNaN(Number(value))) return `${axis}:-`;
    return `${axis}:${Number(value).toFixed(2)}`;
  });
  return `${parts.join(" / ")}${unit ? ` ${unit}` : ""}`;
}

// ============================================================
// 1. 标签页切换
// ============================================================

function initTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tab;
      // 切换按钮
      document.querySelectorAll(".tab-btn").forEach((b) => {
        b.classList.toggle("active", b.dataset.tab === target);
        b.setAttribute("aria-selected", b.dataset.tab === target ? "true" : "false");
      });
      // 切换面板
      document.querySelectorAll(".tab-panel").forEach((p) => {
        const isTarget = p.id === `panel-${target}`;
        p.hidden = !isTarget;
      });
      // 进入测试标签时刷新标定参数显示
      if (target === "test" || target === "status") {
        loadCalibration();
      }
    });
  });
}

// ============================================================
// 2. 主状态轮询
// ============================================================

async function loadStatus() {
  try {
    const res = await fetch("/api/status", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderStatus(data || {});
  } catch (err) {
    setText("last_message", `看板刷新失败：${err.message}`);
  }
}

async function loadVideoDetections() {
  try {
    const res = await fetch("/api/video/detections", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    latestVideoFrameId = data.frame_id || null;
    renderDetectionItems("live-detections", asArray(data.detections), { frame_id: latestVideoFrameId });
  } catch (_) {
    renderDetectionItems("live-detections", [], { frame_id: latestVideoFrameId });
  }
}

function renderStatus(data) {
  const events = asArray(data.events);
  const scan = data.scan || {};
  const obstacle = data.obstacle || {};
  const alarm = data.alarm || {};
  const boundary = data.boundary || {};
  const audio = data.audio || {};
  const gimbal = data.gimbal || {};
  const motionSensor = data.motion_sensor || {};
  const pending = events.filter(isPending);
  latestEventId = pending.length > 0 ? pending[0].id : null;
  latestPendingEvent = pending.length > 0 ? pending[0] : null;

  const currentShelf = data.current_shelf || data.current_zone;
  const isSimulate = !data.run_mode || data.run_mode === "simulate";

  // 手动控制标签
  setText("header_status", data.robot_status || labelFrom(STATUS_LABELS, data.task_status));
  setText("run_id", data.run_id);
  setText("run_mode", data.run_mode || "simulate");
  setText("hardware_connected", data.hardware_connected ? "已连接" : "未连接");
  setText("task_status", labelFrom(STATUS_LABELS, data.task_status));
  setText("patrol_cycle", `${data.patrol_cycle || 1} / ${data.skip_shortage_detection ? "跳过缺货" : "检测缺货"}`);
  setText("motion_sensor_ok", motionSensor.ok ? "可用" : "不可用");
  setText("motion_accel", formatVector3(motionSensor.accel_mps2));
  setText("motion_gyro", formatVector3(motionSensor.gyro_dps));
  renderPosePreview(motionSensor);
  setText("last_message", data.last_message);

  // simulate 模式提示
  const manualHint = byId("manual-hint");
  if (manualHint) manualHint.hidden = !isSimulate;

  // 状态标签
  setText("s-run_mode", data.run_mode || "simulate");
  setText("s-hardware_connected", data.hardware_connected ? "已连接" : "未连接");
  setText("s-task_status", labelFrom(STATUS_LABELS, data.task_status));
  setText("s-current_shelf", currentShelf);
  setText("s-current_target", data.current_target);
  setText("s-pose_state", data.pose ? `(${data.pose.x}, ${data.pose.y}, ${data.pose.heading || "-"})` : "-");
  setText("s-obstacle_state", obstacleLine(obstacle));
  setText("s-boundary_state", boundaryLine(boundary));
  setText("s-alarm_state", alarm.message ? `${alarm.level} / ${alarm.message}` : alarm.level);
  setText("s-gimbal_state", gimbal.side_initialized ? `侧向 ${textOrDash(gimbal.yaw)}°/${textOrDash(gimbal.pitch)}°` : "未初始化");
  setText("s-pending_count", pending.length);
  setText("status-motion-ok", motionSensor.ok ? `可用 / ${motionSensor.zero_drift_compensated ? "已扣零漂" : "未补偿"}` : "不可用");
  setText("status-motion-accel", formatVector3(motionSensor.accel_mps2));
  setText("status-motion-gyro", formatVector3(motionSensor.gyro_dps));
  setText("status-motion-bias", formatVector3(motionSensor.gyro_bias_dps));
  setText("status-motion-error", motionSensor.last_error || `采样时间：${textOrDash(motionSensor.sample_time)}，温度：${textOrDash(motionSensor.temperature_c)}°C`);

  document.body.dataset.alarm = pending.length > 0 ? "warning" : alarm.level || "normal";

  // 测试标签：simulate 横幅
  showEl("test-simulate-banner", isSimulate);

  renderMap(data, events);
  renderShelves(data, events);
  renderDetections(scan, events);
  renderEvents(events);
}

function renderPosePreview(sensor) {
  const orientation = sensor.orientation_deg || {};
  const roll = numericOrZero(orientation.roll);
  const pitch = numericOrZero(orientation.pitch);
  const yaw = numericOrZero(orientation.yaw);
  const car = byId("pose-car");
  if (car) {
    car.style.setProperty("--roll", `${clamp(roll, -35, 35)}deg`);
    car.style.setProperty("--pitch", `${clamp(pitch, -35, 35)}deg`);
    car.style.setProperty("--yaw", `${yaw}deg`);
    car.classList.toggle("unavailable", !sensor.ok);
  }
  setText("pose-roll", sensor.ok ? `${roll.toFixed(1)}°` : "-");
  setText("pose-pitch", sensor.ok ? `${pitch.toFixed(1)}°` : "-");
  setText("pose-yaw", sensor.ok ? `${yaw.toFixed(1)}°` : "-");

  const lastTurn = sensor.last_turn || {};
  if (lastTurn && Object.keys(lastTurn).length > 0) {
    const okText = lastTurn.ok ? "收敛" : "未收敛";
    const finalDeg = lastTurn.final_degrees == null ? "-" : `${Number(lastTurn.final_degrees).toFixed(1)}°`;
    const errorDeg = lastTurn.error_degrees == null ? "-" : `${Number(lastTurn.error_degrees).toFixed(1)}°`;
    setText(
      "pose-turn-summary",
      `最近转向：${okText} / ${lastTurn.direction || "-"} / axis ${lastTurn.turn_axis || "-"} / ${lastTurn.attempts || 0} 次 / 角度 ${finalDeg} / 误差 ${errorDeg}`
    );
  } else if (sensor.ok) {
    setText("pose-turn-summary", `采样时间：${textOrDash(sensor.sample_time)}，温度：${textOrDash(sensor.temperature_c)}°C`);
  } else {
    setText("pose-turn-summary", sensor.last_error || "MPU6050 不可用");
  }
}

function numericOrZero(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function clamp(value, minValue, maxValue) {
  return Math.min(maxValue, Math.max(minValue, value));
}

function obstacleLine(obstacle) {
  const d = obstacle.distance_mm == null ? "-" : `${obstacle.distance_mm} mm`;
  return `${obstacle.blocked ? "阻塞" : "通畅"} / ${d}`;
}

function boundaryLine(boundary) {
  const tape = asArray(boundary.tape_state);
  const t = tape.length > 0 ? tape.join("") : "-";
  return `${boundary.full_black ? "四路全黑" : (boundary.kind || "无触发")} / ${t}`;
}

// ============================================================
// 3. 测试状态轮询
// ============================================================

function startTestPolling() {
  if (testPollingActive) return;
  testPollingActive = true;
  pollTestStatus();
}

function stopTestPolling() {
  testPollingActive = false;
  if (testPollTimer) { clearTimeout(testPollTimer); testPollTimer = null; }
}

async function pollTestStatus() {
  if (!testPollingActive) return;
  try {
    const res = await fetch("/api/test/status", { cache: "no-store" });
    if (res.ok) {
      const data = await res.json();
      renderTestStatus(data);
    }
  } catch (_) { /* 忽略轮询失败 */ }
  testPollTimer = setTimeout(pollTestStatus, 500);
}

function renderTestStatus(data) {
  // 直行测试区
  if (data.test_type === "straight" || (!data.active && !data.test_type)) {
    setText("st-direction", data.direction ? labelFrom(DIRECTION_LABELS, data.direction) : "-");
    setText("st-speed-disp", data.active ? data.speed : "-");
    setText("st-elapsed", data.active ? formatElapsed(data.elapsed_seconds) : formatElapsed(data.elapsed_seconds));
    setText("st-state", testStateLabel(data));
  }
  // 转向测试区
  if (data.test_type === "turn") {
    setText("turn-direction", labelFrom(DIRECTION_LABELS, data.direction));
    setText("turn-speed-disp", data.speed);
    setText("turn-duration-disp", `${data.duration_seconds}s`);
    setText("turn-elapsed", formatElapsed(data.elapsed_seconds));
    setText("turn-state", testStateLabel(data));
    byId("turn-param-summary").textContent =
      `方向: ${labelFrom(DIRECTION_LABELS, data.direction)} | 速度: ${data.speed} | 时长: ${data.duration_seconds}s`;
  }
  // 寻线测试区
  if (data.test_type === "line_follow") {
    setText("lf-state", testStateLabel(data));
    setText("lf-elapsed", formatElapsed(data.elapsed_seconds));
  }
  // 距离（通用）
  const distText = data.distance_mm != null ? `${data.distance_mm} mm` : "-";
  setText("lf-distance", distText);
  setText("status-distance", distText);

  // 传感器可视化
  renderSensorVisual("sv", data.line_sensor, data.line_description);
  renderSensorVisual("svs", data.line_sensor, data.line_description);
  setText("lf-decision", data.line_description || "-");
  byId("lf-decision")?.setAttribute("class", "lf-decision " + decisionClass(data.line_description));
  setText("status-line-desc", data.line_description || "-");
}

function testStateLabel(data) {
  if (data.active) return "运行中 ▶";
  if (!data.stop_reason) return "待机";
  const labels = {
    completed: "✅ 已完成",
    manual: "⏹ 手动停止",
    error: `❌ 错误: ${data.error_message || ""}`,
    timeout: "⏱ 超时停止",
  };
  return labels[data.stop_reason] || data.stop_reason;
}

function decisionClass(desc) {
  if (!desc) return "";
  if (desc.includes("居中")) return "centered";
  if (desc.includes("右转修正")) return "correct-right";
  if (desc.includes("左转修正")) return "correct-left";
  if (desc.includes("丢线") || desc.includes("全白")) return "lost-line";
  if (desc.includes("异常")) return "sensor-error";
  return "";
}

function renderSensorVisual(prefix, sensors, desc) {
  if (!sensors) return;
  const names = ["左", "左中", "右中", "右"];
  sensors.forEach((val, i) => {
    const cell = byId(`${prefix}-${i}`);
    if (!cell) return;
    const isBlack = val === 0;
    cell.classList.toggle("black", isBlack);
    cell.classList.toggle("white", !isBlack);
    const valEl = cell.querySelector(".sv-val");
    if (valEl) valEl.textContent = val;
  });
}

// ============================================================
// 4. 运动测试控制
// ============================================================

function getTestSpeed(inputId) {
  return Math.max(0, Math.min(100, parseInt(byId(inputId)?.value || "22", 10)));
}

function getTestDuration(inputId) {
  return Math.max(0.05, parseFloat(byId(inputId)?.value || "2"));
}

async function runTest(path, body) {
  try {
    await postJson(path, body);
    startTestPolling();
  } catch (err) {
    const isSimulate = err.message.includes("RUN_MODE") || err.message.includes("simulate");
    if (isSimulate) {
      showEl("test-simulate-banner", true);
    } else {
      window.alert(`测试失败：${err.message}`);
    }
  }
}

async function stopTest() {
  try {
    await postJson("/api/test/stop");
  } catch (_) { /* 停止失败不弹窗 */ }
}

function initTestControls() {
  // ---- 直行速度调整按钮 ----
  byId("st-speed-minus5")?.addEventListener("click", () => adjustInput("st-speed", -5));
  byId("st-speed-minus1")?.addEventListener("click", () => adjustInput("st-speed", -1));
  byId("st-speed-plus1")?.addEventListener("click", () => adjustInput("st-speed", +1));
  byId("st-speed-plus5")?.addEventListener("click", () => adjustInput("st-speed", +5));

  // ---- 直行时长预设 ----
  document.querySelectorAll("[data-st-dur]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = byId("st-duration");
      if (input) input.value = btn.dataset.stDur;
    });
  });

  // ---- 直行测试按钮 ----
  byId("btn-st-forward")?.addEventListener("click", () => {
    const speed = getTestSpeed("st-speed");
    const dur = getTestDuration("st-duration");
    setText("st-state", "启动中…");
    runTest("/api/test/straight", { direction: "forward", speed, duration_seconds: dur });
  });

  byId("btn-st-backward")?.addEventListener("click", () => {
    const speed = getTestSpeed("st-speed");
    const dur = getTestDuration("st-duration");
    setText("st-state", "启动中…");
    runTest("/api/test/straight", { direction: "backward", speed, duration_seconds: dur });
  });

  byId("btn-st-stop")?.addEventListener("click", () => stopTest());

  // ---- 保存最低稳定直行速度 ----
  byId("btn-save-min-speed")?.addEventListener("click", async () => {
    const speed = getTestSpeed("st-speed");
    try {
      await postJson("/api/calibration", { straight_min_speed: speed, straight_speed: speed });
      setText("saved-min-speed", speed);
      await loadCalibration();
    } catch (err) {
      window.alert(`保存失败：${err.message}`);
    }
  });

  // ---- 转向时长预设 ----
  document.querySelectorAll("[data-turn-dur]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = byId("turn-duration");
      if (input) input.value = btn.dataset.turnDur;
    });
  });

  // ---- 转向测试按钮 ----
  byId("btn-turn-cw")?.addEventListener("click", () => {
    const speed = getTestSpeed("turn-speed");
    const dur = getTestDuration("turn-duration");
    setText("turn-state", "启动中…");
    runTest("/api/test/turn", { direction: "cw", speed, duration_seconds: dur });
    byId("turn-param-summary").textContent =
      `方向: 顺时针(CW) | 速度: ${speed} | 时长: ${dur}s`;
  });

  byId("btn-turn-ccw")?.addEventListener("click", () => {
    const speed = getTestSpeed("turn-speed");
    const dur = getTestDuration("turn-duration");
    setText("turn-state", "启动中…");
    runTest("/api/test/turn", { direction: "ccw", speed, duration_seconds: dur });
    byId("turn-param-summary").textContent =
      `方向: 逆时针(CCW) | 速度: ${speed} | 时长: ${dur}s`;
  });

  byId("btn-turn-stop")?.addEventListener("click", () => stopTest());

  // ---- 保存转向90°参数 ----
  byId("btn-save-cw90")?.addEventListener("click", async () => {
    const speed = getTestSpeed("turn-speed");
    const dur = getTestDuration("turn-duration");
    const comp = parseFloat(byId("cw-comp")?.value || "1.0");
    try {
      await postJson("/api/calibration", {
        turn_speed: speed,
        turn_cw90_seconds: dur,
        cw_compensation: comp,
      });
      setText("saved-cw90", `速度${speed} / ${dur}s / 补偿×${comp}`);
      await loadCalibration();
    } catch (err) { window.alert(`保存失败：${err.message}`); }
  });

  byId("btn-save-ccw90")?.addEventListener("click", async () => {
    const speed = getTestSpeed("turn-speed");
    const dur = getTestDuration("turn-duration");
    const comp = parseFloat(byId("ccw-comp")?.value || "1.0");
    try {
      await postJson("/api/calibration", {
        turn_speed: speed,
        turn_ccw90_seconds: dur,
        ccw_compensation: comp,
      });
      setText("saved-ccw90", `速度${speed} / ${dur}s / 补偿×${comp}`);
      await loadCalibration();
    } catch (err) { window.alert(`保存失败：${err.message}`); }
  });

  // ---- 寻线测试 ----
  byId("btn-lf-start")?.addEventListener("click", () => {
    const speed = getTestSpeed("lf-speed");
    const step = getTestDuration("lf-step");
    setText("lf-state", "启动中…");
    runTest("/api/test/line_follow/start", { speed, step_seconds: step });
  });

  byId("btn-lf-stop")?.addEventListener("click", () => stopTest());
}

function adjustInput(id, delta) {
  const input = byId(id);
  if (!input) return;
  const val = parseInt(input.value, 10) + delta;
  input.value = Math.max(Number(input.min || 0), Math.min(Number(input.max || 100), val));
}

// ============================================================
// 5. 标定参数
// ============================================================

async function loadCalibration() {
  try {
    const res = await fetch("/api/calibration", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    const cal = data.calibration || {};
    renderCalibration(cal);
  } catch (_) { /* 忽略 */ }
}

function renderCalibration(cal) {
  const uncal = !!cal._uncalibrated;

  // 横幅
  showEl("test-uncalibrated-banner", uncal);
  showEl("status-uncal-banner", uncal);

  // 更新保存值显示
  const minSpeed = cal.straight_min_speed;
  setText("saved-min-speed", minSpeed != null ? minSpeed : "待标定");
  const cw90 = cal.turn_cw90_seconds;
  setText("saved-cw90", cw90 != null ? `速度${cal.turn_speed || 18} / ${cw90}s` : "待标定");
  const ccw90 = cal.turn_ccw90_seconds;
  setText("saved-ccw90", ccw90 != null ? `速度${cal.turn_speed || 18} / ${ccw90}s` : "待标定");

  // 更新手动控制默认速度与时长（若是默认值，自动拉取标定值）
  const manualSpeed = byId("manual-speed");
  if (manualSpeed && cal.straight_speed && (manualSpeed.value === "22" || !manualSpeed.value)) {
    manualSpeed.value = cal.straight_speed;
  }
  const manualDuration = byId("manual-duration");
  if (manualDuration && cal.straight_step_seconds && (manualDuration.value === "0.14" || !manualDuration.value)) {
    manualDuration.value = cal.straight_step_seconds;
  }

  // 寻线速度提示
  const lfHint = byId("lf-speed-hint");
  if (lfHint) {
    if (minSpeed != null) {
      lfHint.textContent = `（已标定最低稳定速度: ${minSpeed}）`;
      const lfInput = byId("lf-speed");
      if (lfInput && lfInput.value === lfInput.defaultValue) {
        lfInput.value = minSpeed;
      }
    } else {
      lfHint.textContent = "（最低稳定速度未标定）";
    }
  }

  // 标定参数表格
  const LABELS = {
    straight_min_speed: "最低稳定直行速度",
    straight_speed: "直行默认速度",
    straight_step_seconds: "直行默认时长(s)",
    patrol_settle_seconds: "巡逻短停顿(s)",
    turn_speed: "转向速度",
    turn_cw90_seconds: "顺时针90°时长(s)",
    turn_ccw90_seconds: "逆时针90°时长(s)",
    cw_compensation: "顺时针补偿系数",
    ccw_compensation: "逆时针补偿系数",
    line_follow_speed: "寻线速度",
    line_follow_step_seconds: "寻线步长(s)",
  };

  const table = byId("cal-table");
  if (!table) return;
  table.innerHTML = "";
  for (const [key, label] of Object.entries(LABELS)) {
    const val = cal[key];
    const isEmpty = val === null || val === undefined;
    const div = document.createElement("dl");
    div.className = "cal-item" + (isEmpty ? " uncal" : "");
    div.innerHTML = `<dt>${label}</dt><dd>${isEmpty ? "待标定" : val}</dd>`;
    table.appendChild(div);
  }
}

// ============================================================
// 6. 急停（全局）
// ============================================================

function initEmergencyStop() {
  byId("btn-emergency-stop")?.addEventListener("click", async () => {
    await Promise.allSettled([
      postJson("/api/stop"),
      postJson("/api/test/stop"),
    ]);
    await loadStatus();
  });

  // 页面关闭时自动急停
  window.addEventListener("beforeunload", () => {
    navigator.sendBeacon("/api/stop", JSON.stringify({}));
    navigator.sendBeacon("/api/test/stop", JSON.stringify({}));
  });
}

// ============================================================
// 7. 手动控制 + 巡逻按钮
// ============================================================

function initManualControls() {
  document.querySelectorAll("[data-manual-cmd]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const cmd = btn.dataset.manualCmd;
      const speed = parseInt(byId("manual-speed")?.value || "22", 10);
      const dur = parseFloat(byId("manual-duration")?.value || "0.14");
      try {
        await postJson(`/api/control/${cmd}`, { speed, duration_seconds: dur });
        await loadStatus();
      } catch (err) {
        const isSimulate = err.message.includes("simulate") || err.message.includes("RUN_MODE");
        if (!isSimulate) {
          window.alert(`手动控制失败：${err.message}`);
        } else {
          setText("last_message", "当前为 simulate 模式，手动控制不会发送到底盘。");
        }
      }
    });
  });
}

// ============================================================
// 事件代理：data-post 按钮 / data-confirm / data-audio
// ============================================================

document.addEventListener("click", async (e) => {
  const postBtn = e.target.closest("[data-post]");
  if (postBtn && !postBtn.closest("[data-manual-cmd]")) {
    try {
      await postJson(postBtn.dataset.post);
      await loadStatus();
    } catch (err) {
      window.alert(`操作失败：${err.message}`);
    }
    return;
  }

  if (e.target.closest("[data-confirm]")) {
    try {
      const evidence = latestPendingEvent && latestPendingEvent.evidence;
      if (evidence && evidence.reason === "camera_cycle_fallback_required") {
        await postJson("/api/cycle/confirm", latestEventId ? { event_id: latestEventId } : {});
      } else {
        await postJson("/api/confirm", latestEventId ? { event_id: latestEventId } : {});
      }
      await loadStatus();
    } catch (err) {
      window.alert(`确认失败：${err.message}`);
    }
    return;
  }

  if (e.target.closest("[data-audio]")) {
    try {
      const res = await fetch("/api/audio/play", { method: "POST" });
      const p = await res.json();
      if (!p.ok) window.alert(`音频播放失败：${p.error || "未知错误"}`);
    } catch (err) {
      window.alert(`音频播放失败：${err.message}`);
    }
  }
});

// ============================================================
// 地图拓扑渲染
// ============================================================

function renderMap(data, events) {
  const root = byId("warehouse_map");
  if (!root) return;
  root.innerHTML = "";
  const topology = data.topology || {};
  const nodes = asArray(topology.nodes);
  const edges = asArray(topology.edges);
  const currentNode = topology.current_node || data.current_shelf;
  const pending = new Set(events.filter(isPending).map((e) => normalizeShelfId(e.shelf_id)).filter(Boolean));

  if (nodes.length === 0) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "尚未生成巡检地图。开始巡逻并识别货架或列端后会在此显示。";
    root.appendChild(p);
    return;
  }

  for (const node of nodes) {
    const art = document.createElement("article");
    art.className = `topology-node ${node.kind || "node"}`;
    if (node.id === currentNode) art.classList.add("current");
    if (pending.has(normalizeShelfId(node.id))) art.classList.add("warning");
    const title = document.createElement("strong");
    title.textContent = textOrDash(node.label || node.id);
    const meta = document.createElement("span");
    meta.textContent = { shelf: "货架", boundary_turn: "列端转向", forbidden: "非预期禁区" }[node.kind] || node.kind;
    art.append(title, meta);
    root.appendChild(art);
  }

  if (edges.length > 0) {
    const p = document.createElement("p");
    p.className = "topology-edges";
    p.textContent = edges.map((e) => `${e[0]} -> ${e[1]}`).join("  |  ");
    root.appendChild(p);
  }
}

// ============================================================
// 货架卡片渲染
// ============================================================

function renderShelves(data, events) {
  const shelvesA = byId("shelves-a");
  const shelvesB = byId("shelves-b");
  const fallbackRoot = byId("shelves");

  if (shelvesA) shelvesA.innerHTML = "";
  if (shelvesB) shelvesB.innerHTML = "";
  if (fallbackRoot) fallbackRoot.innerHTML = "";

  const shelves = normalizeShelves(data, events);
  const SHELF_STATUS_LABELS = {
    pending: "未巡检", aligning: "对准中", scanning: "扫描中",
    normal: "正常", abnormal: "异常", waiting_confirm: "待确认",
  };
  for (const shelf of shelves) {
    const card = document.createElement("article");
    card.className = `shelf-card ${shelf.status}`;
    const title = document.createElement("div");
    title.className = "shelf-title";
    const h = document.createElement("h3");
    h.textContent = shelf.shelf_id;
    const badge = document.createElement("span");
    badge.className = `state-badge ${shelf.status}`;
    badge.textContent = SHELF_STATUS_LABELS[shelf.status] || shelf.status;
    title.append(h, badge);
    const details = document.createElement("dl");
    details.className = "shelf-details";
    addDetail(details, "AprilTag", shelf.tag_id || "-");
    addDetail(details, "OCR", shelf.ocr_text || shelf.shelf_id);
    addDetail(details, "最近物品", shelf.latest_item || "-");
    addDetail(details, "异常", shelf.anomaly_count || 0);
    card.append(title, details);

    const isA = shelf.shelf_id.toUpperCase().startsWith("A");
    if (isA && shelvesA) {
      shelvesA.appendChild(card);
    } else if (!isA && shelvesB) {
      shelvesB.appendChild(card);
    } else if (fallbackRoot) {
      fallbackRoot.appendChild(card);
    }
  }
}

function normalizeShelves(data, events) {
  const shelves = new Map();
  for (const id of PATROL_ORDER) {
    shelves.set(id, { shelf_id: id, status: "pending", anomaly_count: 0, latest_item: "-", tag_id: "-", ocr_text: id });
  }
  for (const shelf of asArray(data.shelves)) {
    const id = normalizeShelfId(shelf.shelf_id || shelf.id);
    if (!id) continue;
    shelves.set(id, { ...shelves.get(id) || { shelf_id: id }, ...shelf, shelf_id: id });
  }
  for (const event of [...events].reverse()) {
    const id = normalizeShelfId(event.shelf_id || event.expected_shelf || event.zone);
    if (!id) continue;
    const shelf = shelves.get(id) || { shelf_id: id, status: "pending", anomaly_count: 0 };
    if (event.item && event.item !== "-") shelf.latest_item = event.item;
    if (event.tag_id && event.type === "shelf_arrived") shelf.tag_id = event.tag_id;
    if (event.ocr_text) shelf.ocr_text = event.ocr_text;
    if (event.status === "waiting_confirm") {
      shelf.status = "waiting_confirm";
      shelf.anomaly_count = (shelf.anomaly_count || 0) + 1;
    } else if (event.status === "normal" && shelf.status !== "waiting_confirm") {
      shelf.status = "normal";
    }
    shelves.set(id, shelf);
  }
  return Array.from(shelves.values()).sort((a, b) => a.shelf_id.localeCompare(b.shelf_id));
}

function addDetail(root, label, value) {
  const wrap = document.createElement("div");
  const dt = document.createElement("dt");
  const dd = document.createElement("dd");
  dt.textContent = label;
  dd.textContent = textOrDash(value);
  wrap.append(dt, dd);
  root.appendChild(wrap);
}

// ============================================================
// 识别证据渲染
// ============================================================

function renderDetections(scan, events) {
  const detections = asArray(scan?.detections);
  const evidence = events.filter((e) => e.ocr_text || e.color || e.image_class || e.evidence);
  const items = detections.length > 0 ? detections : evidence.slice(0, 4);
  renderDetectionItems("detections", items, scan);
}

function renderDetectionItems(rootId, items, scan = {}) {
  const root = byId(rootId);
  if (!root) return;
  root.innerHTML = "";
  if (items.length === 0) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "暂无识别证据";
    root.appendChild(p);
    return;
  }
  for (const item of items.slice(0, 6)) {
    const card = document.createElement("article");
    card.className = "detection-card";
    const t = document.createElement("strong");
    const tagLabel = item.tag_id === null || item.tag_id === undefined || item.tag_id === "" ? "无二维码" : textOrDash(item.tag_id);
    t.textContent = `${tagLabel} / ${textOrDash(item.item || item.item_id || item.kind || item.type || "未归类")}`;
    const meta = document.createElement("span");
    const readableParts = [
      item.color ? `颜色 ${item.color}` : null,
      item.ocr_text ? `文字 ${item.ocr_text}` : "文字 未识别",
      item.image_class ? `图像 ${item.image_class}` : null,
      item.source ? `来源 ${item.source}` : null,
    ].filter(Boolean);
    meta.textContent = readableParts.length > 0 ? readableParts.join(" / ") : "-";
    const frame = document.createElement("small");
    frame.textContent = `帧：${textOrDash(item.frame_id || scan?.frame_id)}，类型：${textOrDash(item.marker_family || item.type)}`;
    card.append(t, meta, frame);
    root.appendChild(card);
  }
}

// ============================================================
// 事件日志渲染
// ============================================================

function renderEvents(events) {
  const tbody = byId("events");
  if (!tbody) return;
  tbody.innerHTML = "";
  if (events.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 12; td.className = "empty"; td.textContent = "暂无事件";
    tr.appendChild(td); tbody.appendChild(tr);
    return;
  }
  const STATUS_LABELS_EV = { normal: "正常", waiting_confirm: "待确认", confirmed: "已确认", info: "信息", warning: "警告", error: "错误" };
  for (const event of events) {
    const tr = document.createElement("tr");
    [
      event.time,
      labelFrom(EVENT_TYPE_LABELS, event.type),
      event.shelf_id || event.expected_shelf || event.zone,
      event.tag_id, event.item,
      event.expected_shelf || event.expected_zone,
    ].forEach((v) => appendCell(tr, v));
    const statusCell = document.createElement("td");
    const span = document.createElement("span");
    span.className = `badge ${event.status || "info"}`;
    span.textContent = STATUS_LABELS_EV[event.status] || event.status || "信息";
    statusCell.appendChild(span); tr.appendChild(statusCell);
    [event.source, event.ocr_text, event.color, event.image_class, event.message].forEach((v) => appendCell(tr, v));
    tbody.appendChild(tr);
  }
}

function appendCell(row, value) {
  const td = document.createElement("td");
  td.textContent = textOrDash(value);
  row.appendChild(td);
}

// ============================================================
// 初始化
// ============================================================

initTabs();
initEmergencyStop();
initManualControls();
initTestControls();

loadStatus();
loadVideoDetections();
loadCalibration();
startTestPolling();

setInterval(loadStatus, 1500);
setInterval(loadVideoDetections, 1500);

})();
