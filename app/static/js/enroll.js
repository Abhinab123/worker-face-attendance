/* Enrollment page: capture several samples in the browser, then submit them together. */
(function () {
  "use strict";
  const root = document.getElementById("enroll-app");
  if (!root) return;

  const $ = function (id) { return document.getElementById(id); };
  const video = $("video"), overlay = $("camera-overlay"), errorBox = $("camera-error");
  const captureBtn = $("capture"), clearBtn = $("clear"), submitBtn = $("submit");
  const thumbs = $("thumbs"), countLabel = $("sample-count"), resultBox = $("result");
  const consent = $("consent");
  const min = parseInt(root.dataset.min, 10), max = parseInt(root.dataset.max, 10);
  const consentRecorded = root.dataset.consentRecorded === "true";
  let samples = [];   // in-memory only; cleared after submit
  let stream = null;

  function setError(msg) {
    errorBox.textContent = msg || "";
    errorBox.classList.toggle("d-none", !msg);
  }

  function consentOk() { return consentRecorded || (consent && consent.checked); }

  function refresh() {
    countLabel.textContent = samples.length + " sample" + (samples.length === 1 ? "" : "s") +
      " (need " + min + "–" + max + ")";
    captureBtn.disabled = !stream || samples.length >= max;
    clearBtn.disabled = samples.length === 0;
    submitBtn.disabled = samples.length < min || !consentOk();
    thumbs.replaceChildren();
    samples.forEach(function (s, i) {
      const wrap = document.createElement("div");
      wrap.className = "thumb" + (s.status === "bad" ? " bad" : s.status === "good" ? " good" : "");
      const img = document.createElement("img");
      img.src = s.data; img.alt = "Sample " + (i + 1);
      const rm = document.createElement("button");
      rm.type = "button"; rm.className = "btn btn-sm btn-light"; rm.textContent = "×";
      rm.setAttribute("aria-label", "Remove sample " + (i + 1));
      rm.addEventListener("click", function () { samples.splice(i, 1); refresh(); });
      wrap.append(img, rm);
      if (s.note) {
        const note = document.createElement("div");
        note.className = "thumb-note"; note.textContent = s.note;
        wrap.append(note);
      }
      thumbs.append(wrap);
    });
  }

  $("start-camera").addEventListener("click", async function () {
    setError("");
    try {
      stream = await window.WFACamera.start(video);
      overlay.classList.add("hidden");
    } catch (err) {
      setError(window.WFACamera.explain(err));
    }
    refresh();
  });

  captureBtn.addEventListener("click", function () {
    const data = window.WFACamera.capture(video, 960, 0.9);
    if (!data) { setError("The camera is not ready yet. Wait a moment and try again."); return; }
    samples.push({ data: data, status: null, note: "" });
    refresh();
  });

  clearBtn.addEventListener("click", function () { samples = []; resultBox.replaceChildren(); refresh(); });
  if (consent) consent.addEventListener("change", refresh);

  function showResult(kind, text, extra) {
    const div = document.createElement("div");
    div.className = "alert alert-" + kind;
    div.setAttribute("role", "alert");
    div.textContent = text;
    if (extra) div.append(extra);
    resultBox.replaceChildren(div);
  }

  submitBtn.addEventListener("click", async function () {
    submitBtn.disabled = true;
    $("submit-spinner").classList.remove("d-none");
    $("submit-label").textContent = "Processing samples…";
    const res = await window.WFA.postJSON(root.dataset.submitUrl, {
      images: samples.map(function (s) { return s.data; }),
      consent: consentOk(),
    });
    $("submit-spinner").classList.add("d-none");
    $("submit-label").textContent = "Save enrollment";
    const data = res.data || {};
    (data.samples || []).forEach(function (fb) {
      if (samples[fb.index]) {
        samples[fb.index].status = fb.accepted ? "good" : "bad";
        samples[fb.index].note = fb.accepted ? "" : fb.message;
      }
    });
    if (data.status === "enrolled" || data.status === "pending_review") {
      const link = document.createElement("a");
      link.href = root.dataset.detailUrl; link.className = "alert-link d-block mt-2";
      link.textContent = "Go to worker profile";
      showResult(data.status === "enrolled" ? "success" : "warning", data.message, link);
      samples = [];
    } else {
      showResult("danger", data.message || "Enrollment failed.");
    }
    refresh();
  });

  window.addEventListener("pagehide", function () { window.WFACamera.stop(stream); });
  refresh();
})();
