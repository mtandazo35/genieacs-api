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
  $("#updates-page").classList.toggle("hidden", nav !== "updates");
  if (nav === "devices") loadDevices();
  if (nav === "users") loadUsers();
  if (nav === "settings") loadSettings();
  if (nav === "updates") loadUpdates();
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

// lectura masiva (dentro del alcance del usuario)
$("#read-all").addEventListener("click", async () => {
  const b = $("#read-all"); b.disabled = true; const p = b.textContent; b.textContent = "Encolando…";
  try {
    const r = await api("/devices/read-bulk", { method: "POST", body: { all: true } });
    toast(`✓ ${r.detail || ("Lectura encolada en " + r.sent + " equipo(s)")}`, "ok");
  } catch (e) { toast(e.message, "err"); }
  finally { b.disabled = false; b.textContent = p; }
});

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
    // si faltan datos volatiles (se pierden tras un BOOTSTRAP), leer del equipo
    if (st.uptime == null || st.cpu == null || st.lan_ip == null) readDevice(true);
  } catch (e) { toast(e.message, "err"); }
}

// Lee datos frescos del CPE (getParameterValues) y refresca la ficha.
async function readDevice(silent) {
  const id = S.current;
  const btn = $("#read-btn");
  const prev = btn.textContent;
  btn.disabled = true; btn.textContent = "Leyendo…";
  if (!silent) toast("Pidiendo datos al equipo…", "info");
  try {
    await api(`/devices/${enc(id)}/read`, { method: "POST" });
    // el equipo responde en su sesion; reintentar la ficha unas veces
    for (let i = 0; i < 5; i++) {
      await new Promise(r => setTimeout(r, 2500));
      if (S.current !== id) return;                // el usuario cambió de vista
      const st = await api(`/devices/${enc(id)}/status`);
      renderStatus(st);
      if (st.uptime != null && st.cpu != null) { if (!silent) toast("✓ Datos actualizados", "ok"); break; }
    }
  } catch (e) { if (!silent) toast(e.message, "err"); }
  finally { btn.disabled = false; btn.textContent = prev; }
}

function cell(k, v) {
  return `<div class="cell"><div class="k">${esc(k)}</div><div class="v">${esc(v != null && v !== "" ? String(v) : "-")}</div></div>`;
}
function pwCell(k, v) {
  if (v == null || v === "") return cell(k, "-");
  return `<div class="cell"><div class="k">${esc(k)}</div><div class="v pw">
    <span class="pw-val" data-pw="${escAttr(v)}">••••••••</span>
    <button class="eye" type="button" title="Mostrar/ocultar">👁</button></div></div>`;
}
function onoff(v) { return v === true ? "Encendido" : v === false ? "Apagado" : "-"; }

function renderStatus(st) {
  $("#dev-tags").innerHTML = visibleTags(st.tags).map(t => `<span class="tag">${esc(t)}</span>`).join("");
  const dhcp = (st.dhcp_min || st.dhcp_max) ? `${st.dhcp_min || "?"} – ${st.dhcp_max || "?"}` : null;
  const sections = [
    ["Dispositivo", [
      cell("Modelo", st.model), cell("Serial", st.serial), cell("Firmware", st.firmware),
      cell("Uptime", st.uptime != null ? fmtUptime(st.uptime) : null),
      cell("CPU", st.cpu != null ? st.cpu + "%" : null),
      cell("Último reporte", fmtDate(st.last_inform)),
    ]],
    ["Internet (WAN)", [
      cell("IP WAN", st.wan_ip),
      cell("PPPoE", st.pppoe_enable ? "activo (" + (st.pppoe_user || "").trim() + ")" : "inactivo"),
    ]],
    ["Red local (LAN)", [
      cell("IP del router", st.lan_ip), cell("Rango DHCP", dhcp),
    ]],
    ["WiFi 2.4 GHz", [
      cell("SSID", st.wifi_2g_ssid), pwCell("Clave", st.wifi_2g_password),
      cell("Canal", st.wifi_2g_channel), cell("Radio", onoff(st.wifi_2g_enable)),
    ]],
    ["WiFi 5 GHz", [
      cell("SSID", st.wifi_5g_ssid), pwCell("Clave", st.wifi_5g_password),
      cell("Canal", st.wifi_5g_channel), cell("Radio", onoff(st.wifi_5g_enable)),
    ]],
  ];
  $("#dev-status").innerHTML = sections.map(([title, cells]) =>
    `<div class="section"><h4>${esc(title)}</h4><div class="section-grid">${cells.join("")}</div></div>`).join("");
}

