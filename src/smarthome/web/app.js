"use strict";

// App da central: dispositivos por cômodo e pedidos por texto ou voz.
// Fala só com a API da própria central, sem dependências externas.

const ICONS = { light: "💡", tv: "📺", ac: "❄️", switch: "🔌", sensor: "🌡️", other: "⚙️" };
const AC_MODES = { cool: "frio", heat: "quente", dry: "seco", fan: "ventilar", auto: "auto" };
const FAN_SPEEDS = { low: "baixa", medium: "média", high: "alta", auto: "auto" };
const TV_APPS = ["netflix", "youtube", "globoplay", "prime video"];
const ALL_ROOMS = "__all__";
const NO_ROOM = "Sem cômodo";

const $ = (sel) => document.querySelector(sel);

const state = {
  home: null,
  devices: [],
  room: ALL_ROOMS,
  open: null, // linha com o detalhe aberto por toque (celular)
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
  // Primeiro o cadastro (instantâneo), depois o estado (consulta nuvens e rede, pode demorar).
  const [home, devices] = await Promise.all([api("/home"), api("/devices")]);
  state.home = home;
  state.devices = devices.map((d) => ({ ...d, state: undefined }));
  document.title = home.name;
  $("#home-name").textContent = home.name;
  renderRooms();
  renderDevices();
  await refreshStates();
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
    main.append(renderRoom(room, devices));
  }
}

// Um painel por cômodo: cabeçalho com "Desligar tudo" e uma linha por aparelho.
function renderRoom(room, devices) {
  const section = document.createElement("section");
  section.className = "room";
  const header = document.createElement("header");
  const title = document.createElement("h2");
  title.textContent = room;
  header.append(title);
  const lit = devices.filter((d) => d.state?.power && d.capabilities.includes("turn_off"));
  if (lit.length) {
    const off = document.createElement("button");
    off.type = "button";
    off.className = "room-off";
    off.textContent = "Desligar tudo";
    off.addEventListener("click", () => lit.forEach((d) => send(d, "turn_off")));
    header.append(off);
  }
  section.append(header, ...devices.map(renderCard));
  return section;
}

function statusText(device) {
  const s = device.state;
  if (s === undefined) return "Consultando...";
  const a = (s && s.attributes) || {};
  if (!s || !s.online) {
    return device.type === "tv" && device.config.mac ? "Em espera · acorda pela rede" : "Sem conexão";
  }
  const parts = [];
  if (s.power === null) parts.push("Estado desconhecido");
  else if (!s.power) parts.push(device.type === "light" ? "Apagada" : device.type === "tv" ? "Desligada" : "Desligado");
  else if (device.type === "ac") {
    parts.push(`${AC_MODES[a.mode] || a.mode || "ligado"}${a.target_temperature != null ? ` ${a.target_temperature}°C` : ""}`);
    if (a.fan_speed) parts.push(`vent. ${FAN_SPEEDS[a.fan_speed] || a.fan_speed}`);
  }
  else if (device.type === "tv") parts.push(a.app || "Ligada", ...(a.volume != null ? [`volume ${a.volume}`] : []));
  else if (device.type === "light") parts.push("Acesa", ...(a.brightness != null ? [`${a.brightness}%`] : []));
  else parts.push("Ligado");
  if (a.muted) parts.push("mudo");
  if (a.temperature != null) parts.push(`ambiente ${a.temperature}°C`);
  if (a.via) parts.push(`via ${a.via}`);
  return parts.join(" · ");
}

const canHover = window.matchMedia("(hover: hover)").matches;

