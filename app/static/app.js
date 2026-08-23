"use strict";
// ===== Estado y helpers =====
const S = { token: localStorage.getItem("token") || null, role: null, isp: null, devices: [], current: null };
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

// tags internos que no se muestran al usuario final
const HIDDEN_TAGS = new Set(["sched-reboot"]);
const visibleTags = (tags) => (tags || []).filter(t => !HIDDEN_TAGS.has(t));

function toast(msg, kind = "info") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast " + kind;
  setTimeout(() => t.classList.add("hidden"), 3500);
}

async function api(path, { method = "GET", body = null, form = null } = {}) {
  const headers = {};
  if (S.token) headers["Authorization"] = "Bearer " + S.token;
  let payload = null;
  if (form) { payload = form; }
  else if (body) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const res = await fetch(path, { method, headers, body: payload });
  if (res.status === 401) { logout(); throw new Error("Sesión expirada"); }
  let data = null;
  try { data = await res.json(); } catch { /* sin cuerpo */ }
  if (!res.ok) throw new Error((data && (data.detail || data.message)) || ("Error " + res.status));
  return data;
}

// el _id lleva % literales -> encodeURIComponent los pasa a %25 (correcto para GenieACS)
const enc = (id) => encodeURIComponent(id);

// ===== Auth =====
function showLogin() { $("#login-view").classList.remove("hidden"); $("#app-view").classList.add("hidden"); }
function showApp() { $("#login-view").classList.add("hidden"); $("#app-view").classList.remove("hidden"); }

function logout() {
  S.token = null; localStorage.removeItem("token");
  showLogin();
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-error").classList.add("hidden");
  const fd = new URLSearchParams();
  fd.set("username", $("#login-user").value.trim());
  fd.set("password", $("#login-pass").value);
  try {
    const r = await api("/auth/login", { method: "POST", form: fd });
    S.token = r.access_token; S.role = r.role; S.isp = r.isp;
    localStorage.setItem("token", S.token);
    await boot();
  } catch (err) {
    const el = $("#login-error"); el.textContent = err.message; el.classList.remove("hidden");
  }
});

$("#logout").addEventListener("click", logout);

// ===== Navegación =====
$$("[data-nav]").forEach(a => a.addEventListener("click", (e) => {
  e.preventDefault();
  $$("[data-nav]").forEach(x => x.classList.remove("active"));
  a.classList.add("active");
  const nav = a.dataset.nav;
  $("#devices-page").classList.toggle("hidden", nav !== "devices");
  $("#device-page").classList.add("hidden");
  $("#users-page").classList.toggle("hidden", nav !== "users");
  $("#settings-page").classList.toggle("hidden", nav !== "settings");
  if (nav === "devices") loadDevices();
  if (nav === "users") loadUsers();
  if (nav === "settings") loadSettings();
}));

// ===== Equipos: lista =====
async function loadDevices() {
  try {
    S.devices = await api("/devices");
    renderDevices();
  } catch (e) { toast(e.message, "err"); }
}

