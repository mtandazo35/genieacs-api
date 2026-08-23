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
function navigate(nav) {
  $$("[data-nav]").forEach(x => x.classList.toggle("active", x.dataset.nav === nav));
  $("#devices-page").classList.toggle("hidden", nav !== "devices");
  $("#device-page").classList.add("hidden");
  $("#users-page").classList.toggle("hidden", nav !== "users");
  $("#settings-page").classList.toggle("hidden", nav !== "settings");
  $("#updates-page").classList.toggle("hidden", nav !== "updates");
  $("#account-page").classList.toggle("hidden", nav !== "account");
  try { localStorage.setItem("view", nav); localStorage.removeItem("device"); } catch {}
  if (nav === "devices") loadDevices();
  if (nav === "users") loadUsers();
  if (nav === "settings") loadSettings();
  if (nav === "updates") loadUpdates();
  if (nav === "account") loadAccount();
}
$$("[data-nav]").forEach(a => a.addEventListener("click", (e) => { e.preventDefault(); navigate(a.dataset.nav); }));

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
  try { localStorage.setItem("device", id); localStorage.setItem("view", "devices"); } catch {}
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

let lastStatus = null;
function renderStatus(st) {
  lastStatus = st;
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
      cell("Tipo", st.wan_mode),
      cell("IP WAN", st.wan_ip), cell("Gateway", st.wan_gateway),
      cell("PPPoE", st.pppoe_enable
        ? `${(st.pppoe_user || "").trim()} — ${st.pppoe_status === "Connected" ? "conectado" : "configurado (sin conexión)"}`
        : "inactivo"),
      ...(st.pppoe_enable ? [pwCell("Clave PPPoE", st.pppoe_password)] : []),
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
  if (t.dataset.tab === "adv") loadParams();
  if (t.dataset.tab === "backup") loadBackup();
  if (t.dataset.tab === "ipv6") loadIPv6();
  if (t.dataset.tab === "wan") loadWan();
}));

// listar las conexiones WAN del equipo (cuantas y cual activa)
async function loadWan() {
  try {
    const r = await api(`/devices/${enc(S.current)}/wan`);
    const box = $("#wan-list");
    if (!r.count) { box.innerHTML = ""; return; }
    box.innerHTML = `<div class="wan-head">${r.count} conexión(es) WAN en el equipo:</div>` +
      r.connections.map(c => `<div class="wan-row">${c.active ? "🟢" : "⚪"} <b>${esc(c.instance)}</b> — ${esc(c.type || "?")} · ${esc(c.status || "?")}${c.ip ? " · " + esc(c.ip) : ""}${c.active ? ' <span class="wan-active">activa</span>' : ""}</div>`).join("");
    prefillWanForm();
  } catch (e) { /* silencioso */ }
}

// refrescar la WAN real del equipo (evita datos cacheados viejos)
async function wanRefreshFromDevice(silent) {
  try {
    await api(`/devices/${enc(S.current)}/refresh?object=${encodeURIComponent("InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1")}`, { method: "POST" });
    for (let i = 0; i < 5; i++) {
      await new Promise(r => setTimeout(r, 2500));
      await loadWan();
      const st = await api(`/devices/${enc(S.current)}/status`); renderStatus(st);
    }
    if (!silent) toast("✓ WAN actualizada", "ok");
  } catch (e) { if (!silent) toast(e.message, "err"); }
}
$("#wan-refresh").addEventListener("click", async () => {
  const b = $("#wan-refresh"); b.disabled = true; const p = b.textContent; b.textContent = "Actualizando…";
  await wanRefreshFromDevice(false);
  b.disabled = false; b.textContent = p;
});

// ---- Respaldo / auto-restauración ----
async function loadBackup() {
  try {
    const b = await api(`/devices/${enc(S.current)}/backup`);
    $("#backup-auto").checked = !!b.autorestore;
    if (b.exists) {
      const n = Object.keys(b.params || {}).length;
      $("#backup-info").textContent = `Respaldo guardado: ${n} parámetros · ${fmtDate(b.updated_at)}`;
    } else {
      $("#backup-info").textContent = "Aún no hay respaldo. Pulsa \"Guardar respaldo\".";
    }
  } catch (e) { toast(e.message, "err"); }
}

async function backupSave() {
  toast("Leyendo y guardando configuración…", "info");
  const r = await api(`/devices/${enc(S.current)}/backup`, { method: "POST" });
  toast("✓ " + (r.detail || "Respaldo guardado"), "ok"); loadBackup();
}
async function backupRestore() {
  if (!confirm("¿Restaurar la configuración guardada en el equipo?")) return;
  const r = await api(`/devices/${enc(S.current)}/restore`, { method: "POST" });
  if (r.ok === false) return toast(r.detail, "err");
  report(r); refreshAfterChange(r);
}

