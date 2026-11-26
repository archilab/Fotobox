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


  // MIDAP's continuous line (ADR-039). Each place owns one line, drawn from its name so that it always
  // looks the same; the encounter is two lines meeting. White is this booth, grey the other place.
  const SVG = "http://www.w3.org/2000/svg";
  function svg(tag, attrs, ...children) {
    const node = document.createElementNS(SVG, tag);
    for (const [name, value] of Object.entries(attrs || {})) node.setAttribute(name, value);
    for (const child of children) if (child) node.append(child);
    return node;
  }

  // The mark from midap.space, used with the project's permission.
  const MIDAP_MARK = "M19.6743 16.2367C19.6743 13.2175 16.3352 4.46249 9.73811 4.93415C3.15111 5.40479 -1.43488 14.5798 1.64404 16.4047C5.58328 18.7387 9.49317 -0.274317 14.9668 0.701384C20.4394 1.6781 18.1824 14.3278 13.1693 16.4158C8.15715 18.5039 3.53067 6.94223 2.6582 1.43316";
  const midap = () => el("div", "midap",
    svg("svg", { class: "midap-mark", viewBox: "-1 -1 23 20", "aria-hidden": "true" }, svg("path", { d: MIDAP_MARK })),
    el("b", "", "MIDAP"));

  // The same name always gives the same numbers.
  function seeded(text) {
    let h = 2166136261;
    for (const c of text || "booth") { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); }
    return () => { h ^= h << 13; h ^= h >>> 17; h ^= h << 5; return ((h >>> 0) % 10007) / 10007; };
  }

  // A smooth curve through the points (Catmull-Rom, written as cubic Béziers).
  function smooth(points) {
    let d = `M${points[0][0].toFixed(1)} ${points[0][1].toFixed(1)}`;
    for (let i = 0; i < points.length - 1; i++) {
      const [p0, p1, p2, p3] = [points[Math.max(0, i - 1)], points[i], points[i + 1], points[Math.min(points.length - 1, i + 2)]];
      const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
      const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
      d += ` C${c1[0].toFixed(1)} ${c1[1].toFixed(1)} ${c2[0].toFixed(1)} ${c2[1].toFixed(1)} ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`;
    }
    return d;
  }

  // A line from (x0, y) to (x1, y) that loops back on itself, like MIDAP's mark: a few loops of
  // different sizes and tilts at uneven distances, so it reads as a drawn line, not as handwriting.
  // MIDAP's mark as curve segments (the path above): one loop gesture, drawn by hand. Each place's line
  // is made of copies of it, so every curve in the line is the mark's own; only size, slant and height
  // vary per place. The mark runs from right to left; it is mirrored for a line that runs the other way.
  const GESTURE = [
    [[19.6743, 16.2367], [19.6743, 13.2175], [16.3352, 4.46249], [9.73811, 4.93415]],
    [[9.73811, 4.93415], [3.15111, 5.40479], [-1.43488, 14.5798], [1.64404, 16.4047]],
    [[1.64404, 16.4047], [5.58328, 18.7387], [9.49317, -0.274317], [14.9668, 0.701384]],
    [[14.9668, 0.701384], [20.4394, 1.6781], [18.1824, 14.3278], [13.1693, 16.4158]],
    [[13.1693, 16.4158], [8.15715, 18.5039], [3.53067, 6.94223], [2.6582, 1.43316]],
  ];

  function gesture(dir, cx, cy, size, angle) {
    const k = size / 18, cos = Math.cos(angle), sin = Math.sin(angle);
    const place = ([x, y]) => {
      const lx = (x - 10) * -dir * k, ly = (y - 9) * k;
      return [cx + lx * cos - ly * sin, cy + lx * sin + ly * cos];
    };
    return GESTURE.map((segment) => segment.map(place));
  }

  const unit = ([x, y]) => { const l = Math.hypot(x, y) || 1; return [x / l, y / l]; };
  const fmt = ([x, y]) => `${x.toFixed(1)} ${y.toFixed(1)}`;

  // A smooth join from a, leaving along ta, to b, arriving along tb: it continues both strokes' directions.
  function join(a, ta, b, tb) {
    const k = Math.hypot(b[0] - a[0], b[1] - a[1]) * 0.3;
    return ` C${fmt([a[0] + ta[0] * k, a[1] + ta[1] * k])} ${fmt([b[0] - tb[0] * k, b[1] - tb[1] * k])} ${fmt(b)}`;
  }

  // A place's line from (x0, y) to (x1, y), looping `loops` times in MIDAP's gesture. `lead`: a straight
  // stretch at the start, where the place's name sits above the line. It starts and ends level.
  function placeLine(place, x0, x1, y, size, loops, lead = 0) {
    const rnd = seeded(place);
    const dir = Math.sign(x1 - x0) || 1;
    const from = x0 + dir * lead;
    let d = `M${fmt([x0, y])}` + (lead ? ` L${fmt([from, y])}` : "");
    let at = [from, y], heading = [dir, 0];
    for (let i = 0; i < loops; i++) {
      const cx = from + (x1 - from) * (i + 0.5 + (rnd() - 0.5) * 0.3) / loops;
      const g = gesture(dir, cx, y + (rnd() - 0.5) * size * 0.35, size * (0.8 + rnd() * 0.4), (rnd() - 0.5) * 0.35);
      const entry = g[0][0], last = g[g.length - 1];
      d += join(at, heading, entry, unit([g[0][1][0] - entry[0], g[0][1][1] - entry[1]]));
      for (const [, c1, c2, end] of g) d += ` C${fmt(c1)} ${fmt(c2)} ${fmt(end)}`;
      at = last[3];
      heading = unit([last[3][0] - last[2][0], last[3][1] - last[2][1]]);
    }
    if (Math.abs(x1 - at[0]) > size * 0.6 && Math.sign(x1 - at[0]) === dir) d += join(at, heading, [x1, y], [dir, 0]);
    return d;
  }

  // Two places' lines woven: they start apart and cross `crossings` times, at evenly spaced points,
  // each with its own uneven swing and a small loop of its own at some turns.
  function weave(ownName, otherName, x0, x1, y, amplitude, crossings) {
    return [[ownName, 1], [otherName, -1]].map(([name, side]) => {
      const rnd = seeded(name);
      const swing = Array.from({ length: crossings + 1 }, () => 0.7 + rnd() * 0.45);
      const points = [];
      for (let i = 0; i <= crossings * 8; i++) {
        const t = i / (crossings * 8);
        const turn = Math.round(t * crossings);
        const a = amplitude * swing[Math.min(turn, crossings)];
        points.push([x0 + (x1 - x0) * t, y + side * a * Math.cos(Math.PI * crossings * t)]);
        // At some turns, away from the other line, a small loop of this place's own.
        if (i % 8 === 0 && i > 0 && i < crossings * 8 && (turn % 2 === 0) === (side === 1)) {
          const [cx, cy] = points[points.length - 1];
          const r = amplitude * 0.28;
          const outward = Math.sign(cy - y) || side;
          for (let k = 1; k <= 6; k++) {
            const angle = Math.PI / 2 * outward + k * Math.PI * 2 / 6;
            points.push([cx + Math.sin(angle) * r * 0.9, cy + outward * r - Math.cos(angle) * r * outward]);
          }
        }
      }
      return smooth(points);
    });
  }

  // A line drawn in from `from` to `to` of its length when it appears; `key` lets a redraw continue.
  const stroke = (d, cls, draw) => svg("path", { d, class: cls, "data-draw": JSON.stringify(draw) });
  const band = (cls, width, height, ...paths) => svg("svg", { class: "lines " + cls, viewBox: `0 0 ${width} ${height}`, "aria-hidden": "true" }, ...paths);

  const drawn = new Map();  // how far each keyed line was drawn, so a redraw of the same screen continues
  const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Where our line crosses the other place's, a short stroke in the ground colour under ours leaves a gap
  // in theirs: our line passes over. Found on the drawn paths, so only real crossings get one.
  function markCrossings(band) {
    const sample = (path) => {
      const length = path.getTotalLength();
      return Array.from({ length: 201 }, (_, i) => path.getPointAtLength(length * i / 200));
    };
    for (const ours of band.querySelectorAll("path.ours")) {
      const a = sample(ours);
      for (const theirs of band.querySelectorAll("path.theirs")) {
        const b = sample(theirs);
        for (let i = 0; i < 200; i++) {
          for (let j = 0; j < 200; j++) {
            const [p, p2, q, q2] = [a[i], a[i + 1], b[j], b[j + 1]];
            const d = (p2.x - p.x) * (q2.y - q.y) - (p2.y - p.y) * (q2.x - q.x);
            if (!d) continue;
            const t = ((q.x - p.x) * (q2.y - q.y) - (q.y - p.y) * (q2.x - q.x)) / d;
            const u = ((q.x - p.x) * (p2.y - p.y) - (q.y - p.y) * (p2.x - p.x)) / d;
            if (t < 0 || t > 1 || u < 0 || u > 1) continue;
            const x = p.x + t * (p2.x - p.x), y = p.y + t * (p2.y - p.y);
            if (band.lastGap && Math.hypot(band.lastGap[0] - x, band.lastGap[1] - y) < 60) continue;
            band.lastGap = [x, y];
            const len = Math.hypot(p2.x - p.x, p2.y - p.y) || 1;
            const [ux, uy] = [(p2.x - p.x) / len * 24, (p2.y - p.y) / len * 24];
            band.insertBefore(svg("line", { class: "gap", x1: x - ux, y1: y - uy, x2: x + ux, y2: y + uy }), ours);
          }
        }
      }
    }
  }

  function drawLines(node, redraw) {
    for (const band of node.querySelectorAll("svg.lines")) markCrossings(band);
    for (const path of node.querySelectorAll("path[data-draw]")) {
      const spec = JSON.parse(path.dataset.draw);
      const length = path.getTotalLength();
      // `toX`: draw up to where the line first reaches that x, so crossings are counted exactly.
      if (spec.toX !== undefined) {
        let reach = length;
        for (let i = 0; i <= 240; i++) {
          if (path.getPointAtLength(length * i / 240).x >= spec.toX) { reach = length * i / 240; break; }
        }
        spec.to = spec.toX <= 0 ? 0 : reach / length;
      }
      const offset = (part) => length * (1 - part);
      path.style.strokeDasharray = `${length} ${length}`;
      const before = redraw && spec.key && drawn.has(spec.key) ? drawn.get(spec.key) : spec.from || 0;
      if (spec.key) drawn.set(spec.key, spec.to);
      if (reducedMotion() || before === spec.to) {
        path.style.strokeDashoffset = offset(spec.to);
        continue;
      }
      path.animate([{ strokeDashoffset: offset(before) }, { strokeDashoffset: offset(spec.to) }],
        { duration: (spec.seconds || 2) * 1000, delay: (spec.delay || 0) * 1000, easing: spec.linear ? "linear" : "cubic-bezier(.16, 1, .3, 1)", fill: "both" });
    }
  }

  const W = 2336;  // the canvas inside its margins

  // Start screen: our line, and one grey line for each place online, in its own lane, with its name
  // above the straight start. A place where something is happening reaches further in.
  function startLines(s) {
    const own = (s.label || "booth").toLowerCase();
    const places = (s.tiles || []).filter((t) => t.kind !== "offline").slice(0, 3);
    const paths = [stroke(placeLine(own, 0, places.length ? 1180 : 1700, 210, 230, places.length ? 2 : 3), "ours", { from: 0, to: 1, seconds: 4.5, key: "start-ours" })];
    const lane = 420 / Math.max(1, places.length);
    places.forEach((place, i) => {
      const y = lane * i + lane * 0.62;
      const reach = { live: 1, nearby: 0.8, recent: 0.62 }[place.kind] || 0.45;
      paths.push(stroke(placeLine(place.label.toLowerCase(), W, 1320, y, Math.min(170, lane * 0.6), 2, 420), "theirs",
        { from: 0, to: reach, seconds: 3.5, delay: 1 + i * 0.4, key: "start-" + place.label + place.kind }));
      paths.push(svg("text", { x: W, y: y - 26, "text-anchor": "end" }, place.label));
    });
    return band("start-lines", W, 420, ...paths);
  }

  // Invitation: the other place's line reaches across to ours while the seconds run.
  let inviteSeconds = null;
  function inviteLines(s, redraw) {
    if (!redraw || !inviteSeconds) inviteSeconds = s.seconds;
    const reached = Math.max(0, 1 - s.seconds / inviteSeconds);
    return band("invite-lines", W, 300,
      stroke(placeLine((s.place || "other").toLowerCase(), W, 1210, 160, 170, 2, 380), "theirs", { from: reached, to: 1, seconds: s.seconds, linear: true }),
      stroke(placeLine((s.label || "booth").toLowerCase(), 0, 1130, 160, 170, 2), "ours", { from: 0, to: 1, seconds: 1.4, key: "invite-ours" }),
      svg("text", { x: W, y: 120, "text-anchor": "end" }, s.place || ""));
  }

  const side_width = (s) => (s.encounter && ["searching", "live"].includes(s.encounter.kind) ? 1080 : 2336);

  // While the strip is made: one crossing per finished picture with a live partner, one loop alone.
  function makingLines(s, width) {
    const total = s.frames ? s.frames.total : 4;
    const done = s.frames ? s.frames.done / total : 0;
    if (s.encounter && s.encounter.kind === "live") {
      const [ours, theirs] = weave((s.label || "booth").toLowerCase(), (s.encounter.place || "other").toLowerCase(), 0, width, 120, 80, total);
      const toX = width * done;
      return band("making-lines", width, 240,
        stroke(theirs, "theirs", { toX, seconds: 1.6, key: "making-theirs" }),
        stroke(ours, "ours", { toX, seconds: 1.6, key: "making-ours" }));
    }
    return band("making-lines", width, 220,
      // Each loop needs room: about 1.8 times its size between loops, so joins stay gentle.
      stroke(placeLine((s.label || "booth").toLowerCase(), 0, width, 120, Math.min(150, width / (total * 1.8)), total), "ours", { to: done, seconds: 1.6, key: "making-alone" }));
  }

  // The two lines knotted, or our line resting alone.
  function knotLines(s, cls, width, height) {
    const y = height / 2;
    // Loops are approached from outside their place, so the drawing keeps a margin at both ends.
    // Each line loops near its own side and ends before the other's loop: they cross once, in the middle.
    const ours = placeLine((s.label || "booth").toLowerCase(), width * 0.03, width * 0.56, y, height * 0.5, 1);
    if (!s.live && !s.place) return band(cls, width, height, stroke(ours, "ours", { from: 0, to: 1, seconds: 1.8 }));
    return band(cls, width, height,
      stroke(placeLine((s.place || "other").toLowerCase(), width * 0.97, width * 0.44, y, height * 0.5, 1), "theirs", { from: 0, to: 1, seconds: 1.8 }),
      stroke(ours, "ours", { from: 0, to: 1, seconds: 1.8 }));
  }

  const screen = (extra, ...children) => el("div", "screen " + (extra || ""), ...children);
  const top = (label) => el("div", "top", el("span", "", "MEET A STRANGER"), el("span", "", label || ""));
  // With a push button, paging hints name the button (ADR-036); ENTER still works throughout.
  let buttonLabel = null;

  // The round button (ADR-040), drawn as the circle it is, in the same place on every screen, with one
  // word beside it. `pulse` while a press is expected; `ring` in seconds: the ring empties as time runs out.
  function press(de, en, options = {}) {
    return el("div", "press action slot" + (options.pulse ? " pulse" : ""),
      svg("svg", { class: "button", viewBox: "0 0 140 140", "aria-hidden": "true" },
        svg("circle", { class: "track", cx: 70, cy: 70, r: 63 }),
        options.ring ? svg("circle", { class: "ring", cx: 70, cy: 70, r: 63, "data-ring": options.ring }) : null,
        svg("circle", { class: "cap", cx: 70, cy: 70, r: 44 })),
      el("div", "", de, en ? el("small", "", en) : null));
  }

  // A QR code from its rows of modules ("1" dark), with the quiet zone of four modules readers need.
  function qrCode(rows) {
    const size = rows.length;
    let d = "";
    rows.forEach((row, y) => {
      for (const run of row.matchAll(/1+/g)) d += `M${run.index} ${y}h${run[0].length}v1h-${run[0].length}z`;
    });
    return svg("svg", { class: "qr", viewBox: `-4 -4 ${size + 8} ${size + 8}`, "shape-rendering": "crispEdges", role: "img", "aria-label": "QR-Code" },
      svg("rect", { x: -4, y: -4, width: size + 8, height: size + 8, class: "qr-paper" }),
      svg("path", { d, class: "qr-ink" }));
  }

  function runRings(node) {
    for (const ring of node.querySelectorAll("circle[data-ring]")) {
      const length = 2 * Math.PI * 63;
      ring.style.strokeDasharray = `${length} ${length}`;
      // Information, not decoration: the ring runs even with reduced motion, slowly and evenly.
      ring.animate([{ strokeDashoffset: 0 }, { strokeDashoffset: length }], { duration: Number(ring.dataset.ring) * 1000, easing: "linear", fill: "both" });
    }
  }


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
      else if (line === line.toUpperCase() && /[A-ZÄÖÜ]/.test(line) && line.length < 90 && (!lines[i - 1] || !lines[i - 1].trim())) blocks.push({ head: line });
      else if (last && last.para !== undefined) last.para += " " + line;
      else blocks.push({ para: line });
    }
    return { title, blocks: blocks.filter(Boolean), keys };
  }

  function drawNotice(notice) {
    const body = el("div", "notice");
    let list = null;
    for (const block of notice.blocks) {
      if (block.head !== undefined) {
        list = null;
        body.append(el("h3", "notice-head", block.head));
      } else if (block.item !== undefined) {
        if (!list) { list = el("ul"); body.append(list); }
        list.append(el("li", "", block.item));
      } else {
        list = null;
        body.append(el("p", "", block.para));
      }
    }
    return body;
  }

  // Never below the panel's floor of 18 px (ADR-038): a longer notice gets more pages instead.
  const MAX_NOTICE = 40, MIN_NOTICE = 36;

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
      pages.textContent = count === 1 ? "" : `Seite ${page + 1} von ${count}` + (seenAll ? "   ↑ ↓" : buttonLabel ? " · weiter mit dem Knopf" : " · weiter mit ↓");
      pages.classList.remove("nudge");
    };
    show();
    return {
      move: (delta) => { page = Math.min(count - 1, Math.max(0, page + delta)); show(); },
      ready: () => seenAll,
      nudge: () => { pages.classList.remove("nudge"); void pages.offsetWidth; pages.classList.add("nudge"); },
    };
  }

  function keyActions(keys, seconds) {
    const labels = {
      ENTER: (t) => t.split(" / "),
      ESC: (t) => t.split(" / "),
    };
    return el("div", "actions", ...keys.filter((k) => k.key === "ENTER").map(({ key: name, text }) => {
      const [de, en] = labels[name](text);
      return press(de, en || "", name === "ENTER" ? { pulse: true, ring: seconds } : {});
    }));
  }

  function noticeScreen(state) {
    const notice = parseNotice(state.text);
    const body = drawNotice(notice);
    const pages = el("span", "pages");
    const actions = keyActions(notice.keys, state.seconds);
    const enter = actions.firstElementChild;
    const node = screen("",
      notice.title && el("h2", "notice-title", notice.title),
      body,
      el("div", "bottom", el("span"), pages), actions);
    if (enter) enter.classList.add("locked");  // until the text has been measured
    node.after_mount = () => { node.paging = fitNotice(body, pages, enter); };
    node.is_notice = true;
    return node;
  }

  function encounter(state) {
    const e = state.encounter;
    if (!e) return null;
    const side = el("div", "side");
    if (e.kind === "searching") {
      side.append(el("h3", "", e.de));
      if (e.seconds !== undefined) side.append(el("div", "wait", e.seconds, el("small", "", "s")));
    } else if (e.kind === "live") {
      side.append(el("h3", "", e.de));
      if (e.message) side.append(el("div", "said", el("b", "", e.message.from), el("span", "", e.message.text)));
      if (e.status) side.append(el("p", "note", e.status.de, el("br"), e.status.en));
    } else {
      return null;  // nothing to do or wait for: the screen stays with the strip being made
    }
    return side;
  }

  const views = {
    start: (s) => screen("start",
      top(s.label),
      el("h1", "hero", s.hero.de),
      startLines(s),
      press("Start", "", { pulse: true }),
      el("div", "signature",
        s.camera_note && el("div", "camera-note", el("span", "", s.camera_note.de), el("span", "en", s.camera_note.en)),
        midap())),

    // The booth rests after a while without a press (ADR-041): the project behind it, and one press to wake it.
    // With an address the QR code leads, on white so every phone reads it; MIDAP's mark then sits by its name.
    resting: (s) => {
      const code = s.address && s.address.modules && qrCode(s.address.modules);
      const about = el("div", "rest-about",
        el("p", "rest-midap", code && svg("svg", { class: "rest-mark small", viewBox: "-1 -1 23 20", "aria-hidden": "true" }, svg("path", { d: MIDAP_MARK })), "MIDAP"),
        el("p", "rest-name", s.name),
        el("p", "rest-cooperation", s.cooperation.de),
        el("p", "rest-cooperation-en en", s.cooperation.en),
        s.address && !code && el("p", "rest-address-text", s.address.text));
      return screen("resting",
        el("div", "rest-body" + (code ? " with-code" : ""),
          code ? el("div", "rest-code", code,
            el("p", "rest-address", s.address.text),
            el("p", "rest-address-de", s.address.de), el("p", "rest-address-en en", s.address.en))
            : svg("svg", { class: "rest-mark", viewBox: "-1 -1 23 20", "aria-hidden": "true" }, svg("path", { d: MIDAP_MARK })),
          about),
        press(s.wake.de, s.wake.en));
    },

    // Another booth invites this one to a shared strip (ADR-037): the seconds are counted down here.
    invite: (s) => {
      const number = el("div", "invite-number", s.seconds);
      const ends = performance.now() + s.seconds * 1000;
      let shown = false;
      const tick = () => {
        number.textContent = Math.max(0, Math.ceil((ends - performance.now()) / 1000));
        shown = shown || number.isConnected;
        if (!shown || number.isConnected) setTimeout(tick, 250);  // stops once the screen has been replaced
      };
      tick();
      return screen("invite",
        el("div", "invite-body", el("h1", "hero", s.hero.de), el("div", "invite-time", number)),
        inviteLines(s, s.screen === currentName),
        press("Mitmachen", "Join", { pulse: true }));
    },

    // The pool's end time is in the pool sentence itself; the ring around the button shows the time left.
    consent: (s) => noticeScreen(s),

    // White from here until the photo has been taken: the screen is extra light for the camera.
    countdown: (s) => screen("countdown light", el("div", "number", s.number)),

    flash: () => screen("light"),

    making: (s) => {
      const left = el("div", "");
      left.append(el("h1", "hero", "Dein Streifen entsteht"), el("p", "remaining", s.remaining || ""), makingLines(s, side_width(s)));
      const side = encounter(s);
      const talk = s.encounter && s.encounter.kind === "live" && s.encounter.talk;
      return screen("", el("div", "making" + (side ? "" : " alone"), left, side),
        talk && press(`Sprechen · bis zu ${talk} s`, "Talk", { pulse: true }));
    },

    live_moment: (s) => screen("center",
      el("div", "live-word", "LIVE"),
      knotLines(s, "moment-lines", 1400, 260),
      el("h1", "hero", s.de)),

    listening: (s) => screen("listen listening",
      el("p", "lead", "Ich höre zu"),
      el("div", "seconds", s.seconds),
      el("div", "meter", (() => { const bar = el("i"); bar.style.width = (100 * s.seconds / s.limit) + "%"; return bar; })()),
      press("Fertig", "Done", { pulse: true })),

    converting: () => screen("listen", el("h1", "hero", "Deine Stimme wird zu Text")),

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

    // The code is the one thing to write down: the largest thing on the screen, in groups of four.
    done: (s) => screen("done",
      el("div", "done-head", el("div", "done-word", "Fertig!"), knotLines(s, "done-lines", 760, 200)),
      s.code && el("p", "code", s.code.match(/.{1,4}/g).join(" ")),
      s.code && el("p", "info", "Mit diesem Code lässt die Betreuung deine Bilder löschen."),
      !s.code && el("p", "info", "Zum Löschen deiner Bilder wende dich an die Betreuung."),
      s.pool && el("p", "info", `Im Pool bis ${s.pool}. Der Code löscht es auch dort.`),
      press("Mehr Zeit", "More time", { ring: s.seconds })),

    message: (s) => screen("center",
      el("h1", "hero", s.de), el("p", "hero-en en", s.en),
      s.key && press("Weiter", "Continue")),

    menu: (s) => screen("", el("div", "menu", s.text)),
  };

  let current = null;
  let currentName = null;

  function render(stage, state) {
    buttonLabel = state.button ? "KNOPF" : null;
    const node = views[state.screen](state);
    if (state.test_mode) node.append(el("div", "test-mode", "TESTMODUS · KEINE KAMERA"));
    if (state.screen === currentName) node.classList.add("instant");  // a redraw of the same screen must not flicker
    currentName = state.screen;
    stage.replaceChildren(node);
    current = node;
    drawLines(node, node.classList.contains("instant"));
    if (!node.classList.contains("instant")) runRings(node);
    if (node.after_mount) document.fonts.ready.then(node.after_mount);
  }

  // The canvas in ui.css, drawn to fit: at half size on the box's 7-inch panel at 1280 x 800.
  const CANVAS_WIDTH = 2560, CANVAS_HEIGHT = 1600;

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
