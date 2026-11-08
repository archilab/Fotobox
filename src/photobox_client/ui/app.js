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
  });

  function send(key) {
    if (screenId === null) return;
    fetch("/key", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Photobox-Token": token },
      body: JSON.stringify({ id: screenId, key }),
    }).catch(() => {});  // the kiosk carries on; a lost key press is simply pressed again
  }

  window.addEventListener("keydown", (event) => {
    event.preventDefault();  // no browser shortcut may leave the kiosk screen
    if (event.key === "ArrowDown") return window.PhotoboxScreens.page(1);
    if (event.key === "ArrowUp") return window.PhotoboxScreens.page(-1);
    if (event.ctrlKey && event.key.toLowerCase() === "s") return send("setup");
    if (event.repeat && event.key !== "Backspace") return;  // a held key is one press
    if (event.ctrlKey || event.metaKey) return;
    if (event.key === "Enter") return window.PhotoboxScreens.canConfirm() && send("enter");
    if (event.key === "Escape") return send("esc");
    if (event.key === "Backspace") return send("backspace");
    if (event.key.length === 1) return send(event.key);
  });
})();