function renderDevices() {
  const q = $("#filter").value.toLowerCase();
  const list = $("#device-list");
  const items = S.devices.filter(d =>
    !q || (d.id + " " + (d.model || "") + " " + (d.manufacturer || "") + " " + (d.tags || []).join(" ")).toLowerCase().includes(q));
  $("#device-empty").classList.toggle("hidden", S.devices.length > 0);
  list.innerHTML = items.map(d => {
    const online = isRecent(d.last_inform);
    return `<div class="dev-card" data-id="${escAttr(d.id)}">
      <h3><span class="dot ${online ? "on" : "off"}"></span>${esc(d.manufacturer || "?")} </h3>
      <div class="model">${esc(d.model || "modelo ?")}</div>
      <div class="meta">FW: ${esc(d.firmware || "-")}<br>Último reporte: ${fmtDate(d.last_inform)}<br>${visibleTags(d.tags).map(t => `#${esc(t)}`).join(" ")}</div>
    </div>`;
  }).join("");
  $$(".dev-card", list).forEach(c => c.addEventListener("click", () => openDevice(c.dataset.id)));
}

$("#filter").addEventListener("input", renderDevices);
$("#refresh-list").addEventListener("click", loadDevices);

// ===== Equipos: detalle =====
async function openDevice(id) {
  S.current = id;
  $("#devices-page").classList.add("hidden");
  $("#users-page").classList.add("hidden");
  $("#device-page").classList.remove("hidden");
  $("#dev-title").textContent = id;
  $("#dev-status").innerHTML = `<div class="muted">Cargando…</div>`;
  try {
    const st = await api(`/devices/${enc(id)}/status`);
    renderStatus(st);
    prefillForms(st);
    loadFirmware();
    loadSchedule();
  } catch (e) { toast(e.message, "err"); }
}

function renderStatus(st) {
  $("#dev-tags").innerHTML = visibleTags(st.tags).map(t => `<span class="tag">${esc(t)}</span>`).join("");
  const cells = [
    ["Firmware", st.firmware], ["Uptime", fmtUptime(st.uptime)], ["CPU", st.cpu != null ? st.cpu + "%" : "-"],
    ["IP WAN", st.wan_ip], ["IP LAN", st.lan_ip], ["SSID 2.4G", st.wifi_2g_ssid],
    ["SSID 5G", st.wifi_5g_ssid], ["PPPoE", st.pppoe_enable ? "activo (" + (st.pppoe_user || "").trim() + ")" : "inactivo"],
    ["Último reporte", fmtDate(st.last_inform)],
  ];
  $("#dev-status").innerHTML = cells.map(([k, v]) =>
    `<div class="cell"><div class="k">${k}</div><div class="v">${esc(v != null && v !== "" ? String(v) : "-")}</div></div>`).join("");
}

function prefillForms(st) {
  $("#wifi-band").value = "2g";
  $("#wifi-ssid").value = ""; $("#wifi-pass").value = "";
  $("#net-ip").value = ""; $("#net-mask").value = "";
}

// tabs
$$(".tab").forEach(t => t.addEventListener("click", () => {
  $$(".tab").forEach(x => x.classList.remove("active")); t.classList.add("active");
  $$(".panel").forEach(p => p.classList.toggle("hidden", p.dataset.panel !== t.dataset.tab));
}));

$$("[data-back]").forEach(b => b.addEventListener("click", () => {
  $("#device-page").classList.add("hidden"); $("#devices-page").classList.remove("hidden");
}));

// resultado de una acción
function report(r) {
  if (r && r.applied) toast("✓ Aplicado en el equipo", "ok");
  else if (r && r.queued) toast("En cola: se aplicará en el próximo reporte del equipo", "info");
  else toast("✓ Hecho", "ok");
  if (r && r.detail) console.log(r.detail);
}

// ===== Acciones =====
const actions = {
  async wifi() {
    const body = { band: $("#wifi-band").value };
    if ($("#wifi-ssid").value.trim()) body.ssid = $("#wifi-ssid").value.trim();
    if ($("#wifi-pass").value) body.password = $("#wifi-pass").value;
    body.enable = $("#wifi-enable").checked;
    if ($("#wifi-channel").value) body.channel = +$("#wifi-channel").value;
    body.hidden = $("#wifi-hidden").checked;
    report(await api(`/devices/${enc(S.current)}/wifi`, { method: "PUT", body }));
  },
  async net() {
    const body = {};
    if ($("#net-ip").value.trim()) body.lan_ip = $("#net-ip").value.trim();
    if ($("#net-mask").value.trim()) body.lan_mask = $("#net-mask").value.trim();
    if ($("#net-min").value.trim()) body.dhcp_min = $("#net-min").value.trim();
    if ($("#net-max").value.trim()) body.dhcp_max = $("#net-max").value.trim();
    body.dhcp_enable = $("#net-dhcp").checked;
    if ($("#net-lease").value) body.dhcp_lease = +$("#net-lease").value;
    report(await api(`/devices/${enc(S.current)}/ip`, { method: "PUT", body }));
  },
  async pppoe() {
    const body = { enable: $("#ppp-enable").checked };
    if ($("#ppp-user").value.trim()) body.username = $("#ppp-user").value.trim();
    if ($("#ppp-pass").value) body.password = $("#ppp-pass").value;
    report(await api(`/devices/${enc(S.current)}/pppoe`, { method: "PUT", body }));
  },
  async dns() {
    const servers = $("#dns-servers").value.split(/\s+/).map(s => s.trim()).filter(Boolean);
    if (!servers.length) return toast("Indica al menos un servidor DNS", "err");
    report(await api(`/devices/${enc(S.current)}/dns`, { method: "PUT", body: { scope: $("#dns-scope").value, servers } }));
  },
  async time() {
    const body = {};
    if ($("#time-tz").value.trim()) body.timezone = $("#time-tz").value.trim();
    if ($("#time-ntp1").value.trim()) body.ntp1 = $("#time-ntp1").value.trim();
    if ($("#time-ntp2").value.trim()) body.ntp2 = $("#time-ntp2").value.trim();
    report(await api(`/devices/${enc(S.current)}/time`, { method: "PUT", body }));
  },
  async fw() {
    const f = $("#fw-file").value;
    if (!f) return toast("No hay firmware seleccionado", "err");
    if (!confirm("¿Enviar la actualización " + f + " al equipo?")) return;
    report(await api(`/devices/${enc(S.current)}/firmware`, { method: "POST", body: { file_name: f } }));
  },
  async reboot() {
    if (!confirm("¿Reiniciar el equipo ahora?")) return;
    report(await api(`/devices/${enc(S.current)}/reboot`, { method: "POST" }));
  },
  async ["sched-set"]() {
    const h = +$("#sched-hour").value, m = +$("#sched-min").value || 0;
    if (isNaN(h)) return toast("Indica la hora", "err");
    await api(`/devices/${enc(S.current)}/schedule-reboot`, { method: "PUT", body: { hour: h, minute: m } });
    toast("✓ Reinicio programado", "ok"); loadSchedule();
  },
  async ["sched-clear"]() {
    await api(`/devices/${enc(S.current)}/schedule-reboot`, { method: "DELETE" });
    toast("Programación eliminada", "ok"); loadSchedule();
  },
};

document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-action]");
  if (!btn) return;
  const act = btn.dataset.action;
  if (!actions[act]) return;
  btn.disabled = true;
  try { await actions[act](); } catch (err) { toast(err.message, "err"); }
  finally { btn.disabled = false; }
});

async function loadFirmware() {
  try {
    const files = await api("/firmware");
    const sel = $("#fw-file");
    sel.innerHTML = files.length
      ? files.map(f => `<option value="${escAttr(f._id || f.filename || f.name)}">${esc(f._id || f.filename || f.name)}</option>`).join("")
      : `<option value="">(sin firmwares cargados)</option>`;
  } catch { $("#fw-file").innerHTML = `<option value="">(no disponible)</option>`; }
}

async function loadSchedule() {
  try {
    const s = await api(`/devices/${enc(S.current)}/schedule-reboot`);
    $("#sched-current").textContent = s.scheduled
      ? `Programado: todos los días a las ${String(s.hour).padStart(2, "0")}:${String(s.minute).padStart(2, "0")}`
      : "Sin reinicio programado.";
  } catch { $("#sched-current").textContent = ""; }
}

// ===== Usuarios (admin) =====
async function loadUsers() {
  try {
    const users = await api("/auth/users");
    $("#users-table tbody").innerHTML = users.map(u =>
      `<tr><td>${u.id}</td><td>${esc(u.username)}</td><td>${esc(u.role)}</td><td>${esc(u.isp_tag || "-")}</td><td>${u.active ? "sí" : "no"}</td></tr>`).join("");
  } catch (e) { toast(e.message, "err"); }
}

$("#user-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    username: $("#u-name").value.trim(), password: $("#u-pass").value,
    role: $("#u-role").value, isp_tag: $("#u-tag").value.trim() || null,
  };
  try {
    await api("/auth/users", { method: "POST", body });
    toast("✓ Usuario creado", "ok"); $("#user-form").reset(); loadUsers();
  } catch (err) { toast(err.message, "err"); }
});

// ===== Ajustes: conexión al ACS (admin) =====
async function loadSettings() {
  try {
    const s = await api("/settings");
    $("#set-url").value = s.nbi_url || "";
    $("#set-timeout").value = s.nbi_timeout || "";
    $("#set-cr").checked = !!s.default_connection_request;
    $("#set-source").textContent =
      `Origen actual: URL=${s.source.nbi_url} · timeout=${s.source.nbi_timeout} · CR=${s.source.default_connection_request}. `
      + `Por defecto (.env): ${s.env_default_nbi_url}`;
    $("#set-testresult").textContent = "";
  } catch (e) { toast(e.message, "err"); }
}

actions["set-test"] = async function () {
  const el = $("#set-testresult");
  el.textContent = "Probando…"; el.style.color = "var(--muted)";
  try {
    const r = await api("/settings/test", { method: "POST", body: { nbi_url: $("#set-url").value.trim() } });
    if (r.ok) { el.textContent = `✓ ${r.detail} (${r.latency_ms} ms)`; el.style.color = "var(--ok)"; }
    else { el.textContent = "✗ " + (r.error || "sin conexión"); el.style.color = "var(--err)"; }
  } catch (e) { el.textContent = "✗ " + e.message; el.style.color = "var(--err)"; }
};

$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    nbi_url: $("#set-url").value.trim(),
    default_connection_request: $("#set-cr").checked,
  };
  if ($("#set-timeout").value) body.nbi_timeout = +$("#set-timeout").value;
  try {
    const r = await api("/settings", { method: "PUT", body });
    if (r.ok === false) return toast(r.error, "err");
    toast("✓ Conexión al ACS actualizada (sin reiniciar)", "ok");
    loadSettings();
  } catch (err) { toast(err.message, "err"); }
});

// ===== Utilidades de formato =====
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function escAttr(s) { return esc(s); }
function fmtDate(iso) { if (!iso) return "-"; const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString(); }
function isRecent(iso) { if (!iso) return false; return (Date.now() - new Date(iso).getTime()) < 15 * 60 * 1000; }
function fmtUptime(s) { if (s == null) return "-"; s = +s; const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return `${d}d ${h}h ${m}m`; }

// ===== Arranque =====
async function boot() {
  showApp();
  // recuperar rol desde /devices no da rol; leemos del token guardado en login o pedimos users
  try {
    // intentar cargar usuarios: si funciona, es admin
    await api("/auth/users");
    S.role = "admin";
  } catch { S.role = S.role || "isp"; }
  $("#nav-users").classList.toggle("hidden", S.role !== "admin");
  $("#nav-settings").classList.toggle("hidden", S.role !== "admin");
  $("#who").textContent = S.isp ? `ISP: ${S.isp}` : (S.role === "admin" ? "Administrador" : "");
  loadDevices();
}

if (S.token) { boot().catch(() => showLogin()); } else { showLogin(); }
