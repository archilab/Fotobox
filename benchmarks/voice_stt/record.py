#!/usr/bin/env python3
"""Record the benchmark sentences read aloud by the person running this, on the Pi.

    python3 record.py sentences.json OUTPUT_DIR [ALSA_DEVICE]

Only record yourself, or someone who has agreed. Nothing is recorded until you press ENTER for that
sentence. Recordings go to a private folder; delete them once the benchmark has run.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SECONDS = 8
PER_LANGUAGE = 4
sentences = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
output = Path(sys.argv[2])
device = sys.argv[3] if len(sys.argv) > 3 else "plughw:CARD=Mini,DEV=0"
output.mkdir(parents=True, exist_ok=True)
output.chmod(0o700)
manifest = []
print(f"You will read {PER_LANGUAGE * len(sentences)} short sentences. Each recording lasts {SECONDS} seconds.\n")
for language, texts in sentences.items():
    for number, text in enumerate(texts[:PER_LANGUAGE], 1):
        input(f"\n[{language}] {text}\nPress ENTER, then read it aloud ... ")
        name = f"own-{language}-{number}.wav"
        path = output / name
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
        print("  RECORDING")
        subprocess.run(["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", "16000", "-c", "1", "-d", str(SECONDS), str(path)], check=True)
        print("  done")
        manifest.append({"file": name, "language": language, "text": text})
(output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
print("\nFinished. You can close this window.")
