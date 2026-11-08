#!/usr/bin/env python3
"""Make the synthetic test speech with the Mac's built-in voices. No person is recorded.

    python3 make_synthetic.py OUTPUT_DIR
"""
import json
import subprocess
import sys
from pathlib import Path

VOICES = {"de": "Anna", "en": "Samantha"}
here = Path(__file__).parent
output = Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=True)
sentences = json.loads((here / "sentences.json").read_text(encoding="utf-8"))
manifest = []
for language, texts in sentences.items():
    for number, text in enumerate(texts, 1):
        name = f"synthetic-{language}-{number}"
        aiff, wav = output / f"{name}.aiff", output / f"{name}.wav"
        subprocess.run(["say", "-v", VOICES[language], "-o", str(aiff), text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)], check=True)
        aiff.unlink()
        manifest.append({"file": wav.name, "language": language, "text": text})
(output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"{len(manifest)} clips in {output}")
