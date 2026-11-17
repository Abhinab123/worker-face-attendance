/* Shared helpers: CSRF-protected JSON requests. */
(function () {
  "use strict";
  const meta = document.querySelector('meta[name="csrf-token"]');

  async function postJSON(url, body) {
    let response;
    try {
      response = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
          "X-CSRFToken": meta ? meta.content : "",
        },
        body: JSON.stringify(body),
      });
    } catch (err) {
      return { httpStatus: 0, data: { ok: false, status: "network_error", title: "Connection problem",
        message: "Could not reach the server. Check the network connection and try again." } };
    }
    let data;
    try {
      data = await response.json();
    } catch (err) {
      data = { ok: false, status: "bad_response", title: "Server error",
        message: response.status === 413 ? "The images were too large." : "Unexpected server response (HTTP " + response.status + ")." };
    }
    return { httpStatus: response.status, data: data };
  }

  window.WFA = { postJSON: postJSON };
})();
