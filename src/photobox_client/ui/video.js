// Picture and sound of the hidden video link (ADR-045, V2), directly between the two booths' browsers with
// WebRTC. Encrypted by WebRTC itself; nothing is recorded, and only the messages that set the link up pass
// through the booth and the server. In test mode no camera or microphone is used: a drawn, moving test picture
// and a quiet tone stand in for them, so tests never film anyone.
(() => {
  "use strict";

  let link = null;  // { id, role, pc, local, remote, pending[], test }

  function testStream() {
    const canvas = document.createElement("canvas");
    canvas.width = 640;
    canvas.height = 480;
    const g = canvas.getContext("2d");
    const started = performance.now();
    const draw = () => {
      if (!link || link.testCanvas !== canvas) return;
      const t = (performance.now() - started) / 1000;
      g.fillStyle = "#1d1533";
      g.fillRect(0, 0, 640, 480);
      g.strokeStyle = "#ffffff";
      g.lineWidth = 6;
      const x = 320 + Math.sin(t) * 120;
      g.beginPath(); g.arc(x, 220, 110, 0, Math.PI * 2); g.stroke();          // a drawn face, not a person
      g.beginPath(); g.arc(x - 40, 195, 10, 0, Math.PI * 2); g.stroke();
      g.beginPath(); g.arc(x + 40, 195, 10, 0, Math.PI * 2); g.stroke();
      g.beginPath(); g.arc(x, 245, 50, 0.15 * Math.PI, 0.85 * Math.PI); g.stroke();
      g.fillStyle = "#ffffff";
      g.font = "600 28px sans-serif";
      g.fillText("TESTBILD", 24, 452);
      requestAnimationFrame(draw);
    };
    const stream = canvas.captureStream(15);
    const audio = new AudioContext();
    const tone = audio.createOscillator();
    const quiet = audio.createGain();
    tone.frequency.value = 330;
    quiet.gain.value = 0.02;
    tone.connect(quiet);
    const out = audio.createMediaStreamDestination();
    quiet.connect(out);
    tone.start();
    stream.addTrack(out.stream.getAudioTracks()[0]);
    return { stream, canvas, draw, audio };
  }

  async function localStream(test) {
    if (test) return testStream();
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: "user" },
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    return { stream };
  }

  function show(node) {
    if (!link || !node) return;
    const remote = node.querySelector("video.remote");
    const self = node.querySelector("video.self");
    if (self && link.local && self.srcObject !== link.local) self.srcObject = link.local;
    if (remote && link.remote && remote.srcObject !== link.remote) {
      remote.srcObject = link.remote;
      remote.play().catch(() => { remote.muted = true; remote.play().catch(() => {}); });  // picture at least
    }
    node.classList.toggle("connected", !!link.remote);
  }

  async function begin(state, node, send) {
    const current = { id: state.link, role: state.role, pending: [], test: !!state.test_mode, send, node };
    link = current;
    try {
      const media = await localStream(current.test);
      if (link !== current) return media.stream.getTracks().forEach((t) => t.stop());
      current.local = media.stream;
      current.audio = media.audio;
      if (media.canvas) { current.testCanvas = media.canvas; media.draw(); }
      const pc = new RTCPeerConnection({ iceServers: [] });  // the booths reach each other on their own network
      current.pc = pc;
      media.stream.getTracks().forEach((track) => pc.addTrack(track, media.stream));
      pc.ontrack = (event) => { current.remote = event.streams[0]; show(current.node); };
      pc.onicecandidate = (event) => { if (event.candidate) send("candidate", JSON.stringify(event.candidate)); };
      show(current.node);
      if (current.role === "caller") {
        await pc.setLocalDescription(await pc.createOffer());
        send("offer", pc.localDescription.sdp);
      }
      for (const signal of current.pending.splice(0)) await receive(signal);
    } catch (error) {
      if (current.node) current.node.classList.add("failed");  // the screen says picture and sound did not start
    }
  }

  async function receive(signal) {
    const current = link;
    if (!current) return;
    if (!current.pc || (signal.kind === "candidate" && !current.pc.remoteDescription)) {
      current.pending.push(signal);
      return;
    }
    const pc = current.pc;
    try {
      if (signal.kind === "offer" && current.role === "callee") {
        await pc.setRemoteDescription({ type: "offer", sdp: signal.data });
        await pc.setLocalDescription(await pc.createAnswer());
        current.send("answer", pc.localDescription.sdp);
        for (const later of current.pending.splice(0)) await receive(later);
      } else if (signal.kind === "answer" && current.role === "caller") {
        await pc.setRemoteDescription({ type: "answer", sdp: signal.data });
        for (const later of current.pending.splice(0)) await receive(later);
      } else if (signal.kind === "candidate") {
        await pc.addIceCandidate(JSON.parse(signal.data));
      }
    } catch (error) {
      /* a bad message is dropped; the link either connects with the others or the screen says it did not */
    }
  }

  function end() {
    if (!link) return;
    const old = link;
    link = null;
    if (old.pc) old.pc.close();
    if (old.local) old.local.getTracks().forEach((track) => track.stop());  // the camera and microphone are let go
    if (old.audio) old.audio.close();
  }

  // Called for every state the kiosk sends.
  function follow(state, node, send) {
    if (state.screen !== "video_link" || !state.media) return end();
    if (link && link.id === state.link) { link.node = node; return show(node); }
    end();
    begin(state, node, send);
  }

  window.PhotoboxVideo = { follow, receive, end };
})();
