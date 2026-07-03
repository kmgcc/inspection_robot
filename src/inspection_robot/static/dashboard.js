const STATUS_LABELS = {
  IDLE: "待命",
  STARTING: "启动中",
  GIMBAL_INIT: "云台初始化",
  PATROLLING: "巡逻中",
  PLANNING: "规划中",
  PLAN_READY: "路径就绪",
  MOVING: "移动中",
  TURNING_AT_BOUNDARY: "列端转向",
  ALIGNING_SHELF: "对准货架",
  SCANNING_SHELF: "扫描货架",
  ANALYZING: "分析中",
  FIRST_PASS_LEARNING: "第一轮学习",
  NORMAL_LOGGED: "正常已记录",
  ABNORMAL_ALARM: "异常告警",
  WAIT_CONFIRM: "等待确认",
  CONFIRMED: "已确认",
  OBSTACLE_WAIT: "障碍等待",
  AVOIDING_OBSTACLE: "绕行避障",
  NESTED_AVOIDANCE: "嵌套避障",
  REROUTING: "重规划",
  FORBIDDEN_ZONE_WAIT: "禁区等待",
  MANUAL_CONTROL: "手动控制",
  FINISHED: "任务完成",
  STOPPED: "已停止",
  ERROR: "错误",
  PATROL: "移动中",
  TAG_DETECTED: "识别中",
  TURNING: "转向中",
};

const EVENT_TYPE_LABELS = {
  system: "系统",
  runtime_started: "运行启动",
  runtime_stopped: "运行停止",
  manual_control: "手动控制",
  gimbal_initialized: "云台初始化",
  shelf_detected: "货架识别",
  item_detected: "物品识别",
  first_pass_observed: "第一轮观察",
  cycle_started: "轮次开始",
  cycle_completed: "轮次完成",
  boundary_full_black: "四路全黑",
  boundary_turn: "列端转向",
  unexpected_boundary: "非预期黑胶带",
  obstacle_avoidance_started: "开始绕行",
  obstacle_avoidance_step: "绕行动作",
  obstacle_avoidance_nested: "嵌套避障",
  audio_cue: "音频提示",
  light_cue: "灯光提示",
  path_planned: "路径规划",
  path_step: "路径推进",
  path_replanned: "路径重规划",
  forbidden_zone_detected: "禁区触发",
  obstacle_wait: "障碍等待",
  obstacle_clear: "障碍解除",
  shelf_arrived: "到达货架",
  shelf_aligned: "货架对准",
  shelf_scanned: "货架扫描",
  normal_item: "正常物品",
  unknown_item: "未知物品",
  wrong_shelf: "错放",
  missing_item: "缺失",
  duplicate_item: "重复",
  evidence_mismatch: "证据冲突",
  manual_confirm: "人工确认",
  llm_summary: "摘要",
  normal_tag: "正常标签",
  unknown_tag: "未知标签",
  wrong_zone: "错放",
  missing_tag: "缺失",
  duplicate_tag: "重复",
};

const EVENT_STATUS_LABELS = {
  normal: "正常",
  waiting_confirm: "待确认",
  confirmed: "已确认",
  info: "信息",
  warning: "警告",
  error: "错误",
};

const SHELF_STATUS_LABELS = {
  pending: "未巡检",
  aligning: "对准中",
  scanning: "扫描中",
  normal: "正常",
  abnormal: "异常",
  waiting_confirm: "待确认",
};

const ALARM_LABELS = {
  normal: "正常",
  warning: "告警",
  danger: "严重",
  info: "提示",
};

const PATROL_ORDER = ["A1", "A2", "A3", "A4", "B4", "B3", "B2", "B1"];

let latestEventId = null;

function byId(id) {
  return document.getElementById(id);
}

function textOrDash(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (Array.isArray(value)) {
    return value.length > 0 ? value.join(", ") : "-";
  }
  return String(value);
}

function setText(id, value) {
  const node = byId(id);
  if (node) {
    node.textContent = textOrDash(value);
  }
}

