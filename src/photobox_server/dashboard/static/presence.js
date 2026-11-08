// Presence preview (ADR-023, milestone S2): applies each snapshot from the server-sent event stream.
// Every value is set with textContent, never as HTML, so a box name can never become markup.
(() => {
  "use strict";
  const root = document.getElementById("presence");
  if (!root || !window.EventSource) return;
  const lang = root.dataset.lang;
  const byId = (id) => document.getElementById(id);
  const icons = { live: "●", recent: "◔", earlier: "▣", online: "○", offline: "×" };
  const badgeKinds = { live_match: "live", partner_waiting: "live", remote_active: "live", recent_photo: "recent", earlier_portrait: "earlier", online_only: "online" };
  const unnamed = lang === "de" ? "Fotobox" : "Photobox";

  function tileKind(item) {
    if (item.device_state === "offline") return "offline";
    return { session_active: "live", recent_photo: "recent" }[item.activity_state] || "online";
  }

  function tile(item) {
    const kind = tileKind(item);
    const li = document.createElement("li");
    li.className = "tile " + kind;
    const name = document.createElement("b");
    name.textContent = item.label || unnamed;
    if (item.simulated) {
      const tag = document.createElement("span");
      tag.className = "simtag";
      tag.textContent = "SIMULATED";
      name.appendChild(tag);
    }
    const state = document.createElement("span");
    state.className = "state";
    state.textContent = icons[kind] + " " + (item.status[lang] || "");
    li.append(name, state);
    return li;
  }

  function render(snapshot) {
    const message = snapshot.message;
    const text = message.texts[lang];
    byId("hero").textContent = text.hero;
    byId("support").textContent = text.support;
    const badge = byId("badge");
    const kind = badgeKinds[message.key] || "";
    badge.className = "badge " + kind;
    badge.hidden = !message.badge;
    badge.textContent = "";
    if (kind === "live") {
      const dot = document.createElement("span");
      dot.className = "pulse";
      dot.setAttribute("aria-hidden", "true");
      dot.textContent = "● ";
      badge.appendChild(dot);
    }
    badge.append(message.badge ? message.badge[lang] : "");
    byId("self").textContent = (snapshot.self.label || snapshot.self.box_id) + (snapshot.self.state === "unavailable" ? " · NOT READY" : "");
    const tiles = byId("tiles");
    tiles.replaceChildren(...snapshot.locations.map(tile));
    if (snapshot.more_locations) {
      const more = document.createElement("li");
      more.className = "tile";
      more.textContent = "+" + snapshot.more_locations;
      tiles.appendChild(more);
    }
    byId("sim").hidden = !snapshot.locations.some((item) => item.simulated);
  }

  const source = new EventSource(root.dataset.stream);
  source.addEventListener("snapshot", (event) => {
    render(JSON.parse(event.data));
    byId("conn").textContent = "Live · last change " + new Date().toLocaleTimeString();
  });
  source.onerror = () => {
    // CLOSED means the browser gave up, for example because the login expired and a page came back instead.
    byId("conn").textContent = source.readyState === EventSource.CLOSED
      ? "Updates stopped. Reload the page; you may need to log in again."
      : "Connection lost, reconnecting…";
  };
})();
