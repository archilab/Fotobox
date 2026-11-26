// The live page: draws each state from the kiosk and sends key presses back with the screen they belong to.
(() => {
  "use strict";
  const stage = document.getElementById("stage");
  const params = new URLSearchParams(location.search);
  const token = params.get("t") || "";
  history.replaceState(null, "", "/");  // keeps the token out of anything showing the address

  let screenId = null;
  const fit = () => window.PhotoboxScreens.fit(stage);
  window.addEventListener("resize", fit);
  fit();

  const events = new EventSource("/events?t=" + encodeURIComponent(token));
  events.addEventListener("state", (event) => {
    const state = JSON.parse(event.data);
    screenId = state.id;
    window.PhotoboxScreens.render(stage, state);
    window.PhotoboxVideo.follow(state, stage.firstElementChild, sendSignal);  // the hidden video link (ADR-045)
  });

  function send(key) {
    if (screenId === null) return;
    fetch("/key", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Photobox-Token": token },
      body: JSON.stringify({ id: screenId, key }),
    }).catch(() => {});  // the kiosk carries on; a lost key press is simply pressed again
  }

  // The video link's setup messages, both ways through the booth (ADR-045, V2).
  function sendSignal(kind, data) {
    fetch("/signal", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Photobox-Token": token },
      body: JSON.stringify({ kind, data }),
    }).catch(() => {});
  }
  events.addEventListener("signal", (event) => window.PhotoboxVideo.receive(JSON.parse(event.data)));

  // The push button (ADR-036): only for the screen showing when it was pressed, and under the same rule as
  // ENTER, so a notice must be read to its last page. Until then the button turns the page.
  events.addEventListener("button", (event) => {
    if (JSON.parse(event.data).id !== screenId) return;
    if (window.PhotoboxScreens.canConfirm()) return send("enter");
    window.PhotoboxScreens.page(1);
  });

  // A frozen page stops sending this, and the kiosk then restarts the browser.
  setInterval(() => {
    fetch("/alive", { method: "POST", headers: { "X-Photobox-Token": token } }).catch(() => {});
  }, 5000);

  window.addEventListener("keydown", (event) => {
    event.preventDefault();  // no browser shortcut may leave the kiosk screen
    if (event.key === "ArrowDown") return window.PhotoboxScreens.page(1);
    if (event.key === "ArrowUp") return window.PhotoboxScreens.page(-1);
    if (event.ctrlKey && event.key.toLowerCase() === "s") return send("setup");
    if (event.ctrlKey && event.key.toLowerCase() === "v") return send("video");  // the button's 5 s hold, for testing (ADR-045)
    if (event.repeat && event.key !== "Backspace") return;  // a held key is one press
    if (event.ctrlKey || event.metaKey) return;
    if (event.key === "Enter") return window.PhotoboxScreens.canConfirm() && send("enter");
    if (event.key === "Escape") return send("esc");
    if (event.key === "Backspace") return send("backspace");
    if (event.key.length === 1) return send(event.key);
  });
})();