$("#backup-auto").addEventListener("change", async () => {
  try {
    await api(`/devices/${enc(S.current)}/autorestore`, { method: "POST", body: { enabled: $("#backup-auto").checked } });
    toast($("#backup-auto").checked ? "✓ Auto-restauración activada" : "Auto-restauración desactivada", "ok");
    loadBackup();
  } catch (e) { toast(e.message, "err"); $("#backup-auto").checked = !$("#backup-auto").checked; }
});

// ---- Avanzado: explorador de todo el árbol del modelo ----
let advCache = [];
async function loadParams() {
  try {
    const r = await api(`/devices/${enc(S.current)}/params`);
    advCache = r.params;
    renderParams();
  } catch (e) { toast(e.message, "err"); }
}
// fila de parámetro reutilizable (Avanzado e IPv6). Los editables llevan data-ppath/data-ptype
function paramRow(p) {
  const val = p.value == null ? "" : String(p.value);
  if (p.writable) {
    return `<div class="adv-row"><div class="adv-path">${esc(p.path)}<span class="w">✎</span></div>
      <div class="adv-edit"><input value="${escAttr(val)}" data-ppath="${escAttr(p.path)}" data-ptype="${escAttr(p.type || '')}">
      <button class="ghost small" data-psave>Guardar</button></div></div>`;
  }
  return `<div class="adv-row"><div class="adv-path">${esc(p.path)}</div><div class="adv-val">${esc(val || "-")}</div></div>`;
}

function renderParams() {
  const q = $("#adv-search").value.toLowerCase();
  const wo = $("#adv-writable").checked;
  let items = advCache;
  if (wo) items = items.filter(p => p.writable);
  if (q) items = items.filter(p => p.path.toLowerCase().includes(q) || (p.value != null && String(p.value).toLowerCase().includes(q)));
  $("#adv-count").textContent = `${items.length} de ${advCache.length} parámetros`;
  $("#adv-list").innerHTML = items.slice(0, 400).map(paramRow).join("")
    || `<div class="muted">Sin resultados. Pulsa "Traer árbol completo" para descubrir todo el modelo.</div>`;
}

// guardar cualquier parámetro editable (Avanzado / IPv6)
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-psave]");
  if (!b) return;
  const input = b.previousElementSibling;
  const path = input.dataset.ppath, type = input.dataset.ptype;
  let value = input.value;
  const t = (type || "").toLowerCase();
  if (t.includes("bool")) value = (value === "true" || value === "1");
  else if (t.includes("int") || t.includes("long")) value = Number(value);
  b.disabled = true;
  try {
    const r = await api(`/devices/${enc(S.current)}/param`, { method: "PUT", body: { path, value, type: type || undefined } });
    toast(r.applied ? "✓ Aplicado" : "En cola (próximo reporte)", r.applied ? "ok" : "info");
  } catch (err) { toast(err.message, "err"); }
  finally { b.disabled = false; }
});

// ---- IPv6: parámetros IPv6 que exponga el modelo ----
async function loadIPv6() {
  try {
    const r = await api(`/devices/${enc(S.current)}/params?search=ipv6`);
    $("#ipv6-count").textContent = r.count ? `${r.count} parámetros IPv6` : "";
    $("#ipv6-list").innerHTML = r.count
      ? r.params.map(paramRow).join("")
      : `<div class="muted">Este equipo no expone parámetros IPv6 por TR-069. Pulsa "Traer parámetros IPv6" tras un refresco, o el firmware simplemente no los incluye.</div>`;
  } catch (e) { toast(e.message, "err"); }
}
async function ipv6Refresh() {
  toast("Buscando parámetros IPv6…", "info");
  await api(`/devices/${enc(S.current)}/refresh`, { method: "POST" });
  for (let i = 0; i < 6; i++) { await new Promise(r => setTimeout(r, 2500)); await loadIPv6(); if (($("#ipv6-count").textContent || "").length) break; }
}
$("#adv-search").addEventListener("input", renderParams);
$("#adv-writable").addEventListener("change", renderParams);

async function advRefresh() {
  toast("Descubriendo todo el árbol del equipo…", "info");
  await api(`/devices/${enc(S.current)}/refresh`, { method: "POST" });
  for (let i = 0; i < 6; i++) {
    await new Promise(r => setTimeout(r, 2500));
    await loadParams();
    if (advCache.length > 60) break;   // ya llegó el árbol grande
  }
  toast("✓ Árbol actualizado (" + advCache.length + " parámetros)", "ok");
}


