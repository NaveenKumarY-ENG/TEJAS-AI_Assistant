/* ───────────────────────────────────────────────
   Tejas — client
   ─────────────────────────────────────────────── */

const $ = (id) => document.getElementById(id);

const thread = $("thread");
const threadEmpty = $("threadEmpty");
const form   = $("form");
const input  = $("input");
const send   = $("send");

let socket = null;
let busy = false;
let currentBody = null;
let openTools = [];

/* ── hologram core ─────────────────────────────────
   A dense radial corona of light filaments whose outer edge is shaped by
   layered, out-of-phase sine harmonics rather than a single radius — the
   silhouette stays roughly circular but ripples unevenly around itself and
   drifts over time, instead of spinning like a rigid disc. */

function noiseAngle(theta, t, seed) {
  return (
    Math.sin(theta * 3 + t * 1.3 + seed) * 0.5 +
    Math.sin(theta * 5 - t * 0.9 + seed * 1.7) * 0.28 +
    Math.sin(theta * 8 + t * 2.1) * 0.16 +
    Math.sin(theta * 1.5 - t * 0.5) * 0.35
  );
}

function hologram(canvas, opts) {
  const o = Object.assign(
    { spokes: 150, baseR: 0.30, coreR: 0.10, ringCount: 2, dpr: true },
    opts
  );
  const ctx = canvas.getContext("2d");
  const dpr = o.dpr ? Math.min(devicePixelRatio || 1, 2) : 1;
  let W, H, CX, CY, R;

  function resize() {
    W = canvas.clientWidth || canvas.width;
    H = canvas.clientHeight || canvas.height;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    CX = W / 2; CY = H / 2;
    R = Math.min(W, H) * o.baseR;
  }
  resize();

  let t = 0;

  function frame() {
    const e = busy ? 1 : 0;
    t += 0.006 + e * 0.02;

    ctx.clearRect(0, 0, W, H);
    ctx.globalCompositeOperation = "lighter";

    const rot = t * 0.15;
    const amp = 0.5 + e * 0.35;

    for (let i = 0; i < o.spokes; i++) {
      const theta = (i / o.spokes) * Math.PI * 2 + rot;
      const n = noiseAngle(theta, t, i * 0.37);
      const outer = R * (1 + n * amp);
      const inner = R * (0.2 + Math.abs(Math.sin(theta * 2 + t)) * 0.08);

      const x1 = CX + Math.cos(theta) * inner, y1 = CY + Math.sin(theta) * inner;
      const x2 = CX + Math.cos(theta) * outer, y2 = CY + Math.sin(theta) * outer;

      const bright = 0.15 + Math.max(0, n) * 0.6;
      ctx.strokeStyle = `rgba(255,${150 + Math.round(n * 40)},${40 + Math.round(Math.max(0, n) * 60)},${bright})`;
      ctx.lineWidth = 0.6 + Math.max(0, n) * 1.1;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();

      if (n > 0.4) {
        ctx.fillStyle = `rgba(255,222,165,${(n - 0.4) * 0.9})`;
        ctx.beginPath();
        ctx.arc(x2, y2, 1.1, 0, 6.283);
        ctx.fill();
      }
    }

    for (let r = 0; r < o.ringCount; r++) {
      ctx.beginPath();
      for (let a = 0; a <= 64; a++) {
        const theta = (a / 64) * Math.PI * 2;
        const n = noiseAngle(theta, t * 0.8 + r * 3, r * 5);
        const rad = R * (0.6 + r * 0.16) * (1 + n * 0.12 * amp);
        const x = CX + Math.cos(theta) * rad, y = CY + Math.sin(theta) * rad;
        if (a === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.closePath();
      ctx.strokeStyle = r === 0
        ? `rgba(255,190,110,${0.22 + e * 0.15})`
        : `rgba(255,230,190,${0.13 + e * 0.1})`;
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    const coreR = R * o.coreR * (1 + Math.sin(t * 2.2) * 0.08 + e * 0.15);
    const grad = ctx.createRadialGradient(CX, CY, 0, CX, CY, coreR * 3.2);
    grad.addColorStop(0, "rgba(255,236,205,0.9)");
    grad.addColorStop(0.35, `rgba(255,160,60,${0.55 + e * 0.2})`);
    grad.addColorStop(1, "rgba(255,120,30,0)");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(CX, CY, coreR * 3.2, 0, 6.283);
    ctx.fill();

    ctx.globalCompositeOperation = "source-over";
    requestAnimationFrame(frame);
  }
  frame();
  return { resize };
}

const heroHologram = hologram($("hologram"), { spokes: 150, baseR: 0.30, coreR: 0.10, ringCount: 2 });
hologram($("mark"), { spokes: 34, baseR: 0.38, coreR: 0.22, ringCount: 1, dpr: false });

addEventListener("resize", () => heroHologram.resize());

/* ── greeting ──────────────────────────────────── */

(function setGreeting() {
  const h = new Date().getHours();
  const g = h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : h < 21 ? "Good evening" : "Good night";
  $("greetingText").textContent = `${g}.`;
})();

/* ── toast (for not-yet-wired nav items) ──────── */

let toastTimer = null;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 1800);
}

document.querySelectorAll("[data-soon]").forEach((el) => {
  el.addEventListener("click", () => toast(`${el.dataset.label || "This"} is coming soon`));
});

/* ── meta / widgets ────────────────────────────── */

fetch("/api/meta")
  .then((r) => r.json())
  .then((m) => {
    $("assistantName").textContent = m.assistant_name;
    $("modelName").textContent = m.model;
    $("wModel").textContent = m.model;
    $("wTools").textContent = m.tools.length;
    document.title = m.assistant_name;
  })
  .catch(() => {});

fetch("/api/sessions")
  .then((r) => r.json())
  .then((data) => {
    const list = $("sessionList");
    const sessions = data.sessions || [];
    if (!sessions.length) {
      list.textContent = "No sessions yet.";
      return;
    }
    list.innerHTML = "";
    sessions.slice(0, 5).forEach((s) => {
      const row = document.createElement("div");
      row.className = "session-row";
      row.innerHTML = `<span>Session #${s.id}</span><span class="mono">${s.message_count} msgs</span>`;
      list.appendChild(row);
    });
  })
  .catch(() => { $("sessionList").textContent = "Unavailable"; });

const WEATHER_CITY = "Bengaluru";
(async function loadWeather() {
  try {
    const geoRes = await fetch(
      `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(WEATHER_CITY)}&count=1`
    );
    const geo = await geoRes.json();
    const place = geo.results?.[0];
    if (!place) throw new Error("no location");
    $("wCity").textContent = place.name;

    const wxRes = await fetch(
      `https://api.open-meteo.com/v1/forecast?latitude=${place.latitude}&longitude=${place.longitude}` +
      `&current=temperature_2m,relative_humidity_2m&timezone=auto`
    );
    const wx = await wxRes.json();
    const c = wx.current;
    $("weatherBody").innerHTML =
      `<div class="wx-temp">${Math.round(c.temperature_2m)}°C</div>` +
      `<div class="wx-sub">Humidity ${c.relative_humidity_2m}%</div>`;
  } catch (e) {
    $("weatherBody").textContent = "Unavailable";
  }
})();

/* ── connection ────────────────────────────────── */

connect();

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${proto}://${location.host}/ws?resume=1`);

  socket.onopen = () => setStatus("Online", "on");

  socket.onclose = () => {
    setStatus("Reconnecting", "");
    setBusy(false);
    setTimeout(connect, 2000);
  };

  socket.onerror = () => setStatus("Offline", "err");
  socket.onmessage = (e) => handle(JSON.parse(e.data));
}

