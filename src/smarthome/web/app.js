"use strict";

// App da central: dispositivos por cômodo e pedidos por texto ou voz.
// Fala só com a API da própria central, sem dependências externas.

const ICONS = { light: "💡", tv: "📺", ac: "❄️", switch: "🔌", sensor: "🌡️", other: "⚙️" };
const AC_MODES = { cool: "frio", heat: "quente", dry: "seco", fan: "ventilar", auto: "auto" };
const TV_APPS = ["netflix", "youtube", "globoplay", "prime video"];
const ALL_ROOMS = "__all__";
const NO_ROOM = "Sem cômodo";

const $ = (sel) => document.querySelector(sel);

const state = {
  home: null,
  devices: [],
  room: ALL_ROOMS,
  sessionId: loadSessionId(),
};

function loadSessionId() {
  const fresh = "web-" + Math.random().toString(36).slice(2, 10);
  try {
    const saved = localStorage.getItem("smarthome.session");
    if (saved) return saved;
    localStorage.setItem("smarthome.session", fresh);
  } catch { /* navegação privada: uma sessão por aba */ }
  return fresh;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) throw new Error((data && data.detail) || `Erro ${response.status}`);
  return data;
}

// ---------- Carregar e desenhar ----------

async function load() {
  const [home, devices] = await Promise.all([api("/home"), api("/devices/with-state")]);
  state.home = home;
  state.devices = devices;
  document.title = home.name;
  $("#home-name").textContent = home.name;
  renderRooms();
  renderDevices();
}

function roomsInUse() {
  const used = new Set(state.devices.map((d) => d.room || NO_ROOM));
  const ordered = state.home.rooms.filter((r) => used.has(r));
  for (const r of used) if (!ordered.includes(r)) ordered.push(r);
  return ordered;
}

function renderRooms() {
  const nav = $("#rooms");
  nav.replaceChildren();
  $("#room-list").replaceChildren(
    ...state.home.rooms.map((r) => Object.assign(document.createElement("option"), { value: r }))
  );
  const rooms = roomsInUse();
  if (rooms.length < 2) return;
  if (state.room !== ALL_ROOMS && !rooms.includes(state.room)) state.room = ALL_ROOMS;
  for (const [value, label] of [[ALL_ROOMS, "Tudo"], ...rooms.map((r) => [r, r])]) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.setAttribute("aria-pressed", String(state.room === value));
    button.addEventListener("click", () => {
      state.room = value;
      renderRooms();
      renderDevices();
    });
    nav.append(button);
  }
}

function renderDevices() {
  const main = $("#devices");
  main.replaceChildren();
  $("#empty").hidden = state.devices.length > 0;
  const rooms = state.room === ALL_ROOMS ? roomsInUse() : [state.room];
  for (const room of rooms) {
    const devices = state.devices.filter((d) => (d.room || NO_ROOM) === room);
    if (!devices.length) continue;
    if (rooms.length > 1) {
      const title = document.createElement("h2");
      title.className = "room-title";
      title.textContent = room;
      main.append(title);
    }
    for (const device of devices) main.append(renderCard(device));
  }
}

function statusText(device) {
  const s = device.state;
  if (!s || !s.online) return "Sem conexão";
  const a = s.attributes || {};
  const parts = [s.power === null ? "Estado desconhecido" : s.power ? "Ligado" : "Desligado"];
  if (s.power) {
    if (a.brightness != null) parts.push(`${a.brightness}%`);
    if (a.target_temperature != null) parts.push(`${a.target_temperature}°C`);
    if (a.mode) parts.push(AC_MODES[a.mode] || a.mode);
    if (a.app) parts.push(a.app);
    if (a.volume != null) parts.push(`volume ${a.volume}`);
  }
  if (a.temperature != null) parts.push(`ambiente ${a.temperature}°C`);
  if (a.via) parts.push(`via ${a.via}`);
  return parts.join(" · ");
}