// mostrar/ocultar claves
document.addEventListener("click", (e) => {
  const b = e.target.closest(".eye");
  if (!b) return;
  const span = b.previousElementSibling;
  if (span.textContent === "••••••••") span.textContent = span.dataset.pw;
  else span.textContent = "••••••••";
});

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

// tras un cambio, re-leer el equipo para mostrar los datos nuevos.
// Si se aplicó al instante, refresca ya; si quedó en cola, reintenta un poco.
function refreshAfterChange(r) {
  if (!S.current) return;
  setTimeout(() => readDevice(true), r && r.applied ? 1500 : 4000);
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
    const r = await api(`/devices/${enc(S.current)}/wifi`, { method: "PUT", body });
    report(r); refreshAfterChange(r);
  },
  async net() {
    const body = {};
    if ($("#net-ip").value.trim()) body.lan_ip = $("#net-ip").value.trim();
    if ($("#net-mask").value.trim()) body.lan_mask = $("#net-mask").value.trim();
    if ($("#net-min").value.trim()) body.dhcp_min = $("#net-min").value.trim();
    if ($("#net-max").value.trim()) body.dhcp_max = $("#net-max").value.trim();
    body.dhcp_enable = $("#net-dhcp").checked;
    if ($("#net-lease").value) body.dhcp_lease = +$("#net-lease").value;
    const r = await api(`/devices/${enc(S.current)}/ip`, { method: "PUT", body });
    report(r); refreshAfterChange(r);
  },
  async pppoe() {
    const body = { enable: $("#ppp-enable").checked };
    if ($("#ppp-user").value.trim()) body.username = $("#ppp-user").value.trim();
    if ($("#ppp-pass").value) body.password = $("#ppp-pass").value;
    const r = await api(`/devices/${enc(S.current)}/pppoe`, { method: "PUT", body });
    report(r); refreshAfterChange(r);
  },
  async dns() {
    const servers = $("#dns-servers").value.split(/\s+/).map(s => s.trim()).filter(Boolean);
    if (!servers.length) return toast("Indica al menos un servidor DNS", "err");
    const r = await api(`/devices/${enc(S.current)}/dns`, { method: "PUT", body: { scope: $("#dns-scope").value, servers } });
    report(r); refreshAfterChange(r);
  },
  async time() {
    const body = {};
    if ($("#time-tz").value.trim()) body.timezone = $("#time-tz").value.trim();
    if ($("#time-ntp1").value.trim()) body.ntp1 = $("#time-ntp1").value.trim();
    if ($("#time-ntp2").value.trim()) body.ntp2 = $("#time-ntp2").value.trim();
    const r = await api(`/devices/${enc(S.current)}/time`, { method: "PUT", body });
    report(r); refreshAfterChange(r);
  },
  async fw() {
    const f = $("#fw-file").value;
    if (!f) return toast("No hay firmware seleccionado", "err");
    if (!confirm("¿Enviar la actualización " + f + " al equipo?")) return;
    report(await api(`/devices/${enc(S.current)}/firmware`, { method: "POST", body: { file_name: f } }));
  },
  async read() { await readDevice(false); },
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

