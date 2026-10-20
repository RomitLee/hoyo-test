(() => {
  "use strict";

  const TOKEN_KEY = "hoyo.accessToken";
  const USERNAME_KEY = "hoyo.username";
  const REFRESH_MS = 5000;
  const OFFLINE_AFTER_MS = 90000;
  const el = Object.fromEntries([
    "login-panel", "login-form", "username", "password", "login-button", "login-error",
    "dashboard", "refresh-button", "logout-button", "connection-state", "device-count",
    "window-count", "last-updated", "message", "devices", "empty-state", "status-count",
    "alert-count", "managed-count", "refresh-alerts", "alerts-list", "alerts-empty",
    "add-device", "pairing-result", "pairing-code", "managed-devices", "managed-empty",
    "bark-state", "bark-form", "bark-enabled", "bark-key", "bark-key-hint", "bark-call",
    "bark-repeat", "bark-save", "bark-test", "bark-feedback",
    "logs-list", "logs-empty", "logs-latest", "logs-older",
  ].map((id) => [id, document.getElementById(id)]));

  let token = localStorage.getItem(TOKEN_KEY) || "";
  let timer = null;
  let lastStatus = null;
  let barkDirty = false;
  let logsBeforeId = 0;
  let logsNextId = 0;
  let logsLoading = false;

  const statusLabels = {
    full: "背包已满", almost_full: "接近满格", not_full: "背包未满",
    inventory_closed: "背包未打开", waiting: "等待检测", unknown: "未知状态",
  };
  const eventLabels = {
    inventory_full: "背包已满", inventory_capacity: "背包容量不足",
    inventory_closed: "背包未打开", inventory_occluded: "背包被遮挡",
    window_occluded: "窗口被遮挡", window_minimized: "游戏窗口最小化",
  };

  class AuthenticationError extends Error {}

  function value(input, fallback = "—") {
    return input === null || input === undefined || input === "" ? fallback : String(input);
  }

  function append(parent, tag, className = "", content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = content;
    parent.appendChild(node);
    return node;
  }

  function formatTime(input) {
    if (!input) return "—";
    const date = new Date(input);
    if (Number.isNaN(date.getTime())) return value(input);
    return date.toLocaleString("zh-CN", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  }

  function isOffline(input) {
    const time = input ? new Date(input).getTime() : NaN;
    return Number.isNaN(time) || Date.now() - time > OFFLINE_AFTER_MS;
  }

  function showMessage(message) {
    el.message.textContent = message;
    el.message.hidden = !message;
  }

  function setConnection(label, state) {
    el["connection-state"].textContent = label;
    el["connection-state"].className = `state-dot state-${state}`;
  }

  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}), Authorization: `Bearer ${token}` };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(path, { ...options, headers, cache: "no-store" });
    if (response.status === 401) throw new AuthenticationError("登录状态已失效，请重新登录。");
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `请求失败（HTTP ${response.status}）`);
    return payload;
  }

  function metric(parent, label, content) {
    const item = append(parent, "span", "metric");
    append(item, "small", "", label);
    append(item, "strong", "", content);
  }

  function renderWindow(windowItem) {
    const stale = isOffline(windowItem.updated_at);
    const status = String(windowItem.status || "unknown").toLowerCase();
    const card = document.createElement("article");
    card.className = `window-card window-${status}${stale ? " window-stale" : ""}`;
    const title = append(card, "div", "window-title");
    append(title, "h3", "", [windowItem.server_name, windowItem.role_name].filter(Boolean).join(" / ") || value(windowItem.title, "未命名窗口"));
    append(title, "span", `window-status ${status}`, statusLabels[status] || value(windowItem.status, "未知状态"));
    append(card, "div", "window-subtitle", windowItem.character_id ? `角色 ID ${windowItem.character_id}` : value(windowItem.status_text, statusLabels[status] || "未知"));
    const metrics = append(card, "div", "window-metrics");
    metric(metrics, "空格", value(windowItem.empty_slots, "-"));
    metric(metrics, "占用", value(windowItem.occupied_slots, "-"));
    metric(metrics, "置信", windowItem.confidence == null ? "—" : `${Math.round(Number(windowItem.confidence) * 100)}%`);
    metric(metrics, "遮挡", windowItem.occlusion_percent == null ? "—" : `${windowItem.occlusion_percent}%`);
    const footer = append(card, "div", "window-footer");
    append(footer, "span", windowItem.minimized ? "flag-danger" : "", windowItem.minimized ? "已最小化" : "窗口正常");
    append(footer, "span", windowItem.monitoring ? "flag-ok" : "flag-danger", windowItem.monitoring ? "监控中" : "未监控");
    append(footer, "time", "", stale ? "数据已过期" : formatTime(windowItem.updated_at));
    return card;
  }

  function renderStatus(payload) {
    lastStatus = payload;
    const devices = Array.isArray(payload.devices) ? payload.devices : [];
    const windowCount = devices.reduce((total, device) => total + (device.windows?.length || 0), 0);
    el["device-count"].textContent = String(devices.length);
    el["window-count"].textContent = String(windowCount);
    el["status-count"].textContent = String(windowCount);
    el["last-updated"].textContent = new Date().toLocaleTimeString("zh-CN");
    el.devices.replaceChildren();
    devices.forEach((device) => {
      const offline = isOffline(device.last_seen_at);
      const card = append(el.devices, "article", "device-card");
      const header = append(card, "div", "device-header");
      const name = append(header, "div", "device-name");
      append(name, "h2", "", value(device.display_name, value(device.hostname, device.device_id)));
      append(name, "div", "device-id", device.device_id);
      const meta = append(name, "div", "device-meta");
      append(meta, "span", "", `心跳 ${formatTime(device.last_seen_at)}`);
      append(meta, "span", "", `版本 ${value(device.app_version)}`);
      append(meta, "span", "", `窗口 ${device.windows?.length || 0}`);
      append(header, "span", `status-badge ${offline ? "badge-offline" : "badge-online"}`, offline ? "离线" : "在线");
      const windows = append(card, "div", "windows");
      if (!device.windows?.length) append(windows, "div", "empty-window", "当前没有上报游戏窗口");
      else device.windows.forEach((item) => windows.appendChild(renderWindow(item)));
    });
    el["empty-state"].hidden = devices.length !== 0;
  }

  function renderAlerts(payload) {
    const events = Array.isArray(payload.events) ? [...payload.events].reverse() : [];
    el["alert-count"].textContent = String(events.filter((item) => !item.acked_at).length);
    el["alerts-list"].replaceChildren();
    events.forEach((item) => {
      const card = append(el["alerts-list"], "article", `alert-card${item.acked_at ? " acknowledged" : ""}`);
      const head = append(card, "div", "alert-head");
      const identity = append(head, "div", "alert-identity");
      append(identity, "strong", "", eventLabels[item.event_type] || value(item.event_type, "未知告警"));
      append(identity, "small", "", `${value(item.device_id)} · ${[item.server_name, item.role_name].filter(Boolean).join(" / ") || "未识别角色"}`);
      append(head, "span", `alert-state ${item.acked_at ? "handled" : "pending"}`, item.acked_at ? "已处理" : "未处理");
      const body = append(card, "div", "alert-body");
      append(body, "span", "", `发生 ${formatTime(item.occurred_at)}`);
      if (item.empty_slots != null) append(body, "span", "", `空格 ${item.empty_slots}`);
      if (item.confidence != null) append(body, "span", "", `置信 ${Math.round(Number(item.confidence) * 100)}%`);
      if (!item.acked_at) {
        const button = append(card, "button", "small-primary", "确认处理");
        button.type = "button";
        button.addEventListener("click", async () => {
          button.disabled = true;
          try {
            await api(`/api/v1/master/events/${item.server_event_id}/ack`, { method: "POST", body: "{}" });
            await loadAll();
          } catch (error) { handleError(error); }
        });
      }
    });
    el["alerts-empty"].hidden = events.length !== 0;
  }

  async function loadMonitorLogs(beforeId = logsBeforeId) {
    if (!token || logsLoading) return;
    logsLoading = true;
    el["logs-older"].disabled = true;
    try {
      const data = await api(`/api/v1/master/monitor-logs?limit=100&before_id=${beforeId}`);
      logsBeforeId = beforeId;
      const logs = data.logs || [];
      el["logs-list"].replaceChildren();
      const labels = { window_occluded: "遮挡", window_minimized: "最小化", monitoring_started: "开始监控", monitoring_paused: "暂停监控" };
      const reasons = { startup: "启动程序", resume: "点击开始", pause: "点击暂停", close: "关闭程序", recognition: "面板识别" };
      const panelNames = { inventory: "背包", give: "给予面板", trade: "交易面板", attributes: "属性面板", stall: "摊位面板", stall_records: "摊位记录面板" };
      logs.forEach((item) => {
        const card = append(el["logs-list"], "article", "alert-card");
        const head = append(card, "div", "alert-head");
        const identity = append(head, "div", "alert-identity");
        const action = item.event_type === "panel_opened" ? "打开" : "关闭";
        const label = item.event_type === "game_default" ? "关闭面板，返回默认游戏界面"
          : ["panel_opened", "panel_closed"].includes(item.event_type) ? action + (panelNames[item.panel_type] || "未知面板")
          : labels[item.event_type] || item.event_type;
        append(identity, "strong", "", item.message || label);
        append(identity, "small", "", `${item.display_name || item.hostname || item.device_id} · ${item.device_id}`);
        append(card, "div", "alert-body", [item.server_name, item.role_name].filter(Boolean).join(" / ") || "整台从机");
        const body = append(card, "div", "alert-body");
        const parts = Object.fromEntries(new Intl.DateTimeFormat("zh-CN", {
          timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
          hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
        }).formatToParts(new Date(item.occurred_at)).map((part) => [part.type, part.value]));
        append(body, "time", "", `${parts.year}年${parts.month}月${parts.day}日${parts.hour}:${parts.minute}:${parts.second}`);
        if (item.occlusion_percent != null) {
          const percent = Number(item.occlusion_percent);
          append(body, "span", "", `遮挡 ${percent > 0 && percent < 0.1 ? "<0.1" : percent.toFixed(1)}%`);
        }
        if (item.minimized != null) append(body, "span", "", `最小化 ${item.minimized ? "是" : "否"}`);
        append(body, "span", "", reasons[item.reason] || "状态上报");
      });
      logsNextId = logs.length ? logs[logs.length - 1].id : 0;
      el["logs-older"].disabled = !data.has_more;
      el["logs-empty"].hidden = logs.length !== 0;
    } catch (error) { handleError(error); }
    finally { logsLoading = false; }
  }

  function deviceAction(parent, label, handler, danger = false) {
    const button = append(parent, "button", `device-action${danger ? " danger" : ""}`, label);
    button.type = "button";
    button.addEventListener("click", handler);
  }

  function renderManaged(payload) {
    const devices = Array.isArray(payload.devices) ? payload.devices : [];
    el["managed-count"].textContent = String(devices.length);
    el["managed-devices"].replaceChildren();
    devices.forEach((device) => {
      const card = append(el["managed-devices"], "article", `managed-card${device.disabled ? " disabled" : ""}`);
      const head = append(card, "div", "managed-head");
      const identity = append(head, "div", "managed-identity");
      append(identity, "strong", "", value(device.display_name, value(device.hostname, device.device_id)));
      append(identity, "small", "", device.device_id);
      append(head, "span", `status-badge ${device.disabled || isOffline(device.last_seen_at) ? "badge-offline" : "badge-online"}`, device.disabled ? "已禁用" : isOffline(device.last_seen_at) ? "离线" : "在线");
      const info = append(card, "div", "managed-info");
      append(info, "span", "", `主机名 ${value(device.hostname)}`);
      append(info, "span", "", `版本 ${value(device.app_version)}`);
      append(info, "span", "", `最后连接 ${formatTime(device.last_seen_at)}`);
      if (device.note) append(info, "span", "", `备注 ${device.note}`);
      const actions = append(card, "div", "managed-actions");
      deviceAction(actions, "编辑", () => editDevice(device));
      deviceAction(actions, device.disabled ? "启用" : "禁用", () => toggleDevice(device));
      deviceAction(actions, "重新配对", () => reissueDevice(device));
      deviceAction(actions, "移除", () => removeDevice(device), true);
    });
    el["managed-empty"].hidden = devices.length !== 0;
  }

  function renderBark(settings) {
    el["bark-enabled"].checked = Boolean(settings.enabled);
    el["bark-call"].checked = Boolean(settings.call_enabled);
    el["bark-repeat"].value = String(settings.repeat_minutes ?? 10);
    el["bark-key"].value = "";
    el["bark-key"].placeholder = settings.configured
      ? `已保存 ${settings.masked_key}；留空表示不修改`
      : "可直接粘贴 Bark App 首页显示的完整测试地址";
    el["bark-key-hint"].textContent = settings.configured
      ? `当前密钥：${settings.masked_key}。新输入会替换旧密钥。`
      : "密钥仅保存在服务器数据库，不会显示在页面或写入安装包。";
    el["bark-state"].textContent = settings.enabled ? "开" : "关";
    el["bark-state"].className = settings.enabled ? "enabled" : "";
  }

  function barkFeedback(message, error = false) {
    el["bark-feedback"].textContent = message;
    el["bark-feedback"].className = `form-feedback${error ? " feedback-error" : ""}`;
    el["bark-feedback"].hidden = !message;
  }

  async function editDevice(device) {
    const displayName = window.prompt("从机名称", device.display_name || device.hostname || device.device_id);
    if (displayName === null || !displayName.trim()) return;
    const note = window.prompt("备注（可留空）", device.note || "");
    if (note === null) return;
    try {
      await api(`/api/v1/master/devices/${encodeURIComponent(device.device_id)}`, {
        method: "PATCH", body: JSON.stringify({ display_name: displayName.trim(), note: note.trim() }),
      });
      await loadAll();
    } catch (error) { handleError(error); }
  }

  async function toggleDevice(device) {
    if (!window.confirm(`确定要${device.disabled ? "启用" : "禁用"}“${device.display_name || device.device_id}”吗？`)) return;
    try {
      await api(`/api/v1/master/devices/${encodeURIComponent(device.device_id)}`, {
        method: "PATCH", body: JSON.stringify({ disabled: !device.disabled }),
      });
      await loadAll();
    } catch (error) { handleError(error); }
  }

  function showPairingCode(code) {
    el["pairing-code"].textContent = code;
    el["pairing-result"].hidden = false;
    el["pairing-result"].scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function reissueDevice(device) {
    if (!window.confirm("重新生成凭据后，旧凭据立即失效。确定继续吗？")) return;
    try {
      const result = await api(`/api/v1/master/devices/${encodeURIComponent(device.device_id)}/reissue`, { method: "POST", body: "{}" });
      showPairingCode(result.pairing_code);
    } catch (error) { handleError(error); }
  }

  async function removeDevice(device) {
    if (!window.confirm(`确定永久移除“${device.display_name || device.device_id}”吗？移除后需重新添加。`)) return;
    try {
      await api(`/api/v1/master/devices/${encodeURIComponent(device.device_id)}`, { method: "DELETE" });
      await loadAll();
    } catch (error) { handleError(error); }
  }

  function handleError(error) {
    if (error instanceof AuthenticationError) {
      showLogin(error.message);
      setConnection("未授权", "error");
      return;
    }
    setConnection("连接失败", "error");
    showMessage(lastStatus ? `云端暂时不可用，保留上一次数据（${error.message}）。` : error.message || "云端暂时不可用。");
  }

  async function loadAll() {
    if (!token) return;
    try {
      const [status, events, devices, bark] = await Promise.all([
        api("/api/v1/master/status"),
        api("/api/v1/master/events?state=all&limit=200"),
        api("/api/v1/master/devices"),
        api("/api/v1/master/notifications/bark"),
      ]);
      renderStatus(status);
      renderAlerts(events);
      renderManaged(devices);
      if (!barkDirty) renderBark(bark);
      setConnection("已连接", "online");
      showMessage("");
      if (document.querySelector('[data-page="logs"]').classList.contains("active")) await loadMonitorLogs();
    } catch (error) { handleError(error); }
  }

  function selectTab(name) {
    document.querySelectorAll(".tab-button").forEach((button) => button.classList.toggle("active", button.dataset.tab === name));
    document.querySelectorAll(".tab-page").forEach((page) => {
      const active = page.dataset.page === name;
      page.classList.toggle("active", active);
      page.hidden = !active;
    });
    if (name === "logs") loadMonitorLogs();
  }

  function startRefreshing() {
    if (timer !== null) window.clearInterval(timer);
    loadAll();
    timer = window.setInterval(loadAll, REFRESH_MS);
  }

  function showDashboard() {
    el["login-panel"].hidden = true;
    el.dashboard.hidden = false;
    el["login-error"].hidden = true;
    startRefreshing();
  }

  function showLogin(message = "") {
    localStorage.removeItem(TOKEN_KEY);
    token = "";
    lastStatus = null;
    logsBeforeId = 0;
    logsNextId = 0;
    el["logs-list"].replaceChildren();
    if (timer !== null) window.clearInterval(timer);
    timer = null;
    el.dashboard.hidden = true;
    el["login-panel"].hidden = false;
    el.password.value = "";
    el["login-error"].textContent = message;
    el["login-error"].hidden = !message;
    el["login-button"].disabled = false;
    el["login-button"].textContent = "登录";
  }

  el["login-form"].addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = el.username.value.trim();
    const password = el.password.value;
    if (!username || !password) return;
    el["login-button"].disabled = true;
    el["login-button"].textContent = "登录中…";
    el["login-error"].hidden = true;
    try {
      const response = await fetch("/api/v1/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "账号或密码错误");
      token = String(payload.access_token || "");
      if (!token) throw new Error("服务器没有返回登录会话");
      localStorage.setItem(TOKEN_KEY, token);
      localStorage.setItem(USERNAME_KEY, username);
      el.password.value = "";
      showDashboard();
    } catch (error) {
      el["login-error"].textContent = error.message || "登录失败，请稍后重试。";
      el["login-error"].hidden = false;
      el["login-button"].disabled = false;
      el["login-button"].textContent = "登录";
    }
  });

  document.querySelectorAll(".tab-button").forEach((button) => button.addEventListener("click", () => selectTab(button.dataset.tab)));
  el["refresh-button"].addEventListener("click", loadAll);
  el["refresh-alerts"].addEventListener("click", loadAll);
  el["logs-latest"].addEventListener("click", () => loadMonitorLogs(0));
  el["logs-older"].addEventListener("click", () => loadMonitorLogs(logsNextId));
  el["add-device"].addEventListener("click", async () => {
    el["add-device"].disabled = true;
    try {
      const result = await api("/api/v1/master/pairing-codes", { method: "POST", body: "{}" });
      showPairingCode(result.pairing_code);
    } catch (error) { handleError(error); }
    finally { el["add-device"].disabled = false; }
  });
  el["bark-form"].addEventListener("submit", async (event) => {
    event.preventDefault();
    el["bark-save"].disabled = true;
    barkFeedback("正在保存…");
    const payload = {
      enabled: el["bark-enabled"].checked,
      call_enabled: el["bark-call"].checked,
      repeat_minutes: Number(el["bark-repeat"].value),
    };
    let deviceKey = el["bark-key"].value.trim();
    if (/^https?:\/\//i.test(deviceKey)) {
      try {
        const pastedUrl = new URL(deviceKey);
        if (pastedUrl.hostname !== "api.day.app") throw new Error("请使用 Bark 官方测试地址");
        deviceKey = pastedUrl.pathname.split("/").filter(Boolean)[0] || "";
      } catch (error) {
        barkFeedback(error.message || "Bark 测试地址格式不正确", true);
        el["bark-save"].disabled = false;
        return;
      }
    }
    if (deviceKey) payload.device_key = deviceKey;
    try {
      const settings = await api("/api/v1/master/notifications/bark", {
        method: "PUT", body: JSON.stringify(payload),
      });
      barkDirty = false;
      renderBark(settings);
      barkFeedback("设置已保存。建议立即发送一次测试通知。");
    } catch (error) {
      if (error instanceof AuthenticationError) handleError(error);
      else barkFeedback(error.message || "保存失败", true);
    } finally { el["bark-save"].disabled = false; }
  });
  el["bark-form"].addEventListener("input", () => { barkDirty = true; });
  el["bark-test"].addEventListener("click", async () => {
    el["bark-test"].disabled = true;
    barkFeedback("正在发送测试通知…");
    try {
      await api("/api/v1/master/notifications/bark/test", { method: "POST", body: "{}" });
      barkFeedback("测试通知已发送，请查看 iPhone。若没有收到，请检查 Bark 通知权限。");
    } catch (error) {
      if (error instanceof AuthenticationError) handleError(error);
      else barkFeedback(error.message || "测试推送失败", true);
    } finally { el["bark-test"].disabled = false; }
  });
  el["logout-button"].addEventListener("click", () => { showLogin(); setConnection("未连接", "unknown"); });

  localStorage.removeItem("hoyo.masterToken");
  el.username.value = localStorage.getItem(USERNAME_KEY) || "";
  const requestedTab = new URLSearchParams(window.location.search).get("tab");
  if (["status", "alerts", "management", "notifications", "logs"].includes(requestedTab)) selectTab(requestedTab);
  if (token) showDashboard();
  else {
    el.dashboard.hidden = true;
    (el.username.value ? el.password : el.username).focus();
  }
})();