function renderCard(device) {
  const card = $("#device-card").content.firstElementChild.cloneNode(true);
  card.dataset.id = device.id;
  const s = device.state || {};
  const attrs = s.attributes || {};
  card.classList.toggle("on", !!s.power);
  card.classList.toggle("offline", s.online === false);
  card.querySelector(".icon").textContent = ICONS[device.type] || ICONS.other;
  card.querySelector("h3").textContent = device.name;
  card.querySelector(".status").textContent = statusText(device);

  const can = (cmd) => device.capabilities.includes(cmd);
  const power = card.querySelector(".power");
  power.checked = !!s.power;
  power.setAttribute("aria-label", `Ligar ou desligar ${device.name}`);
  if (!can("turn_on") && !can("turn_off")) power.closest(".switch").hidden = true;
  power.addEventListener("change", () => send(device, power.checked ? "turn_on" : "turn_off"));

  const controls = card.querySelector(".controls");
  if (can("set_brightness")) {
    controls.append(slider("Brilho", attrs.brightness ?? 100, "%", (level) => send(device, "set_brightness", { level })));
  }
  if (can("set_temperature")) {
    controls.append(stepper(attrs.target_temperature ?? 23, (celsius) => send(device, "set_temperature", { celsius })));
  }
  if (can("set_mode")) {
    controls.append(chips(Object.entries(AC_MODES), attrs.mode, (mode) => send(device, "set_mode", { mode })));
  }
  if (can("set_volume")) {
    controls.append(slider("Volume", attrs.volume ?? 20, "", (level) => send(device, "set_volume", { level })));
  }
  if (can("open_app")) {
    const options = TV_APPS.map((app) => [app, app]);
    if (can("mute")) options.push(["__mute__", attrs.muted ? "tirar mudo" : "mudo"]);
    controls.append(
      chips(options, attrs.app, (value) =>
        value === "__mute__" ? send(device, "mute", { muted: !attrs.muted }) : send(device, "open_app", { app: value })
      )
    );
  }
  return card;
}

function slider(label, value, unit, onCommit) {
  const row = document.createElement("label");
  row.className = "control";
  row.innerHTML = `<span></span><input type="range" min="0" max="100" step="1"><output></output>`;
  row.firstElementChild.textContent = label;
  const input = row.querySelector("input");
  const output = row.querySelector("output");
  input.value = value;
  output.textContent = value + unit;
  input.addEventListener("input", () => (output.textContent = input.value + unit));
  input.addEventListener("change", () => onCommit(Number(input.value)));
  return row;
}

function stepper(value, onCommit) {
  const row = document.createElement("div");
  row.className = "control";
  row.innerHTML = `<span>Temperatura</span><div class="stepper">
    <button type="button" aria-label="Diminuir temperatura">−</button><output></output>
    <button type="button" aria-label="Aumentar temperatura">+</button></div>`;
  const output = row.querySelector("output");
  let current = Number(value);
  let timer;
  const show = () => (output.textContent = `${current}°C`);
  const change = (delta) => {
    current = Math.min(30, Math.max(16, current + delta));
    show();
    clearTimeout(timer); // espera parar de clicar antes de mandar
    timer = setTimeout(() => onCommit(current), 600);
  };
  const [minus, plus] = row.querySelectorAll("button");
  minus.addEventListener("click", () => change(-1));
  plus.addEventListener("click", () => change(1));
  show();
  return row;
}

function chips(options, selected, onPick) {
  const row = document.createElement("div");
  row.className = "chips";
  for (const [value, label] of options) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.setAttribute("aria-pressed", String(value === selected));
    button.addEventListener("click", () => onPick(value));
    row.append(button);
  }
  return row;
}

// ---------- Comandos ----------

async function send(device, command, params = {}) {
  const card = document.querySelector(`.card[data-id="${CSS.escape(device.id)}"]`);
  card?.classList.add("busy");
  try {
    const result = await api(`/devices/${encodeURIComponent(device.id)}/commands`, {
      method: "POST",
      body: { command, params },
    });
    if (!result.ok) showReply(result.message || `Não consegui falar com ${device.name}.`);
    else if (result.message) showReply(result.message);
    if (result.state) device.state = result.state;
  } catch (error) {
    showReply(error.message);
  }
  card?.replaceWith(renderCard(device));
}

async function refreshStates() {
  if (document.hidden) return;
  try {
    state.devices = await api("/devices/with-state");
    renderDevices();
  } catch { /* tenta de novo no próximo ciclo */ }
}

// ---------- Assistente: texto e voz ----------

function showReply(text, thinking = false) {
  const reply = $("#reply");
  reply.textContent = text;
  reply.classList.toggle("thinking", thinking);
}