function labelFrom(labels, value) {
  return labels[value] || textOrDash(value);
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function isPending(event) {
  return event && event.status === "waiting_confirm";
}

function normalizeShelfId(value) {
  const text = textOrDash(value);
  if (text === "-") return null;
  const match = text.toUpperCase().match(/[A-Z][0-9]+/);
  return match ? match[0] : text;
}

function latestDetection(scan, events) {
  const detections = asArray(scan?.detections);
  if (detections.length > 0) {
    return detections[0];
  }
  return events.find((event) => event.ocr_text || event.color || event.image_class) || null;
}

async function loadStatus() {
  try {
    const res = await fetch("/api/status", { cache: "no-store" });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    renderStatus(data || {});
  } catch (error) {
    setText("last_message", `看板刷新失败：${error.message}`);
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
  const pending = events.filter(isPending);
  const detection = latestDetection(scan, events);

  latestEventId = pending.length > 0 ? pending[0].id : null;

  const currentShelf = data.current_shelf || data.current_zone;
  const currentItem = data.current_item || detection?.item || detection?.item_id;
  const currentTag = data.current_tag || detection?.tag_id;
  const pose = data.pose || null;

  setText("run_id", data.run_id);
  setText("run_mode", data.run_mode || "simulate");
  setText("hardware_connected", data.hardware_connected ? "已连接" : "未连接/未验证");
  setText("header_status", data.robot_status || labelFrom(STATUS_LABELS, data.task_status));
  setText("task_status", labelFrom(STATUS_LABELS, data.task_status));
  setText("patrol_cycle", `${data.patrol_cycle || 1} / ${data.skip_shortage_detection ? "跳过缺货" : "检测缺货"}`);
  setText("current_target", data.current_target);
  setText("current_shelf", currentShelf);
  setText("current_item", currentItem || currentTag ? `${textOrDash(currentTag)} / ${textOrDash(currentItem)}` : "-");
  setText("shelf_ocr", detection?.ocr_text || scan.ocr_text);
  setText("evidence_summary", evidenceLine(detection));
  setText("obstacle_state", obstacleLine(obstacle));
  setText("boundary_state", boundaryLine(boundary));
  setText("alarm_state", alarm.message ? `${labelFrom(ALARM_LABELS, alarm.level)} / ${alarm.message}` : labelFrom(ALARM_LABELS, alarm.level));
  setText("audio_state", audio.last_error ? `${textOrDash(audio.last_cue)} / ${audio.last_error}` : textOrDash(audio.last_cue || audio.last_message));
  setText("gimbal_state", gimbal.side_initialized ? `侧向 ${textOrDash(gimbal.yaw)}°/${textOrDash(gimbal.pitch)}°` : "未初始化");
  setText("pose_state", pose ? `(${pose.x}, ${pose.y}, ${pose.heading || "-"})` : "-");
  setText("pending_count", pending.length);
  setText("last_message", data.last_message);

  const topology = data.topology || {};
  const nodes = asArray(topology.nodes);
  const edges = asArray(topology.edges);
  setText("path_status", `拓扑：${labelTopologyStatus(topology.status)}`);
  setText("path_counter", nodes.length > 0 ? `${nodes.length} 个节点 / ${edges.length} 条连接` : "尚未生成巡检地图");

  document.body.dataset.alarm = pending.length > 0 ? "warning" : alarm.level || "normal";
  renderMap(data, events);
  renderShelves(data, events);
  renderDetections(scan, events);
  renderEvents(events);
}

function labelPathStatus(status) {
  const labels = {
    idle: "待规划",
    planning: "规划中",
    active: "执行中",
    blocked: "受阻",
    complete: "完成",
  };
  return labels[status] || textOrDash(status);
}

function labelTopologyStatus(status) {
  const labels = {
    empty: "尚未生成",
    building: "生成中",
    ready: "已生成",
  };
  return labels[status] || textOrDash(status);
}

function obstacleLine(obstacle) {
  const distance = obstacle.distance_mm === null || obstacle.distance_mm === undefined ? "-" : `${obstacle.distance_mm} mm`;
  const wait = obstacle.waiting_seconds ? ` / 等待 ${obstacle.waiting_seconds}s` : "";
  return `${obstacle.blocked ? "阻塞" : "通畅"} / ${distance}${wait}`;
}

function boundaryLine(boundary) {
  const tape = asArray(boundary.tape_state);
  const tapeText = tape.length > 0 ? tape.join("") : "-";
  return `${boundary.full_black ? "四路全黑" : labelBoundaryKind(boundary.kind)} / ${tapeText}`;
}

function labelBoundaryKind(kind) {
  const labels = {
    none: "无触发",
    column_end: "列端",
    unexpected_partial: "局部黑胶带",
  };
  return labels[kind] || textOrDash(kind);
}

function evidenceLine(detection) {
  if (!detection) {
    return "-";
  }
  const parts = [
    detection.color ? `颜色 ${detection.color}` : null,
    detection.ocr_text ? `文字 ${detection.ocr_text}` : null,
    detection.image_class ? `图像 ${detection.image_class}` : null,
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(" / ") : "-";
}

function renderMap(data, events) {
  const root = byId("warehouse_map");
  root.innerHTML = "";
  const topology = data.topology || {};
  const nodes = asArray(topology.nodes);
  const edges = asArray(topology.edges);
  const currentNode = topology.current_node || data.current_shelf;
  const pendingShelves = new Set(events.filter(isPending).map((event) => normalizeShelfId(event.shelf_id)).filter(Boolean));

  root.className = "topology-list";
  if (nodes.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "尚未生成巡检地图。开始巡逻并识别货架或列端后，这里会显示运行中生成的拓扑。";
    root.appendChild(empty);
    return;
  }

  for (const node of nodes) {
    const item = document.createElement("article");
    const kind = node.kind || "node";
    item.className = `topology-node ${kind}`;
    if (node.id === currentNode) item.classList.add("current");
    if (pendingShelves.has(normalizeShelfId(node.id))) item.classList.add("warning");
    const title = document.createElement("strong");
    title.textContent = textOrDash(node.label || node.id);
    const meta = document.createElement("span");
    meta.textContent = topologyNodeLabel(kind);
    item.append(title, meta);
    root.appendChild(item);
  }

  if (edges.length > 0) {
    const edgeText = document.createElement("p");
    edgeText.className = "topology-edges";
    edgeText.textContent = edges.map((edge) => `${edge[0]} -> ${edge[1]}`).join("  |  ");
    root.appendChild(edgeText);
  }
}

function topologyNodeLabel(kind) {
  const labels = {
    shelf: "货架",
    boundary_turn: "列端转向",
    forbidden: "非预期禁区",
    node: "节点",
  };
  return labels[kind] || textOrDash(kind);
}

function renderShelves(data, events) {
  const root = byId("shelves");
  root.innerHTML = "";

  const shelves = normalizeShelves(data, events);
  for (const shelf of shelves) {
    const card = document.createElement("article");
    card.className = `shelf-card ${shelf.status}`;

    const title = document.createElement("div");
    title.className = "shelf-title";
    const heading = document.createElement("h3");
    heading.textContent = shelf.shelf_id;
    const badge = document.createElement("span");
    badge.className = `state-badge ${shelf.status}`;
    badge.textContent = SHELF_STATUS_LABELS[shelf.status] || textOrDash(shelf.status);
    title.append(heading, badge);

    const details = document.createElement("dl");
    details.className = "shelf-details";
    addDetail(details, "AprilTag", shelf.tag_id || "-");
    addDetail(details, "OCR", shelf.ocr_text || shelf.shelf_id);
    addDetail(details, "最近物品", shelf.latest_item || "-");
    addDetail(details, "异常", shelf.anomaly_count || 0);

    card.append(title, details);
    root.appendChild(card);
  }
}

function normalizeShelves(data, events) {
  const shelves = new Map();
  for (const shelfId of PATROL_ORDER) {
    shelves.set(shelfId, {
      shelf_id: shelfId,
      status: "pending",
      anomaly_count: 0,
      latest_item: "-",
      tag_id: "-",
      ocr_text: shelfId,
    });
  }

  for (const shelf of asArray(data.shelves)) {
    const shelfId = normalizeShelfId(shelf.shelf_id || shelf.id || shelf.name);
    if (!shelfId) continue;
    const current = shelves.get(shelfId) || { shelf_id: shelfId };
    shelves.set(shelfId, {
      ...current,
      ...shelf,
      shelf_id: shelfId,
      status: shelf.status || current.status || "pending",
      anomaly_count: Number(shelf.anomaly_count || current.anomaly_count || 0),
    });
  }

  for (const event of [...events].reverse()) {
    const shelfId = normalizeShelfId(event.shelf_id || event.expected_shelf || event.expected_zone || event.zone);
    if (!shelfId) continue;
    const shelf = shelves.get(shelfId) || { shelf_id: shelfId, status: "pending", anomaly_count: 0 };
    if (event.item && event.item !== "-") {
      shelf.latest_item = event.item;
    }
    if (event.tag_id && event.type === "shelf_arrived") {
      shelf.tag_id = event.tag_id;
    }
    if (event.ocr_text) {
      shelf.ocr_text = event.ocr_text;
    }
    if (event.status === "waiting_confirm") {
      shelf.status = "waiting_confirm";
      shelf.anomaly_count = Number(shelf.anomaly_count || 0) + 1;
    } else if (event.status === "normal" && shelf.status !== "waiting_confirm") {
      shelf.status = "normal";
    }
    shelves.set(shelfId, shelf);
  }

  return Array.from(shelves.values()).sort((left, right) => left.shelf_id.localeCompare(right.shelf_id));
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

function renderDetections(scan, events) {
  const root = byId("detections");
  root.innerHTML = "";
  const detections = asArray(scan?.detections);
  const evidenceEvents = events.filter((event) => event.ocr_text || event.color || event.image_class || event.evidence);
  const items = detections.length > 0 ? detections : evidenceEvents.slice(0, 4);

  if (items.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "暂无识别证据";
    root.appendChild(empty);
    return;
  }

  for (const item of items.slice(0, 6)) {
    const card = document.createElement("article");
    card.className = "detection-card";
    const title = document.createElement("strong");
    title.textContent = `${textOrDash(item.tag_id)} / ${textOrDash(item.item || item.item_id || item.kind)}`;
    const meta = document.createElement("span");
    meta.textContent = evidenceLine(item);
    const frame = document.createElement("small");
    frame.textContent = `帧：${textOrDash(item.frame_id || scan?.frame_id)}，类型：${textOrDash(item.marker_family || item.type)}`;
    card.append(title, meta, frame);
    root.appendChild(card);
  }
}

function renderEvents(events) {
  const tbody = byId("events");
  tbody.innerHTML = "";

  if (events.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 12;
    td.className = "empty";
    td.textContent = "暂无事件";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  for (const event of events) {
    const tr = document.createElement("tr");
    appendCell(tr, event.time);
    appendCell(tr, labelFrom(EVENT_TYPE_LABELS, event.type));
    appendCell(tr, event.shelf_id || event.expected_shelf || event.expected_zone || event.zone);
    appendCell(tr, event.tag_id);
    appendCell(tr, event.item);
    appendCell(tr, event.expected_shelf || event.expected_zone);
    const statusCell = document.createElement("td");
    const status = document.createElement("span");
    status.className = `badge ${event.status || "info"}`;
    status.textContent = labelFrom(EVENT_STATUS_LABELS, event.status);
    statusCell.appendChild(status);
    tr.appendChild(statusCell);
    appendCell(tr, event.source);
    appendCell(tr, event.ocr_text);
    appendCell(tr, event.color);
    appendCell(tr, event.image_class);
    appendCell(tr, event.message);
    tbody.appendChild(tr);
  }
}

function appendCell(row, value) {
  const td = document.createElement("td");
  td.textContent = textOrDash(value);
  row.appendChild(td);
}

async function postAction(url, body = {}) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const payload = await res.json();
      message = payload.error || message;
    } catch (_) {
      // Keep the HTTP status when the server did not return JSON.
    }
    throw new Error(message);
  }
  await loadStatus();
}

async function confirmLatest() {
  if (latestEventId) {
    await postAction("/api/confirm", { event_id: latestEventId });
    return;
  }
  await postAction("/api/confirm", {});
}

async function playCarAudio() {
  const res = await fetch("/api/audio/play", { method: "POST" });
  const payload = await res.json();
  if (!payload.ok) {
    window.alert(`小车音频播放失败：${payload.error || "未知错误"}`);
  }
}

async function calibrateTurn(direction) {
  const speed = Number(byId("turn_calibration_speed")?.value || 18);
  const duration = Number(byId("turn_calibration_seconds")?.value || 0.75);
  await postAction("/api/calibration/turn_90", {
    direction,
    speed,
    duration_seconds: duration,
  });
}

document.addEventListener("click", async (event) => {
  try {
    const action = event.target.closest("[data-post]");
    if (action) {
      await postAction(action.dataset.post);
      return;
    }
    const calibration = event.target.closest("[data-calibrate-turn]");
    if (calibration) {
      await calibrateTurn(calibration.dataset.calibrateTurn);
      return;
    }
    if (event.target.closest("[data-confirm]")) {
      await confirmLatest();
      return;
    }
    if (event.target.closest("[data-audio]")) {
      await playCarAudio();
    }
  } catch (error) {
    window.alert(`操作失败：${error.message}`);
  }
});

loadStatus();
setInterval(loadStatus, 1000);