// ===== Actualizaciones / firmware (admin) =====
async function loadUpdates() {
  try {
    const files = await api("/firmware");
    // tabla de firmwares
    $("#fw-table tbody").innerHTML = files.length
      ? files.map(f => {
          const name = f._id || f.filename || f.name;
          return `<tr><td>${esc(name)}</td><td>${esc((f.metadata && f.metadata.fileType) || f.fileType || "-")}</td>
            <td><button class="ghost small" data-fwdel="${escAttr(name)}">Borrar</button></td></tr>`;
        }).join("")
      : `<tr><td colspan="3" class="muted">No hay firmwares cargados.</td></tr>`;
    // selector del envío masivo
    $("#fwp-file").innerHTML = files.length
      ? files.map(f => { const n = f._id || f.filename || f.name; return `<option value="${escAttr(n)}">${esc(n)}</option>`; }).join("")
      : `<option value="">(sube un firmware primero)</option>`;
  } catch (e) { toast(e.message, "err"); }
}

// subir por archivo
$("#fw-upload-file").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = $("#fwu-file").files[0];
  if (!f) return toast("Elige un archivo", "err");
  const fd = new FormData();
  fd.append("file", f);
  fd.append("product_class", $("#fwu-model").value.trim());
  fd.append("version", $("#fwu-version").value.trim());
  fd.append("oui", $("#fwu-oui").value.trim());
  try {
    const r = await api("/firmware/upload", { method: "POST", form: fd });
    toast(`✓ Subido: ${r.file_name} (${(r.size/1024/1024).toFixed(1)} MB)`, "ok");
    $("#fw-upload-file").reset(); loadUpdates();
  } catch (err) { toast(err.message, "err"); }
});

// subir por URL
$("#fw-upload-url").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    url: $("#fwurl-url").value.trim(),
    file_name: $("#fwurl-name").value.trim() || null,
    product_class: $("#fwurl-model").value.trim(),
    version: $("#fwurl-version").value.trim(),
  };
  toast("Descargando desde el URL…", "info");
  try {
    const r = await api("/firmware/upload-url", { method: "POST", body });
    toast(`✓ Guardado: ${r.file_name} (${(r.size/1024/1024).toFixed(1)} MB)`, "ok");
    $("#fw-upload-url").reset(); loadUpdates();
  } catch (err) { toast(err.message, "err"); }
});

// borrar firmware
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-fwdel]");
  if (!b) return;
  const name = b.dataset.fwdel;
  if (!confirm("¿Borrar el firmware " + name + "?")) return;
  try { await api(`/firmware/${encodeURIComponent(name)}`, { method: "DELETE" }); toast("Borrado", "ok"); loadUpdates(); }
  catch (err) { toast(err.message, "err"); }
});

// mostrar/ocultar el campo de valor según destino
$("#fwp-target").addEventListener("change", () => {
  const v = $("#fwp-target").value;
  $("#fwp-value-wrap").classList.toggle("hidden", v === "all");
  $("#fwp-value").placeholder = v === "tag" ? "tag del ISP (p.ej. altala)" : "modelo (p.ej. WR3000 V1.0)";
});

// enviar masivo
$("#fw-push").addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = $("#fwp-file").value;
  if (!file) return toast("No hay firmware seleccionado", "err");
  const target = $("#fwp-target").value;
  const val = $("#fwp-value").value.trim();
  if (target !== "all" && !val) return toast("Indica el valor del filtro", "err");
  const body = { file_name: file };
  if (target === "all") body.all = true;
  if (target === "tag") body.tag = val;
  if (target === "model") body.model = val;
  if (!confirm("¿Enviar " + file + " a los equipos seleccionados?")) return;
  const el = $("#fwp-result"); el.textContent = "Enviando…"; el.style.color = "var(--muted)";
  try {
    const r = await api("/firmware/push", { method: "POST", body });
    el.textContent = `✓ ${r.detail} (fallidos: ${r.failed || 0})`; el.style.color = "var(--ok)";
    toast("✓ Actualización enviada a " + r.sent + " equipo(s)", "ok");
  } catch (err) { el.textContent = "✗ " + err.message; el.style.color = "var(--err)"; }
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
  $("#nav-updates").classList.toggle("hidden", S.role !== "admin");
  $("#who").textContent = S.isp ? `ISP: ${S.isp}` : (S.role === "admin" ? "Administrador" : "");
  loadDevices();
}

if (S.token) { boot().catch(() => showLogin()); } else { showLogin(); }