async function ask(text, { speakReply = false } = {}) {
  text = text.trim();
  if (!text) return;
  showReply(`"${text}" `, true);
  $("#ask-input").value = "";
  try {
    const { reply } = await api("/agent", { method: "POST", body: { text, session_id: state.sessionId } });
    showReply(reply);
    if (speakReply) speak(reply);
    await load(); // o agente pode ter mudado estados ou cadastrado algo
  } catch (error) {
    showReply(error.message);
  }
}

function speak(text) {
  if (!("speechSynthesis" in window) || !text) return;
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "pt-BR";
  const voice = speechSynthesis.getVoices().find((v) => v.lang.toLowerCase().startsWith("pt-br"));
  if (voice) utterance.voice = voice;
  speechSynthesis.cancel();
  speechSynthesis.speak(utterance);
}

function setupVoice() {
  const mic = $("#mic");
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition || !window.isSecureContext) {
    mic.addEventListener("click", () =>
      showReply(
        !window.isSecureContext
          ? "A voz no navegador precisa de HTTPS (ou localhost). Veja o README para abrir a central com HTTPS."
          : "Este navegador não reconhece voz. Use o Chrome, o Edge ou o Safari, ou digite o pedido."
      )
    );
    return;
  }
  const recognition = new Recognition();
  recognition.lang = "pt-BR";
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;
  let listening = false;
  let finalText = "";

  recognition.addEventListener("start", () => {
    listening = true;
    finalText = "";
    mic.classList.add("listening");
    showReply("Ouvindo…");
  });
  recognition.addEventListener("result", (event) => {
    const transcript = Array.from(event.results).map((r) => r[0].transcript).join("");
    $("#ask-input").value = transcript;
    if (event.results[event.results.length - 1].isFinal) finalText = transcript;
  });
  recognition.addEventListener("end", () => {
    listening = false;
    mic.classList.remove("listening");
    if (finalText) ask(finalText, { speakReply: true });
    else if ($("#reply").textContent === "Ouvindo…") showReply("");
  });
  recognition.addEventListener("error", (event) => {
    if (event.error === "not-allowed") showReply("Permita o uso do microfone para falar com a central.");
    else if (event.error !== "no-speech" && event.error !== "aborted") showReply(`Erro no microfone: ${event.error}`);
  });
  mic.addEventListener("click", () => (listening ? recognition.stop() : recognition.start()));
}

// ---------- Adicionar dispositivo ----------

function setupAddDialog() {
  const dialog = $("#add-dialog");
  const form = $("#add-form");
  const error = $("#add-error");
  const tvFields = form.querySelector(".tv-fields");
  const syncTvFields = () => (tvFields.hidden = !["samsung_tv", "lg_tv"].includes(form.adapter.value));
  form.adapter.addEventListener("change", syncTvFields);

  $("#add-device").addEventListener("click", async () => {
    form.reset();
    form.device_id.value = "";
    error.hidden = true;
    syncTvFields();
    const list = $("#discovered");
    list.replaceChildren();
    dialog.showModal();
    try {
      const found = await api("/discover");
      const integrations = await api("/integrations").catch(() => []);
      // Explica por que uma integração não trouxe nada (desligada, sem token, erro).
      for (const item of integrations) {
        const line = document.createElement("p");
        line.className = item.status === "active" ? "integration ok" : "integration warn";
        line.textContent = `${item.label}: ${item.detail}`;
        list.append(line);
      }
      if (!found.length) {
        if (integrations.some((i) => i.status === "active")) {
          const none = document.createElement("p");
          none.textContent = "Nenhum aparelho novo encontrado nas integrações conectadas.";
          list.append(none);
        }
        return;
      }
      const intro = document.createElement("p");
      intro.textContent = "Encontrados nas suas integrações:";
      list.append(
        intro,
        chips(
          found.map((d, i) => [String(i), d.name]),
          null,
          (i) => {
            const d = found[Number(i)];
            form.name.value = d.name;
            form.type.value = d.suggested_type;
            form.adapter.value = d.adapter;
            form.device_id.value = d.external_id;
            syncTvFields();
          }
        )
      );
    } catch { /* sem descoberta: segue com o cadastro manual */ }
  });

  $("#add-cancel").addEventListener("click", () => dialog.close());

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const config = {};
    if (form.device_id.value) config.device_id = form.device_id.value;
    if (!tvFields.hidden) {
      if (form.host.value) config.host = form.host.value.trim();
      if (form.mac.value) config.mac = form.mac.value.trim();
    }
    try {
      await api("/devices", {
        method: "POST",
        body: {
          name: form.name.value.trim(),
          room: form.room.value.trim() || null,
          type: form.type.value,
          adapter: form.adapter.value,
          config,
        },
      });
      dialog.close();
      await load();
    } catch (e) {
      error.textContent = e.message;
      error.hidden = false;
    }
  });
}

