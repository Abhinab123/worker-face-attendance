/* Kiosk: request a server-issued liveness challenge, capture a short frame burst, submit. */
(function () {
  "use strict";
  const root = document.getElementById("kiosk-app");
  if (!root) return;

  const $ = function (id) { return document.getElementById(id); };
  const cfg = $("kiosk-config").dataset;
  const FRAME_COUNT = parseInt(cfg.frameCount, 10) || 16;
  const FRAME_INTERVAL = parseInt(cfg.frameInterval, 10) || 180;
  const RESET_AFTER_MS = 7000;

  const video = $("video"), overlay = $("camera-overlay"), errorBox = $("camera-error");
  const banner = $("challenge-banner"), bannerText = $("challenge-text"), progress = $("capture-progress");
  const buttons = [$("btn-check-in"), $("btn-check-out")];
  const result = $("result"), resultTitle = $("result-title"), resultMsg = $("result-message"), hints = $("result-hints");
  let stream = null, busy = false, resetTimer = null;

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  function setButtons(enabled) { buttons.forEach(function (b) { b.disabled = !enabled; }); }

  function show(kind, title, message, hintList) {
    result.className = "result-card result-" + kind;
    resultTitle.textContent = title;
    resultMsg.textContent = message || "";
    hints.replaceChildren();
    (hintList || []).forEach(function (h) {
      const li = document.createElement("li"); li.textContent = h; hints.append(li);
    });
  }

  function idle() {
    show("idle", "Ready", stream ? "Press Check in or Check out." : "Start the camera to begin.");
  }

  function tick() {
    $("clock").textContent = new Date().toLocaleTimeString();
  }
  tick(); setInterval(tick, 1000);

  $("start-camera").addEventListener("click", async function () {
    errorBox.classList.add("d-none");
    try {
      stream = await window.WFACamera.start(video);
      overlay.classList.add("hidden");
      setButtons(true);
      idle();
    } catch (err) {
      errorBox.textContent = window.WFACamera.explain(err);
      errorBox.classList.remove("d-none");
    }
  });

  async function run(mode) {
    if (busy || !stream) return;
    busy = true;
    clearTimeout(resetTimer);
    setButtons(false);
    show("busy", mode === "check_in" ? "Check in" : "Check out", "Preparing…");

    const ch = await window.WFA.postJSON(root.dataset.challengeUrl, { mode: mode });
    if (!ch.data.ok) {
      finish(ch.data);
      return;
    }
    banner.classList.remove("d-none");
    bannerText.textContent = "Get ready: " + ch.data.instruction;
    progress.style.width = "0%";
    await sleep(1500);
    bannerText.textContent = ch.data.instruction;

    const frames = [];
    const count = ch.data.frame_count || FRAME_COUNT;
    const interval = ch.data.frame_interval_ms || FRAME_INTERVAL;
    for (let i = 0; i < count; i++) {
      const f = window.WFACamera.capture(video, 640, 0.8);
      if (f) frames.push(f);
      progress.style.width = Math.round(((i + 1) / count) * 100) + "%";
      await sleep(interval);
    }
    banner.classList.add("d-none");
    show("busy", "Checking…", "Verifying your face. Please wait.");

    const res = await window.WFA.postJSON(root.dataset.verifyUrl, { token: ch.data.token, mode: mode, frames: frames });
    frames.length = 0;
    finish(res.data);
  }

  function finish(data) {
    banner.classList.add("d-none");
    let kind = "error";
    if (data.ok && data.status === "recorded") kind = "success";
    else if (data.ok) kind = "info";
    show(kind, data.title || "Not recorded", data.message, data.hints);
    if (data.status === "kiosk_not_authorised" || data.status === "kiosk_not_authorized") {
      setButtons(false);
      busy = false;
      return;
    }
    busy = false;
    setButtons(!!stream);
    resetTimer = setTimeout(idle, RESET_AFTER_MS);
  }

  buttons.forEach(function (b) {
    b.addEventListener("click", function () { run(b.dataset.mode); });
  });

  window.addEventListener("pagehide", function () { window.WFACamera.stop(stream); });
})();