function setStatus(text, cls) {
  $("linkState").textContent = text;
  $("status").className = "status " + cls;
  $("statusTag").textContent = text;
  $("statusTag").className = "tag" + (cls === "on" ? " ok" : cls === "err" ? " err" : "");
}

/* ── events ────────────────────────────────────── */

function handle(msg) {
  switch (msg.type) {
    case "ready":
      $("wSession").textContent = "#" + msg.session_id;
      if (msg.history?.length) {
        msg.history.forEach((m) => {
          addMsg(m.role === "user" ? "user" : "assistant").textContent = m.content;
        });
        scroll();
      }
      break;

    case "tool":
      stopTyping();
      showTool(msg.name);
      break;

    case "chunk":
      closeTools();
      stopTyping();
      if (!currentBody) currentBody = addMsg("assistant");
      currentBody.textContent += msg.text;
      scroll();
      break;

    case "done":
      closeTools();
      stopTyping();
      currentBody = null;
      setBusy(false);
      break;

    case "error":
      closeTools();
      stopTyping();
      if (!currentBody) currentBody = addMsg("assistant");
      currentBody.textContent += `\n\n${msg.message}`;
      currentBody = null;
      setBusy(false);
      scroll();
      break;
  }
}

/* ── rendering ─────────────────────────────────── */

function addMsg(who) {
  if (threadEmpty.isConnected) threadEmpty.remove();

  const el = document.createElement("div");
  el.className = `msg ${who}`;

  const role = document.createElement("div");
  role.className = "msg-role";
  role.textContent = who === "user" ? "You" : $("assistantName").textContent;

  const body = document.createElement("div");
  body.className = "msg-body";

  el.append(role, body);
  thread.append(el);
  return body;
}

function showTool(name) {
  const pill = document.createElement("div");
  pill.className = "tool";
  pill.textContent = name.replace(/_/g, " ");
  thread.append(pill);
  openTools.push(pill);
  scroll();
}

function closeTools() {
  openTools.forEach((p) => p.classList.add("done"));
  openTools = [];
}

function startTyping() {
  stopTyping();
  const t = document.createElement("div");
  t.className = "typing";
  t.id = "typing";
  t.innerHTML = "<i></i><i></i><i></i>";
  thread.append(t);
  scroll();
}

const stopTyping = () => $("typing")?.remove();
const scroll = () => (thread.scrollTop = thread.scrollHeight);

/* ── sending ───────────────────────────────────── */

function setBusy(state) {
  busy = state;
  send.disabled = state;
  document.body.classList.toggle("busy", state);
  if (!state) input.focus();
}

function submit(text) {
  if (busy || !text.trim() || socket?.readyState !== WebSocket.OPEN) return;

  addMsg("user").textContent = text;
  socket.send(JSON.stringify({ text }));
  setBusy(true);
  startTyping();
  scroll();
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value;
  input.value = "";
  input.style.height = "auto";
  submit(text);
});

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
});

document.querySelectorAll(".chip").forEach((b) =>
  b.addEventListener("click", () => submit(b.dataset.q))
);

input.focus();