// mostrar campos segun el modo WAN elegido
$("#wan-mode").addEventListener("change", () => {
  const m = $("#wan-mode").value;
  $("#wan-static").classList.toggle("hidden", m !== "static");
  $("#wan-pppoe").classList.toggle("hidden", m !== "pppoe");
});

// prellenar el formulario WAN con lo que tiene el equipo (modo + usuario/clave PPPoE)
function prefillWanForm() {
  const st = lastStatus; if (!st) return;
  let m = "dhcp";
  if (st.pppoe_enable) m = "pppoe";
  else if ((st.wan_mode || "").toLowerCase().startsWith("static")) m = "static";
  $("#wan-mode").value = m;
  $("#wan-static").classList.toggle("hidden", m !== "static");
  $("#wan-pppoe").classList.toggle("hidden", m !== "pppoe");
  $("#wanppp-user").value = (st.pppoe_user || "").trim();
  $("#wanppp-pass").value = (st.pppoe_password || "").trim();
}

$$("[data-back]").forEach(b => b.addEventListener("click", () => {
  $("#device-page").classList.add("hidden"); $("#devices-page").classList.remove("hidden");
  try { localStorage.removeItem("device"); } catch {}
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

// vacia los campos indicados tras aplicar un cambio
function clearInputs(...ids) { ids.forEach(id => { const el = $("#" + id); if (el) el.value = ""; }); }

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
    clearInputs("wifi-ssid", "wifi-pass", "wifi-channel");
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
    clearInputs("net-ip", "net-mask", "net-min", "net-max", "net-lease");
  },
  async wan() {
    const mode = $("#wan-mode").value;
    const body = { mode };
    let aviso = "¿Aplicar WAN por DHCP?";
    if (mode === "static") {
      body.ip = $("#wan-ip").value.trim();
      body.mask = $("#wan-mask").value.trim();
      body.gateway = $("#wan-gw").value.trim();
      const dns = $("#wan-dns").value.split(/\s+/).map(s => s.trim()).filter(Boolean);
      if (dns.length) body.dns = dns;
      if ($("#wan-mtu").value) body.mtu = +$("#wan-mtu").value;
      if (!body.ip || !body.mask || !body.gateway) return toast("IP, máscara y gateway son obligatorios en estático", "err");
      aviso = "⚠ VAS A PONER IP ESTÁTICA EN LA WAN.\n\nLa IP debe ser de la MISMA red por la que el equipo llega al ACS, o perderás la gestión remota.\n\n¿Continuar?";
    } else if (mode === "pppoe") {
      body.username = $("#wanppp-user").value.trim();
      body.password = $("#wanppp-pass").value;
      if (!body.username) return toast("Usuario PPPoE requerido", "err");
      aviso = "¿Aplicar WAN por PPPoE con el usuario " + body.username + "?";
    }
    if (!confirm(aviso)) return;
    const r = await api(`/devices/${enc(S.current)}/wan`, { method: "PUT", body });
    report(r);
    clearInputs("wan-ip", "wan-mask", "wan-gw", "wan-dns", "wan-mtu");
    wanRefreshFromDevice(true);   // refrescar la WAN real para reflejar el cambio
  },
  async pppoe() {
    const body = { enable: $("#ppp-enable").checked };
    if ($("#ppp-user").value.trim()) body.username = $("#ppp-user").value.trim();
    if ($("#ppp-pass").value) body.password = $("#ppp-pass").value;
    const r = await api(`/devices/${enc(S.current)}/pppoe`, { method: "PUT", body });
    report(r); refreshAfterChange(r);
    clearInputs("ppp-user", "ppp-pass");
  },
  async dns() {
    const servers = $("#dns-servers").value.split(/\s+/).map(s => s.trim()).filter(Boolean);
    if (!servers.length) return toast("Indica al menos un servidor DNS", "err");
    const r = await api(`/devices/${enc(S.current)}/dns`, { method: "PUT", body: { scope: $("#dns-scope").value, servers } });
    report(r); refreshAfterChange(r);
    clearInputs("dns-servers");
  },
  async time() {
    const body = {};
    if ($("#time-tz").value.trim()) body.timezone = $("#time-tz").value.trim();
    if ($("#time-ntp1").value.trim()) body.ntp1 = $("#time-ntp1").value.trim();
    if ($("#time-ntp2").value.trim()) body.ntp2 = $("#time-ntp2").value.trim();
    const r = await api(`/devices/${enc(S.current)}/time`, { method: "PUT", body });
    report(r); refreshAfterChange(r);
    clearInputs("time-tz", "time-ntp1", "time-ntp2");
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

// registrar acciones definidas fuera del objeto (evita usar 'actions' antes de crearlo)
actions["adv-refresh"] = advRefresh;
actions["ipv6-refresh"] = ipv6Refresh;
actions["backup-save"] = backupSave;
actions["backup-restore"] = backupRestore;

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
      `<tr><td>${u.id}</td><td>${esc(u.username)}</td><td>${esc(u.role)}</td><td>${esc(u.isp_tag || "-")}</td>
        <td>${u.active ? "sí" : "no"}</td>
        <td class="uactions">
          <button class="ghost small" data-uact="pass" data-u="${escAttr(u.username)}">Clave</button>
          <button class="ghost small" data-uact="toggle" data-u="${escAttr(u.username)}" data-active="${u.active}">${u.active ? "Desactivar" : "Activar"}</button>
          <button class="ghost small" data-uact="del" data-u="${escAttr(u.username)}">Eliminar</button>
        </td></tr>`).join("");
  } catch (e) { toast(e.message, "err"); }
}