// Linha do aparelho: ícone | nome e estado | controle principal | interruptor.
// O detalhe (apps, modos) aparece ao passar o mouse; no celular, ao tocar na linha.
function renderCard(device) {
  const row = document.createElement("article");
  row.className = "row";
  row.dataset.id = device.id;
  const s = device.state || {};
  const attrs = s.attributes || {};
  row.classList.toggle("on", !!s.power);
  row.classList.toggle("offline", s.online === false);
  row.classList.toggle("open", state.open === device.id);
  const can = (cmd) => device.capabilities.includes(cmd);

  const main = document.createElement("div");
  main.className = "main";
  main.innerHTML = `<span class="icon" aria-hidden="true"></span><div class="title"><h3></h3><p class="status"></p></div>`;
  main.querySelector(".icon").textContent = ICONS[device.type] || ICONS.other;
  main.querySelector("h3").textContent = device.name;
  main.querySelector(".status").textContent = statusText(device);

  const quick = document.createElement("div");
  quick.className = "quick";
  if (can("set_temperature")) {
    quick.append(stepper(attrs.target_temperature ?? 23, (celsius) => send(device, "set_temperature", { celsius })));
  } else if (can("set_volume")) {
    quick.append(slider("Volume", attrs.volume ?? 20, "", (level) => send(device, "set_volume", { level })));
  } else if (can("set_brightness")) {
    quick.append(slider("Brilho", attrs.brightness ?? 100, "%", (level) => send(device, "set_brightness", { level })));
  }
  quick.addEventListener("click", (e) => e.stopPropagation());
  main.append(quick);

  if (can("turn_on") || can("turn_off")) {
    const sw = document.createElement("label");
    sw.className = "switch";
    sw.setAttribute("aria-label", `Ligar ou desligar ${device.name}`);
    sw.innerHTML = `<input type="checkbox" class="power"><span></span>`;
    const power = sw.querySelector("input");
    power.checked = !!s.power;
    power.addEventListener("change", () => send(device, power.checked ? "turn_on" : "turn_off"));
    sw.addEventListener("click", (e) => e.stopPropagation());
    main.append(sw);
  }
  row.append(main);

  const more = document.createElement("div");
  more.className = "more";
  const inner = document.createElement("div");
  inner.className = "inner";
  if (can("set_mode")) {
    inner.append(chips(Object.entries(AC_MODES), attrs.mode, (mode) => send(device, "set_mode", { mode })));
  }
  if (can("set_fan_speed")) {
    inner.append(labeled("Ventilação", chips(Object.entries(FAN_SPEEDS), attrs.fan_speed, (speed) => send(device, "set_fan_speed", { speed }))));
  }
  if (can("open_app")) {
    const options = TV_APPS.map((app) => [app, app]);
    if (can("mute")) options.push(["__mute__", attrs.muted ? "tirar mudo" : "mudo"]);
    inner.append(
      chips(options, attrs.app, (value) =>
        value === "__mute__" ? send(device, "mute", { muted: !attrs.muted }) : send(device, "open_app", { app: value })
      )
    );
  }
  if (can("set_brightness") && !can("set_temperature")) {
    inner.append(chips([["25", "25%"], ["50", "50%"], ["100", "100%"]], String(attrs.brightness ?? ""), (v) => send(device, "set_brightness", { level: Number(v) })));
  }
  if (device.type === "tv" && s.online === false) {
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = device.config.mac
      ? "Ligar manda o pacote de acordar pela rede; a SmartThings pode demorar uns segundos para refletir."
      : "A central ainda não aprendeu o IP e o MAC desta TV: ligue-a uma vez pelo controle com a central rodando.";
    inner.append(hint);
  }
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "remove";
  remove.textContent = "Remover";
  remove.title = "Tirar este aparelho do cadastro da central";
  remove.addEventListener("click", () => removeDevice(device));
  inner.append(remove);
  if (inner.childElementCount) {
    inner.addEventListener("click", (e) => e.stopPropagation());
    const clip = document.createElement("div");
    clip.append(inner);
    more.append(clip);
    row.append(more);
    row.classList.add("has-more");
    if (!canHover) {
      row.addEventListener("click", () => {
        state.open = state.open === device.id ? null : device.id;
        renderDevices();
      });
    }
  }
  return row;
}