// ---------- Início ----------

$("#ask-form").addEventListener("submit", (event) => {
  event.preventDefault();
  ask($("#ask-input").value);
});
setupVoice();
// ---------- Contas de fabricante ----------

function setupAccountsDialog() {
  const dialog = $("#accounts-dialog");
  const list = $("#accounts-list");
  const error = $("#accounts-error");
  const tuyaFlow = $("#tuya-flow");
  const lgFlow = $("#lg-flow");
  const qrBox = $("#tuya-qr");
  let polling = null;

  const fail = (message) => { error.textContent = message; error.hidden = false; };
  const stopPolling = () => { if (polling) clearInterval(polling); polling = null; };

  async function render() {
    error.hidden = true;
    const accounts = await api("/accounts");
    list.replaceChildren();
    for (const acc of accounts) {
      const row = document.createElement("div");
      row.className = `account ${acc.status}`;
      const info = document.createElement("div");
      const name = document.createElement("div");
      name.className = "name";
      name.textContent = acc.label;
      const detail = document.createElement("p");
      detail.className = "detail";
      detail.textContent = acc.detail;
      info.append(name, detail);
      const button = document.createElement("button");
      button.type = "button";
      if (acc.status === "connected") {
        button.className = "ghost";
        button.textContent = "Desconectar";
        button.addEventListener("click", async () => {
          try { await api(`/accounts/${acc.id}`, { method: "DELETE" }); await render(); await load(); }
          catch (e) { fail(e.message); }
        });
      } else {
        button.textContent = "Conectar";
        button.disabled = acc.status === "unavailable";
        button.addEventListener("click", () => connect(acc.id));
      }
      row.append(info, button);
      list.append(row);
    }
  }

  async function connect(id) {
    error.hidden = true;
    tuyaFlow.hidden = id !== "tuya";
    lgFlow.hidden = id !== "lg_thinq";
    if (id === "smartthings") {
      try {
        const { url } = await api("/accounts/smartthings/start", { method: "POST" });
        window.location.assign(url); // volta para cá depois do login na Samsung
      } catch (e) { fail(e.message); }
    }
  }

  $("#tuya-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const userCode = event.target.user_code.value.trim();
    stopPolling();
    try {
      const { token, qr } = await api("/accounts/tuya/qr", { method: "POST", body: { user_code: userCode } });
      qrBox.querySelector("img").src = qr;
      qrBox.hidden = false;
      polling = setInterval(async () => {
        try {
          const result = await api("/accounts/tuya/poll", { method: "POST", body: { token, user_code: userCode } });
          if (result.status === "connected") {
            stopPolling();
            qrBox.hidden = true;
            tuyaFlow.hidden = true;
            showReply("Conta Smart Life conectada. Use Adicionar para cadastrar os aparelhos.");
            await render();
          }
        } catch (e) { stopPolling(); fail(e.message); }
      }, 2000);
    } catch (e) { fail(e.message); }
  });

  $("#lg-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/accounts/lg_thinq", { method: "POST", body: { token: event.target.token.value } });
      event.target.reset();
      lgFlow.hidden = true;
      await render();
    } catch (e) { fail(e.message); }
  });

  $("#open-accounts").addEventListener("click", async () => {
    tuyaFlow.hidden = lgFlow.hidden = qrBox.hidden = true;
    dialog.showModal();
    try { await render(); } catch (e) { fail(e.message); }
  });
  $("#accounts-close").addEventListener("click", () => { stopPolling(); dialog.close(); });
  dialog.addEventListener("close", stopPolling);

  // Volta do login da Samsung: /?conta=smartthings[&erro=...]
  const params = new URLSearchParams(window.location.search);
  if (params.get("conta")) {
    history.replaceState(null, "", "/");
    const err = params.get("erro");
    showReply(err ? "A conexão com a Samsung não foi concluída. Tente de novo em Contas." : "Conta SmartThings conectada. Use Adicionar para cadastrar os aparelhos.");
  }
}

setupAddDialog();
setupAccountsDialog();
load().catch((error) => showReply(`Não consegui carregar a casa: ${error.message}`));
setInterval(refreshStates, 15000);
document.addEventListener("visibilitychange", refreshStates);
