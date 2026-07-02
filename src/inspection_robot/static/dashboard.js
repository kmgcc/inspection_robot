let latestEventId = null;

async function loadStatus() {
  const res = await fetch("/api/status");
  const data = await res.json();

  document.getElementById("task_status").textContent = data.task_status;
  document.getElementById("robot_status").textContent = data.robot_status;
  document.getElementById("current_tag").textContent = data.current_tag || "-";
  document.getElementById("current_item").textContent = data.current_item
    ? `${data.current_item} / ${data.current_zone}`
    : "-";
  document.getElementById("last_message").textContent = data.last_message;

  const tbody = document.getElementById("events");
  tbody.innerHTML = "";
  latestEventId = null;

  for (const event of data.events) {
    if (event.status === "待确认" && latestEventId === null) {
      latestEventId = event.id;
    }

    const tr = document.createElement("tr");
    const statusClass = event.status === "正常" || event.status === "已确认" ? "ok" : "bad";
    tr.innerHTML = `
      <td>${event.time}</td>
      <td>${event.tag_id}</td>
      <td>${event.item}</td>
      <td>${event.zone}</td>
      <td><span class="badge ${statusClass}">${event.status}</span></td>
      <td>${event.message}</td>
    `;
    tbody.appendChild(tr);
  }
}

async function postAction(url, body = {}) {
  await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  await loadStatus();
}

async function confirmLatest() {
  if (!latestEventId) {
    await postAction("/api/confirm", {});
    return;
  }
  await postAction("/api/confirm", { event_id: latestEventId });
}

async function playCarAudio() {
  const res = await fetch("/api/audio/play", { method: "POST" });
  const payload = await res.json();

  if (!payload.ok) {
    window.alert(`小车音频播放失败：${payload.error || "未知错误"}`);
  }
}

loadStatus();
setInterval(loadStatus, 1000);