function slider(label, value, unit, onCommit) {
  const row = document.createElement("label");
  row.className = "control";
  row.innerHTML = `<input type="range" min="0" max="100" step="1"><output></output>`;
  const input = row.querySelector("input");
  input.setAttribute("aria-label", label);
  const output = row.querySelector("output");
  input.value = value;
  output.textContent = value + unit;
  input.addEventListener("input", () => (output.textContent = input.value + unit));
  input.addEventListener("change", () => onCommit(Number(input.value)));
  return row;
}

function stepper(value, onCommit) {
  const row = document.createElement("div");
  row.className = "stepper";
  row.innerHTML = `<button type="button" aria-label="Diminuir temperatura">−</button><output></output>
    <button type="button" aria-label="Aumentar temperatura">+</button>`;
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

// Rótulo curto antes de um grupo de chips (ex.: "Ventilação" no ar-condicionado)
function labeled(text, control) {
  const box = document.createElement("div");
  box.className = "labeled";
  const label = document.createElement("span");
  label.textContent = text;
  box.append(label, control);
  return box;
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

async function removeDevice(device) {
  const what = device.adapter === "virtual" ? `${device.name} (exemplo virtual)` : device.name;
  if (!confirm(`Remover ${what} do cadastro da central?`)) return;
  try {
    await api(`/devices/${encodeURIComponent(device.id)}`, { method: "DELETE" });
    state.open = null;
    await load();
  } catch (err) {
    alert(err.message);
  }
}

async function send(device, command, params = {}) {
  const card = document.querySelector(`.row[data-id="${CSS.escape(device.id)}"]`);
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

let refreshing = false;

async function refreshStates() {
  // Um pedido por aparelho, em paralelo: cada card atualiza assim que a resposta dele
  // chega, em vez de a tela inteira esperar o mais lento (nuvem da LG, TV em espera...).
  if (document.hidden || refreshing) return;
  refreshing = true;
  try {
    const devices = await api("/devices");
    const previous = new Map(state.devices.map((d) => [d.id, d.state]));
    const changed = devices.length !== state.devices.length || devices.some((d, i) => state.devices[i]?.id !== d.id);
    state.devices = devices.map((d) => ({ ...d, state: previous.get(d.id) }));
    if (changed) renderDevices();
    await Promise.all(state.devices.map(async (device) => {
      try { device.state = await api(`/devices/${encodeURIComponent(device.id)}/state`); }
      catch { device.state = null; }
      const card = document.querySelector(`.row[data-id="${CSS.escape(device.id)}"]`);
      if (card) card.replaceWith(renderCard(device));
    }));
  } catch { /* tenta de novo no próximo ciclo */ }
  finally { refreshing = false; }
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
  let picked = null; // item da descoberta escolhido; o cadastro leva o config inteiro dele

  $("#add-device").addEventListener("click", async () => {
    form.reset();
    form.device_id.value = "";
    picked = null;
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
            picked = d;
            form.name.value = d.name.replace(/^\[TV\]\s*/i, "");
            form.type.value = d.suggested_type;
            form.adapter.value = d.adapter;
            form.device_id.value = d.config.device_id || "";
            form.host.value = d.config.host || "";
            form.mac.value = d.config.mac || "";
            syncTvFields();
          }
        )
      );
    } catch { /* sem descoberta: segue com o cadastro manual */ }
  });

  $("#add-cancel").addEventListener("click", () => dialog.close());

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const config = { ...((picked && picked.config) || {}) };
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
      if (acc.status === "connected" && !acc.removable) {
        button.hidden = true; // veio de chave do .env: só quem configurou a central tira
      } else if (acc.status === "connected") {
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

// No celular, tocar fora de uma linha fecha o detalhe aberto.
document.addEventListener("click", (e) => {
  if (!canHover && state.open && !e.target.closest(".row")) {
    state.open = null;
    renderDevices();
  }
});

setupAddDialog();
setupAccountsDialog();
load().catch((error) => showReply(`Não consegui carregar a casa: ${error.message}`));
setInterval(refreshStates, 15000);
document.addEventListener("visibilitychange", refreshStates);
