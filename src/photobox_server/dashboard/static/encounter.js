// Simulated encounter (ADR-025): shows each simulated box's screen. Text is set with textContent only.
(() => {
  "use strict";
  const root = document.getElementById("encounter");
  if (!root) return;

  function line(tag, text, className) {
    const element = document.createElement(tag);
    element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function card(box) {
    const article = document.createElement("article");
    article.className = "tile";
    article.append(line("b", (box.label || box.box_id) + " "));
    article.firstChild.append(line("span", "SIMULATED", "simtag"));
    if (box.pool_text) {
      article.append(line("p", box.pool_text));
    } else if (box.open) {
      article.append(line("p", "CONNECTED TO " + box.partner + " · LIVE", "state"));
      article.append(line("p", "Your shared photo is being created… " + box.seconds_left + " s to talk"));
    } else if (box.closed) {
      article.append(line("p", box.closed));
    } else if (!box.member) {
      article.append(line("p", "Not in this encounter. Sees nothing."));
    }
    box.received.forEach((message) => article.append(line("p", message.from + ": " + message.text, "bubble")));
    if (box.status) article.append(line("p", box.status, "state"));
    return article;
  }

  async function refresh() {
    try {
      const response = await fetch("/presence/simulator/encounter");
      if (response.ok) root.replaceChildren(...(await response.json()).cards.map(card));
    } finally {
      setTimeout(refresh, 1000);
    }
  }
  refresh();
})();