// acciones sobre usuarios (admin)
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-uact]");
  if (!b) return;
  const u = b.dataset.u, act = b.dataset.uact;
  try {
    if (act === "pass") {
      const p = prompt("Nueva contraseña para " + u + " (mín. 6):");
      if (!p) return;
      if (p.length < 6) return toast("Mínimo 6 caracteres", "err");
      await api(`/auth/users/${encodeURIComponent(u)}/password`, { method: "PUT", body: { new_password: p } });
      toast("✓ Contraseña actualizada", "ok");
    } else if (act === "toggle") {
      const active = b.dataset.active !== "1" && b.dataset.active !== "true";
      await api(`/auth/users/${encodeURIComponent(u)}/active`, { method: "POST", body: { active } });
      toast("✓ Estado actualizado", "ok"); loadUsers();
    } else if (act === "del") {
      if (!confirm("¿Eliminar al usuario " + u + "?")) return;
      await api(`/auth/users/${encodeURIComponent(u)}`, { method: "DELETE" });
      toast("✓ Usuario eliminado", "ok"); loadUsers();
    }
  } catch (err) { toast(err.message, "err"); }
});

async function loadAccount() {
  try {
    const me = await api("/auth/me");
    $("#account-who").textContent = `Usuario: ${me.username} · rol: ${me.role}` + (me.isp ? ` · ISP: ${me.isp}` : "");
  } catch (e) { /* ignore */ }
}

$("#account-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const cur = $("#acc-current").value, n1 = $("#acc-new").value, n2 = $("#acc-new2").value;
  if (n1 !== n2) return toast("Las contraseñas nuevas no coinciden", "err");
  try {
    await api("/auth/me/password", { method: "PUT", body: { current_password: cur, new_password: n1 } });
    toast("✓ Contraseña cambiada", "ok"); $("#account-form").reset();
  } catch (err) { toast(err.message, "err"); }
});

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

// ===== Actualizaciones: Firmware y Respaldos separados (admin) =====
const fileKind = f => { const t=(f.metadata&&f.metadata.fileType)||f.fileType||""; return t.startsWith("3")?"config":t.startsWith("2")?"web":"firmware"; };

async function loadUpdates() {
  try {
    const files = await api("/firmware");
    const sz = f => f.length ? (f.length/1024/1024).toFixed(2)+" MB" : "-";
    for (const kind of ["firmware","config"]) {
      const list = files.filter(f => fileKind(f) === kind);
      const tbody = $(`table[data-list="${kind}"] tbody`);
      if (tbody) tbody.innerHTML = list.length
        ? list.map(f => { const n=f._id||f.filename||f.name; return `<tr><td>${esc(n)}</td><td>${sz(f)}</td><td><button class="ghost small" data-fwdel="${escAttr(n)}">Borrar</button></td></tr>`; }).join("")
        : `<tr><td colspan="3" class="muted">No hay ${kind==="firmware"?"firmwares":"archivos"} cargados.</td></tr>`;
      const sel = $(`.push-form[data-kind="${kind}"] .pf-file`);
      if (sel) sel.innerHTML = list.length
        ? list.map(f => { const n=f._id||f.filename||f.name; return `<option value="${escAttr(n)}">${esc(n)}</option>`; }).join("")
        : `<option value="">(sube un archivo primero)</option>`;
    }
  } catch (e) { toast(e.message, "err"); }
}

