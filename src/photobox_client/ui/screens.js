// Draws one screen from a plain state object. All text is set as text, never as HTML, so nothing
// that comes from a server, a box name or a visitor's message can become markup.
(() => {
  "use strict";

  function el(tag, className, ...children) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    for (const child of children) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : String(child));
    }
    return node;
  }

  const screen = (extra, ...children) => el("div", "screen " + (extra || ""), ...children);
  const top = (label) => el("div", "top", el("span", "", "MEET A STRANGER"), el("span", "", label || ""));
  const key = (name, quiet) => el("span", "key" + (quiet ? " quiet" : ""), name);
  const action = (name, de, en, quiet) => el("div", "action", key(name, quiet), el("div", "", de, el("small", "", en)));

  function badge(state) {
    if (!state) return null;
    const pill = el("div", "badge " + (state.kind || ""));
    if (state.kind === "live") {
      const dot = el("span", "dot");
      dot.style.animationDelay = `-${Math.round(performance.now() % 1600)}ms`;  // keeps its rhythm across redraws
      pill.append(dot);
    }
    pill.append(state.de === state.en ? state.de : `${state.de} / ${state.en}`);
    return pill;
  }

  const hero = (text) => [el("h1", "hero", text.de), el("p", "hero-en en", text.en)];

  // A notice is the operator's free text: hard-wrapped lines, "- " list items, and ENTER = / ESC = lines.
  function parseNotice(text) {
    const lines = text.split("\n");
    const blocks = [];
    const keys = [];
    let title = null;
    for (let i = 0; i < lines.length; i++) {
      const raw = lines[i];
      const line = raw.trim();
      const pressed = line.match(/^(ENTER|ESC)\s*=\s*(.+)$/);
      if (pressed) { keys.push({ key: pressed[1], text: pressed[2] }); continue; }
      if (!line) { blocks.push(null); continue; }
      if (title === null && !blocks.length && lines[i + 1] !== undefined && !lines[i + 1].trim()) { title = line; continue; }
      const last = blocks[blocks.length - 1];
      if (line.startsWith("- ")) blocks.push({ item: line.slice(2) });
      else if (last && last.item !== undefined && raw.startsWith(" ")) last.item += " " + line;
      else if (last && last.para !== undefined) last.para += " " + line;
      else blocks.push({ para: line });
    }
    return { title, blocks: blocks.filter(Boolean), keys };
  }

  function drawNotice(notice) {
    const body = el("div", "notice");
    let list = null;
    for (const block of notice.blocks) {
      if (block.item !== undefined) {
        if (!list) { list = el("ul"); body.append(list); }
        list.append(el("li", "", block.item));
      } else {
        list = null;
        body.append(el("p", "", block.para));
      }
    }
    return body;
  }

  const MAX_NOTICE = 40, MIN_NOTICE = 32;

  // Largest size at which the text fits; below the minimum it is paged with the arrow keys instead.
  // Agreeing is possible only once the last page has been on screen: nobody agrees to a page they never saw.
  function fitNotice(body, pages, enter) {
    let size = MAX_NOTICE;
    for (; size >= MIN_NOTICE; size -= 1) {
      body.style.fontSize = size + "px";
      if (body.scrollWidth <= body.clientWidth + 1) break;
    }
    if (size < MIN_NOTICE) body.style.fontSize = MIN_NOTICE + "px";
    const step = body.clientWidth + 72;
    const count = Math.max(1, Math.ceil((body.scrollWidth + 72) / step));
    let page = 0;
    let seenAll = count === 1;
    const show = () => {
      body.scrollLeft = page * step;
      seenAll = seenAll || page === count - 1;
      if (enter) enter.classList.toggle("locked", !seenAll);
      pages.textContent = count === 1 ? "" : `Seite ${page + 1} von ${count}` + (seenAll ? "   ↑ ↓" : " · weiter mit ↓");
      pages.classList.remove("nudge");
    };
    show();
    return {
      move: (delta) => { page = Math.min(count - 1, Math.max(0, page + delta)); show(); },
      ready: () => seenAll,
      nudge: () => { pages.classList.remove("nudge"); void pages.offsetWidth; pages.classList.add("nudge"); },
    };
  }

  function keyActions(keys) {
    const labels = {
      ENTER: (t) => t.split(" / "),
      ESC: (t) => t.split(" / "),
    };
    return el("div", "actions", ...keys.map(({ key: name, text }) => {
      const [de, en] = labels[name](text);
      return action(name, de, en || "", name === "ESC");
    }));
  }

  function noticeScreen(state, extra) {
    const notice = parseNotice(state.text);
    const body = drawNotice(notice);
    const pages = el("span", "pages");
    const actions = keyActions(notice.keys);
    const enter = actions.children[notice.keys.findIndex((k) => k.key === "ENTER")] || null;
    const node = screen("",
      top(state.label),
      notice.title && el("h2", "notice-title", notice.title),
      extra || null,
      body,
      el("div", "bottom", actions, pages));
    if (enter) enter.classList.add("locked");  // until the text has been measured
    node.after_mount = () => { node.paging = fitNotice(body, pages, enter); };
    node.is_notice = true;
    return node;
  }

  // Only a display: the kiosk itself decides when the time is up.
  function addTimeLeft(node, seconds) {
    if (!seconds) return;
    const left = el("span", "time-left");
    const ends = performance.now() + seconds * 1000;
    let shown = false;
    const tick = () => {
      left.textContent = `Noch ${Math.max(0, Math.ceil((ends - performance.now()) / 1000))} s`;
      shown = shown || left.isConnected;
      if (!shown || left.isConnected) setTimeout(tick, 250);  // stops once the screen has been replaced
    };
    tick();
    node.querySelector(".bottom").append(left);
  }

  function encounter(state) {
    const e = state.encounter;
    if (!e) return null;
    const side = el("div", "side");
    if (e.kind === "searching") {
      side.append(el("h3", "", e.de), el("p", "en", e.en));
      if (e.seconds !== undefined) side.append(el("div", "wait", e.seconds, el("small", "", "s")));
    } else if (e.kind === "live") {
      side.append(badge({ kind: "live", de: "LIVE", en: "LIVE" }), el("h3", "", e.de), el("p", "en", e.en));
      if (e.talk) side.append(el("div", "key-row", action("ENTER", `Sprechen · bis zu ${e.talk} s`, `Talk · up to ${e.talk} s`)));
      if (e.message) side.append(el("div", "bubble", el("b", "", e.message.from), el("span", "", e.message.text)));
      if (e.status) side.append(el("p", "note", e.status.de, el("br"), e.status.en));
    } else {
      side.append(el("h3", "", e.de), el("p", "en", e.en));
    }
    return side;
  }

  const views = {
    start: (s) => screen(s.tiles && s.tiles.length ? "compact" : "",
      top(s.label),
      el("div", "fill"),
      badge(s.badge),
      ...hero(s.hero),
      el("p", "lead", s.support.de), el("p", "lead-en en", s.support.en),
      el("div", "fill"),
      s.tiles && s.tiles.length ? el("div", "tiles", ...s.tiles.map((t) =>
        el("div", "tile " + t.kind, el("b", "", t.label), el("span", "", t.status)))) : null,
      el("div", "actions", action("ENTER", "Zum Starten drücken", "Press to start")),
      s.camera_note && el("div", "camera-note", s.camera_note.de, el("br"), el("span", "en", s.camera_note.en))),

    consent: (s) => {
      const node = noticeScreen(s, s.until && el("div", "until", s.until, el("small", "", "Danach wird dein Foto gelöscht / Your photo is deleted after that")));
      addTimeLeft(node, s.seconds);
      return node;
    },

    // White from here until the photo has been taken: the screen is extra light for the camera.
    countdown: (s) => screen("countdown light", el("div", "number", s.number),
      el("p", "lead", "Schau in die Kamera"), el("p", "lead-en en", "Look at the camera")),

    flash: () => screen("light"),

    making: (s) => {
      const left = el("div", "");
      left.append(
        el("h1", "hero", "Dein Fotostreifen entsteht"), el("p", "hero-en en", "Your photo strip is being made"),
        el("div", "clock", s.clock));
      if (s.frames) {
        left.append(
          el("div", "progress", ...Array.from({ length: s.frames.total }, (_, i) => el("i", i < s.frames.done ? "done" : ""))),
          el("p", "frame-text", `Bild ${s.frames.done} von ${s.frames.total} / Picture ${s.frames.done} of ${s.frames.total}`));
      } else {
        left.append(el("p", "frame-text", "In der Warteschlange / Queued"));
      }
      const side = encounter(s);
      return screen("", top(s.label), el("div", "making", left, side || el("div", "")));
    },

    live_moment: (s) => screen("center", top(s.label),
      el("div", "live-word", "LIVE"),
      el("h1", "hero", s.de), el("p", "hero-en en", s.en)),

    listening: (s) => screen("listen",
      el("p", "lead", "Ich höre zu"), el("p", "lead-en en", "Listening"),
      el("div", "seconds", s.seconds),
      el("div", "meter", (() => { const bar = el("i"); bar.style.width = (100 * s.seconds / s.limit) + "%"; return bar; })()),
      action("ENTER", "Fertig", "Done")),

    converting: () => screen("listen", el("h1", "hero", "Deine Stimme wird zu Text"), el("p", "hero-en en", "Turning your voice into text")),

    strip: (s) => {
      const image = el("img", "strip");
      image.src = s.image;
      image.alt = "";
      return screen("", el("div", "result", image, s.code ? el("div", "",
        el("p", "code-label", "Dein Widerrufscode"), el("p", "lead-en en", "Your withdrawal code"),
        el("p", "code", s.code),
        el("p", "info", "Notiere ihn, um deine Bilder löschen zu lassen."),
        el("p", "info", "Write it down to have your images deleted.")) : el("div")));
    },

    done: (s) => {
      const node = screen("center", top(s.label),
        el("div", "done-word", "Fertig!"),
        s.code && el("p", "code-label", "Dein Widerrufscode"), s.code && el("p", "lead-en en", "Your withdrawal code"),
        s.code && el("p", "code", s.code),
        s.code && el("p", "info", "Notiere ihn, um deine Bilder löschen zu lassen. / Write it down to have your images deleted."),
        !s.code && el("p", "info", "Zum Löschen deiner Bilder wende dich an die Betreuung. / To have your images deleted, ask the staff."),
        s.pool && el("p", "info", `Dein Foto bleibt bis ${s.pool} im Pool. Der Code löscht es auch dort. / Your photo stays in the pool until ${s.pool}. The code deletes it there too.`),
        el("div", "bottom", el("div", "actions", action("ENTER", "Mehr Zeit", "More time")), null));
      addTimeLeft(node, s.seconds);
      return node;
    },

    message: (s) => screen("center", top(s.label),
      el("h1", "hero", s.de), el("p", "hero-en en", s.en),
      s.key && el("div", "bottom", el("div", "actions", action("ENTER", "Weiter", "Continue")), null)),

    menu: (s) => screen("", el("div", "menu", s.text)),
  };

  let current = null;
  let currentName = null;

  function render(stage, state) {
    const node = views[state.screen](state);
    if (state.test_mode) node.append(el("div", "test-mode", "TESTMODUS · KEINE KAMERA"));
    if (state.screen === currentName) node.classList.add("instant");  // a redraw of the same screen must not flicker
    currentName = state.screen;
    stage.replaceChildren(node);
    current = node;
    if (node.after_mount) document.fonts.ready.then(node.after_mount);
  }

  // The canvas in ui.css, drawn to fit: at half size on the box's 1920 x 1080 screen.
  const CANVAS_WIDTH = 3840, CANVAS_HEIGHT = 2160;

  function fit(stage) {
    const scale = Math.min(window.innerWidth / CANVAS_WIDTH, window.innerHeight / CANVAS_HEIGHT);
    stage.style.transform = `translate(${(window.innerWidth - CANVAS_WIDTH * scale) / 2}px, ${(window.innerHeight - CANVAS_HEIGHT * scale) / 2}px) scale(${scale})`;
  }

  // False on a notice until its last page has been shown, or while it has not been measured yet.
  function canConfirm() {
    if (!current || !current.is_notice) return true;
    if (current.paging && current.paging.ready()) return true;
    if (current.paging) current.paging.nudge();
    return false;
  }

  window.PhotoboxScreens = { render, fit, canConfirm, page: (delta) => current && current.paging && current.paging.move(delta) };
})();
