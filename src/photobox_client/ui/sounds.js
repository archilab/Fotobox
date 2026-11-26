// Soft, warm sounds at a few moments (ADR-047), made here with Web Audio: no sound files.
// The kiosk names the sound with each screen (`sound`, `volume`); this plays it once per screen, and repeats
// a call from another booth every few seconds while it is shown. Resting, the video link and recording a
// voice message stay silent, so the microphone never picks a sound up.
(() => {
  "use strict";

  const SILENT = new Set(["resting", "listening", "converting", "video_link"]);
  const CALL_EVERY_MS = 4000;
  const CALL_FOR_MS = 20000;
  const C5 = 523.25, E5 = 659.25, G4 = 392.0, G5 = 783.99;

  let audio = null, out = null;
  let volume = 0, screen = null, lastKey = null, repeat = null;

  function context() {
    if (!audio) {
      audio = new AudioContext();
      const soft = audio.createBiquadFilter();  // takes the edge off every tone: warm, not shrill
      soft.type = "lowpass";
      soft.frequency.value = 2400;
      soft.Q.value = 0.5;
      out = audio.createGain();
      out.connect(soft);
      soft.connect(audio.destination);
    }
    if (audio.state === "suspended") audio.resume().catch(() => {});
    out.gain.value = Math.pow(volume / 100, 2) * 0.8;  // heard loudness grows roughly with the square
    return audio;
  }

  // One tone with a soft start and a long, even fade, and the octave below for warmth.
  function tone(frequency, at, length, level) {
    const now = audio.currentTime + at;
    for (const [factor, share, type] of [[1, 1, "triangle"], [0.5, 0.35, "sine"]]) {
      const osc = audio.createOscillator();
      const gain = audio.createGain();
      osc.type = type;
      osc.frequency.value = frequency * factor;
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.linearRampToValueAtTime(level * share, now + 0.012);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + length);
      osc.connect(gain);
      gain.connect(out);
      osc.start(now);
      osc.stop(now + length + 0.05);
    }
  }

  // A short breath of noise, for the shutter.
  function hush(at, length, level) {
    const now = audio.currentTime + at;
    const samples = Math.ceil(audio.sampleRate * length);
    const buffer = audio.createBuffer(1, samples, audio.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < samples; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / samples);
    const source = audio.createBufferSource();
    const band = audio.createBiquadFilter();
    const gain = audio.createGain();
    source.buffer = buffer;
    band.type = "bandpass";
    band.frequency.value = 1500;
    band.Q.value = 0.8;
    gain.gain.value = level;
    source.connect(band);
    band.connect(gain);
    gain.connect(out);
    source.start(now);
  }

  const SOUNDS = {
    tick: () => tone(330, 0, 0.08, 0.35),
    count: (state) => tone(state.number === 1 ? E5 : C5, 0, 0.4, 0.4),  // the last one a little higher
    shutter: () => { hush(0, 0.05, 0.6); hush(0.06, 0.06, 0.4); tone(110, 0, 0.1, 0.25); },
    ready: () => { tone(G4, 0, 0.7, 0.4); tone(C5, 0.15, 1.0, 0.4); },
    call: () => { tone(C5, 0, 0.6, 0.35); tone(E5, 0.14, 0.6, 0.35); tone(G5, 0.28, 0.9, 0.35); },
  };

  function play(name, state) {
    if (!volume || SILENT.has(screen) || !SOUNDS[name]) return;
    try {
      context();
      SOUNDS[name](state || {});
    } catch (error) {
      /* no sound is better than no screen */
    }
  }

  function stopRepeating() {
    if (repeat) clearInterval(repeat);
    repeat = null;
  }

  // Called for every state the kiosk sends.
  function follow(state) {
    volume = Number(state.volume) || 0;
    screen = state.screen;
    const key = state.id + ":" + (state.sound || "");
    if (key === lastKey) return;  // the same screen again, updated: its sound has been played
    lastKey = key;
    stopRepeating();
    if (!state.sound) return;
    play(state.sound, state);
    if (state.sound === "call") {
      const started = Date.now();
      repeat = setInterval(() => {
        if (Date.now() - started >= CALL_FOR_MS) return stopRepeating();
        play("call", state);
      }, CALL_EVERY_MS);
    }
  }

  // The page sent a press: a short tick, so the visitor hears that it counted.
  function tick() {
    play("tick");
  }

  window.PhotoboxSounds = { follow, tick };
})();