// sub-tabs Firmware / Respaldos
$$(".subtab").forEach(t => t.addEventListener("click", () => {
  $$(".subtab").forEach(x => x.classList.remove("active")); t.classList.add("active");
  $$("[data-subpanel]").forEach(p => p.classList.toggle("hidden", p.dataset.subpanel !== t.dataset.sub));
}));

// formularios de Actualizaciones (delegado; cubre firmware y config)
document.addEventListener("submit", async (e) => {
  const form = e.target;
  if (!(form.classList && (form.classList.contains("up-file") || form.classList.contains("up-url") || form.classList.contains("push-form")))) return;
  e.preventDefault();
  const kind = form.dataset.kind;
  try {
    if (form.classList.contains("up-file")) {
      const f = form.querySelector(".uf-file").files[0];
      if (!f) return toast("Elige un archivo", "err");
      const fd = new FormData();
      fd.append("file", f); fd.append("file_type", kind);
      fd.append("product_class", form.querySelector(".uf-model").value.trim());
      fd.append("version", form.querySelector(".uf-version").value.trim());
      fd.append("oui", form.querySelector(".uf-oui").value.trim());
      const r = await api("/firmware/upload", { method: "POST", form: fd });
      toast(`✓ Subido: ${r.file_name} (${(r.size/1024/1024).toFixed(1)} MB)`, "ok");
      form.reset(); loadUpdates();
    } else if (form.classList.contains("up-url")) {
      const body = { url: form.querySelector(".uu-url").value.trim(), file_name: form.querySelector(".uu-name").value.trim()||null, file_type: kind, product_class: form.querySelector(".uu-model").value.trim(), version: form.querySelector(".uu-version").value.trim() };
      toast("Descargando desde el URL…", "info");
      const r = await api("/firmware/upload-url", { method: "POST", body });
      toast(`✓ Guardado: ${r.file_name} (${(r.size/1024/1024).toFixed(1)} MB)`, "ok");
      form.reset(); loadUpdates();
    } else if (form.classList.contains("push-form")) {
      const file = form.querySelector(".pf-file").value;
      if (!file) return toast("No hay archivo seleccionado", "err");
      const target = form.querySelector(".pf-target").value;
      const val = form.querySelector(".pf-value").value.trim();
      if (target !== "all" && !val) return toast("Indica el valor del filtro", "err");
      const body = { file_name: file };
      if (target === "all") body.all = true;
      if (target === "tag") body.tag = val;
      if (target === "model") body.model = val;
      if (!confirm("¿Enviar " + file + " a los equipos seleccionados?")) return;
      const el = form.querySelector(".pf-result"); el.textContent = "Enviando…"; el.style.color = "var(--muted)";
      try {
        const r = await api("/firmware/push", { method: "POST", body });
        el.textContent = `✓ ${r.detail} (fallidos: ${r.failed || 0})`; el.style.color = "var(--ok)";
        toast("✓ Enviado a " + r.sent + " equipo(s)", "ok");
        form.reset(); form.querySelector(".pf-value-wrap").classList.add("hidden");
      } catch (err) { el.textContent = "✗ " + err.message; el.style.color = "var(--err)"; }
    }
  } catch (err) { toast(err.message, "err"); }
});

// mostrar/ocultar el valor del filtro segun destino (delegado)
document.addEventListener("change", (e) => {
  const sel = e.target;
  if (!(sel.classList && sel.classList.contains("pf-target"))) return;
  const form = sel.closest(".push-form");
  form.querySelector(".pf-value-wrap").classList.toggle("hidden", sel.value === "all");
  form.querySelector(".pf-value").placeholder = sel.value === "tag" ? "tag del ISP (p.ej. altala)" : "modelo (p.ej. WR3000 V1.0)";
});

// borrar archivo
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-fwdel]");
  if (!b) return;
  const name = b.dataset.fwdel;
  if (!confirm("¿Borrar el archivo " + name + "?")) return;
  try { await api(`/firmware/${encodeURIComponent(name)}`, { method: "DELETE" }); toast("Borrado", "ok"); loadUpdates(); }
  catch (err) { toast(err.message, "err"); }
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
  // restaurar la ultima vista (y equipo) en vez de volver siempre a Equipos
  let view = "devices", dev = null;
  try { view = localStorage.getItem("view") || "devices"; dev = localStorage.getItem("device"); } catch {}
  if (["updates", "users", "settings"].includes(view) && S.role !== "admin") view = "devices";
  navigate(view);
  if (dev) openDevice(dev);
}

if (S.token) { boot().catch(() => showLogin()); } else { showLogin(); }
