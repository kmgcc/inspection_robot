const STATUS_LABELS = {
  IDLE: "待命",
  PLANNING: "规划中",
  PLAN_READY: "路径就绪",
  MOVING: "移动中",
  ALIGNING_SHELF: "对准货架",
  SCANNING_SHELF: "扫描货架",
  ANALYZING: "分析中",
  NORMAL_LOGGED: "正常已记录",
  ABNORMAL_ALARM: "异常告警",
  WAIT_CONFIRM: "等待确认",
  CONFIRMED: "已确认",
  OBSTACLE_WAIT: "障碍等待",
  REROUTING: "重规划",
  FORBIDDEN_ZONE_WAIT: "禁区等待",
  FINISHED: "任务完成",
  STOPPED: "已停止",
  PATROL: "移动中",
  TAG_DETECTED: "识别中",
};

const EVENT_TYPE_LABELS = {
  system: "系统",
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

const DEFAULT_GRID_SIZE = [8, 6];
const DEFAULT_SHELF_POINTS = {
  A1: [3, 1],
  A2: [5, 1],
  B1: [3, 4],
  B2: [5, 4],
};
const DEFAULT_START = [0, 0];

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
  const match = text.toUpperCase().match(/[AB][12]/);
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
  const pending = events.filter(isPending);
  const detection = latestDetection(scan, events);

  latestEventId = pending.length > 0 ? pending[0].id : null;

  const currentShelf = data.current_shelf || data.current_zone;
  const currentItem = data.current_item || detection?.item || detection?.item_id;
  const currentTag = data.current_tag || detection?.tag_id;
  const pose = data.pose || null;

  setText("run_id", data.run_id);
  setText("header_status", data.robot_status || labelFrom(STATUS_LABELS, data.task_status));
  setText("task_status", labelFrom(STATUS_LABELS, data.task_status));
  setText("current_target", data.current_target);
  setText("current_shelf", currentShelf);
  setText("current_item", currentItem || currentTag ? `${textOrDash(currentTag)} / ${textOrDash(currentItem)}` : "-");
  setText("shelf_ocr", detection?.ocr_text || scan.ocr_text);
  setText("evidence_summary", evidenceLine(detection));
  setText("obstacle_state", obstacleLine(obstacle));
  setText("alarm_state", alarm.message ? `${labelFrom(ALARM_LABELS, alarm.level)} / ${alarm.message}` : labelFrom(ALARM_LABELS, alarm.level));
  setText("pose_state", pose ? `(${pose.x}, ${pose.y}, ${pose.heading || "-"})` : "-");
  setText("pending_count", pending.length);
  setText("last_message", data.last_message);

  const path = data.path || {};
  const waypoints = asArray(path.waypoints);
  setText("path_status", `路径：${labelPathStatus(path.status)}`);
  setText("path_counter", waypoints.length > 0 ? `${path.next_index || 0}/${waypoints.length} 个路径点` : "暂无路径");

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

function obstacleLine(obstacle) {
  const distance = obstacle.distance_mm === null || obstacle.distance_mm === undefined ? "-" : `${obstacle.distance_mm} mm`;
  return `${obstacle.blocked ? "阻塞" : "通畅"} / ${distance}`;
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

  const gridSize = DEFAULT_GRID_SIZE;
  const forbiddenCells = forbiddenCellSet(asArray(data.forbidden_zones));
  const pathCells = new Set(asArray(data.path?.waypoints).map(cellKey));
  const pose = data.pose || {};
  const poseKey = Number.isInteger(pose.x) && Number.isInteger(pose.y) ? `${pose.x},${pose.y}` : null;
  const currentShelf = normalizeShelfId(data.current_shelf || data.current_zone);

  root.style.setProperty("--grid-columns", String(gridSize[0]));

  for (let y = 0; y < gridSize[1]; y += 1) {
    for (let x = 0; x < gridSize[0]; x += 1) {
      const cell = document.createElement("div");
      const key = `${x},${y}`;
      const shelfId = shelfAt(x, y);
      cell.className = "map-cell";
      cell.dataset.x = String(x);
      cell.dataset.y = String(y);

      if (key === cellKey(DEFAULT_START)) {
        cell.classList.add("start");
        cell.textContent = "S";
      }
      if (forbiddenCells.has(key)) {
        cell.classList.add("forbidden");
        cell.textContent = "禁";
      }
      if (pathCells.has(key)) {
        cell.classList.add("path");
      }
      if (shelfId) {
        cell.classList.add("shelf");
        cell.textContent = shelfId;
      }
      if (shelfId && shelfId === currentShelf) {
        cell.classList.add("current-shelf");
      }
      if (poseKey === key) {
        cell.classList.add("robot");
        cell.textContent = "车";
      }

      root.appendChild(cell);
    }
  }
}

function forbiddenCellSet(zones) {
  const cells = new Set();
  for (const zone of zones) {
    for (const cell of asArray(zone.cells)) {
      cells.add(cellKey(cell));
    }
  }
  return cells;
}

function cellKey(cell) {
  if (!Array.isArray(cell) || cell.length < 2) {
    return "-,-";
  }
  return `${cell[0]},${cell[1]}`;
}

function shelfAt(x, y) {
  for (const [shelfId, point] of Object.entries(DEFAULT_SHELF_POINTS)) {
    if (point[0] === x && point[1] === y) {
      return shelfId;
    }
  }
  return null;
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
  for (const shelfId of Object.keys(DEFAULT_SHELF_POINTS)) {
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
    throw new Error(`HTTP ${res.status}`);
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

document.addEventListener("click", async (event) => {
  const action = event.target.closest("[data-post]");
  if (action) {
    await postAction(action.dataset.post);
    return;
  }
  if (event.target.closest("[data-confirm]")) {
    await confirmLatest();
    return;
  }
  if (event.target.closest("[data-audio]")) {
    await playCarAudio();
  }
});

loadStatus();
setInterval(loadStatus, 1000);
