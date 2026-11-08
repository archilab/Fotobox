// Design prototype (milestone U0): every screen with made-up data. Left and right arrows change screen.
(() => {
  "use strict";
  const CONSENT = `FOTOBOX - BITTE LESEN / PLEASE READ

Diese Fotobox macht ein Foto von dir. Ein Computer im lokalen Netz erzeugt daraus mit KI einen Fotostreifen, auf dem du mit einer weiteren Person in einer kleinen Geschichte zu sehen bist.

- LIVE: Macht gerade jemand an einer anderen Fotobox ein Foto, landet ihr zusammen auf einem Streifen, und ihr bekommt beide denselben.
- Während euer Streifen entsteht, könnt ihr euch kurze Sprachnachrichten schicken, die als Text erscheinen. Ton wird weder gesendet noch gespeichert.
- Dein Foto wird höchstens 1 Stunde gespeichert. Der Streifen wird mit ihm gelöscht.
- Andere Fotoboxen zeigen an, dass hier gerade jemand ein Foto macht, ohne Bild und ohne Angaben zu dir.
- Am Ende bekommst du einen Widerrufscode. Damit löscht die Betreuung deine Bilder sofort.
- Die Teilnahme ist freiwillig.

This photo booth takes a photo of you and uses AI to make a photo strip of you with a second person. If someone is taking a photo at another booth right now, you two share one strip. Your photo is kept for at most 1 hour. You get a code to have it deleted.

ENTER = Ich stimme zu / I agree, take the photo
ESC   = Abbrechen / Cancel`;
  const POOL = `DEIN FOTO IM POOL? / YOUR PHOTO IN THE POOL?

Darf dein Foto 24 Stunden lang mit Fotos anderer Besucherinnen und Besucher kombiniert werden?

- Andere bekommen dann einen KI-Fotostreifen, auf dem du zu sehen bist.
- Dein Foto selbst wird niemandem gezeigt.
- Mit deinem Widerrufscode kannst du es vorher löschen lassen.
- Mit ENTER stimmst du Foto und Pool gemeinsam zu.

May your photo be combined with other visitors' photos for 24 hours? Others then get an AI photo strip showing you. ENTER agrees to the photo and the pool together.

ENTER = Ja, in den Pool / Yes, add it to the pool
ESC = Nein, nur mein Streifen / No, just my strip`;

  // A drawn placeholder: no photograph of anyone.
  const frame = (y, shade) => `<rect x="20" y="${y}" width="520" height="390" fill="${shade}"/><circle cx="210" cy="${y + 170}" r="70" fill="#666"/><circle cx="360" cy="${y + 200}" r="58" fill="#888"/>`;
  const STRIP = "data:image/svg+xml," + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="560" height="1703"><rect width="560" height="1703" fill="#fff"/>${frame(20, "#333")}${frame(430, "#444")}${frame(840, "#2a2a2a")}${frame(1250, "#3a3a3a")}</svg>`);
  const LABEL = "BERLIN MEDIENHAUS";
  const CODE = "kQ3xV9mTzL7pRw2YbN4hJA";

  const screens = [
    { screen: "start", label: LABEL, hero: { de: "Deine nächste Begegnung beginnt hier.", en: "Your next encounter starts here." }, support: { de: "Mach ein Foto, um anzufangen.", en: "Take a photo to begin." }, tiles: [],
      camera_note: { de: "Die Kamera erkennt, ob jemand da ist. Kein Bild wird gespeichert oder gesendet.", en: "The camera detects whether someone is here. No image is stored or sent." } },
    { screen: "start", label: LABEL, badge: { kind: "live", de: "LIVE", en: "LIVE" }, hero: { de: "Jemand in Essen wartet auf ein gemeinsames Foto.", en: "Someone in Essen is waiting for a shared photo." }, support: { de: "Mach jetzt deins und ihr landet auf einem Streifen.", en: "Take yours now to join them." },
      tiles: [{ kind: "live", label: "Essen", status: "● LIVE" }, { kind: "recent", label: "London", status: "◔ GERADE EBEN" }, { kind: "online", label: "Wien", status: "○ ONLINE" }] },
    { screen: "consent", label: LABEL, text: CONSENT.split("ENTER =")[0] + POOL.split("ENTER =")[0] + "ENTER = Ich stimme zu: Foto und Pool / I agree: photo and pool", until: "09.11. 17:42", seconds: 45 },
    { screen: "countdown", number: 3 },
    { screen: "flash" },
    { screen: "making", label: LABEL, clock: "00:14", frames: { done: 0, total: 4 }, encounter: { kind: "searching", de: "Wir suchen jemanden an einer anderen Fotobox", en: "Looking for someone at another photo booth", seconds: 14 } },
    { screen: "live_moment", label: LABEL, de: "Du und jemand in Essen: ein gemeinsamer Fotostreifen!", en: "You and someone in Essen: one shared photo strip!" },
    { screen: "making", label: LABEL, clock: "01:27", frames: { done: 2, total: 4 }, encounter: { kind: "live", de: "Verbunden mit Essen", en: "Connected to Essen", talk: 8, message: { from: "ESSEN", text: "Hallo aus dem Ruhrgebiet! Schöne Grüße nach Berlin." } } },
    { screen: "listening", seconds: 6, limit: 8 },
    { screen: "converting" },
    { screen: "making", label: LABEL, clock: "02:51", frames: { done: 3, total: 4 }, encounter: { kind: "other", de: "Dein Foto trifft jemanden, der vorhin hier war.", en: "Your photo meets someone who was here earlier." } },
    { screen: "strip", image: STRIP, code: CODE },
    { screen: "done", label: LABEL, code: CODE, pool: "09.11. 17:42", seconds: 10 },
    { screen: "message", label: LABEL, de: "Abgebrochen. Es wurde kein Foto gemacht.", en: "Cancelled. No photo was taken.", key: true },
    { screen: "menu", text: "  OK  Network: FRITZ!Box 7590\n  OK  Server: 192.168.1.24:8000\n  OK  Pairing: paired\n  OK  Consent notice: ready\n\n  1  WiFi\n  2  Server address\n  3  Pair with server\n  4  Camera\n  5  Test everything\n  6  Start photo booth\n  7  Microphone (optional)\n\nESC = back" },
  ];

  const stage = document.getElementById("stage");
  const start = Number(new URLSearchParams(location.search).get("n") || 0);
  let index = Math.max(0, Math.min(screens.length - 1, start));
  const show = () => window.PhotoboxScreens.render(stage, screens[index]);
  const fit = () => window.PhotoboxScreens.fit(stage);
  window.addEventListener("resize", fit);
  window.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight") { index = (index + 1) % screens.length; show(); }
    if (event.key === "ArrowLeft") { index = (index + screens.length - 1) % screens.length; show(); }
    if (event.key === "ArrowDown") window.PhotoboxScreens.page(1);
    if (event.key === "ArrowUp") window.PhotoboxScreens.page(-1);
  });
  fit();
  show();
})();
