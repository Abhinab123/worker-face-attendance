/* Browser camera access. Frames are captured un-mirrored (the preview is mirrored by CSS only). */
(function () {
  "use strict";

  function explain(err) {
    if (!window.isSecureContext) {
      return "The browser blocks the camera on insecure pages. Open the app at http://localhost:5000 " +
        "(or http://127.0.0.1:5000) on this computer, or serve it over HTTPS.";
    }
    const name = err && err.name;
    switch (name) {
      case "NotAllowedError":
      case "PermissionDeniedError":
        return "Camera permission was denied. Click the camera icon in the address bar (or site settings), " +
          "choose Allow, then press Start camera again. On Windows also check Settings > Privacy > Camera; " +
          "on macOS check System Settings > Privacy & Security > Camera.";
      case "NotFoundError":
      case "DevicesNotFoundError":
        return "No camera was found. Connect a webcam and press Start camera again.";
      case "NotReadableError":
      case "TrackStartError":
        return "The camera could not be started. It may be in use by another application (video call, " +
          "another browser tab) or disabled by the operating system. Close other apps and try again.";
      case "OverconstrainedError":
        return "The camera does not support the requested resolution.";
      case "SecurityError":
        return "Camera access is blocked by browser security settings for this page.";
      case "AbortError":
        return "Camera start was interrupted. Try again.";
      default:
        return "Could not start the camera" + (name ? " (" + name + ")" : "") + ". Reload the page and try again.";
    }
  }

  async function start(video) {
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      const e = new Error("insecure"); e.name = "SecurityError"; throw e;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" },
      });
    } catch (err) {
      if (err && err.name === "OverconstrainedError") {
        stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: true });
      } else {
        throw err;
      }
    }
    video.srcObject = stream;
    await video.play();
    return stream;
  }

  function stop(stream) {
    if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
  }

  function capture(video, maxWidth, quality) {
    const w = video.videoWidth, h = video.videoHeight;
    if (!w || !h) return null;
    const scale = Math.min(1, (maxWidth || 640) / w);
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(w * scale);
    canvas.height = Math.round(h * scale);
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", quality || 0.85);
  }

  window.WFACamera = { start: start, stop: stop, capture: capture, explain: explain };
})();
