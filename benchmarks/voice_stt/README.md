# Local speech-to-text benchmark (milestone M-VOICE-1)

Compares speech engines on the Raspberry Pi 5 (4 GB) for the voice messages in `docs/INTERACTION_EXTENSION.md`, section 15.4. Everything runs offline on the Pi.

## Setup on the Pi
Everything lives in `~/photobox-stt-bench`, apart from the booth: a Python environment with `vosk==0.3.45` and `cmake`, whisper.cpp `v1.7.4` built from source, the Vosk small German and English models, and the whisper.cpp `tiny` and `base` models.

## Running it
1. `python3 make_synthetic.py OUTPUT_DIR` on a Mac creates speech with built-in voices, so no person is recorded.
2. `python3 record.py sentences.json OUTPUT_DIR` on the Pi records someone reading the sentences. Only record yourself or someone who has agreed.
3. `venv/bin/python benchmark.py AUDIO_DIR BENCH_DIR RESULT.json` measures each clip in its own process.

Results hold scores only, never recognized text. Recordings are personal data: they are git-ignored and should be deleted after the run.

## Results, 2026-11-08
Pi 5 with 4 GB, 4 threads, booth kiosk running and idle. "Time" is transcription after the model has loaded.

**Synthetic Mac voices, 8 clips per language, 2-3 s each**

| Engine | Language | Words wrong | Time, mean | Peak memory |
|---|---|---:|---:|---:|
| Vosk small | German | 6.7% | 0.58 s | 224 MB |
| Vosk small | English | 10.4% | 0.91 s | 182 MB |
| whisper.cpp tiny | German | 7.7% | 1.00 s | 240 MB |
| whisper.cpp tiny | English | 1.4% | 0.98 s | 240 MB |
| whisper.cpp base | German | 3.6% | 2.11 s | 371 MB |
| whisper.cpp base | English | 0.0% | 2.08 s | 371 MB |

**One person reading into a RØDE NT-USB Mini, quiet room, 4 clips per language, 8 s recordings**

| Engine | Language | Words wrong | Time, mean | Peak memory |
|---|---|---:|---:|---:|
| Vosk small | German | 3.6% | 0.75 s | 224 MB |
| Vosk small | English | 18.8% | 1.24 s | 182 MB |
| whisper.cpp tiny | German | 0.0% | 0.98 s | 241 MB |
| whisper.cpp tiny | English | 0.0% | 0.99 s | 241 MB |
| whisper.cpp base | German | 0.0% | 2.08 s | 371 MB |
| whisper.cpp base | English | 0.0% | 2.13 s | 371 MB |

## What this does not show
The sample is small, with one speaker in a quiet room. Noise, other voices, accents, children, short or mumbled phrases and silence are untested; whisper models are known to sometimes invent text for silence, so that needs checking before use. Peak input level was only 8-20% of full scale, so microphone gain should be checked. Live on-screen word-by-word feedback was not tested.

## Installed for the booth
The chosen engine is whisper.cpp `base` (ADR-026). In the whisper.cpp v1.7.4 source, build a self-contained program and copy it with the model to `~/photobox-speech` on the Pi:
```bash
cmake -B build-static -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DWHISPER_BUILD_TESTS=OFF && cmake --build build-static -j2 --target whisper-cli
```
```bash
mkdir -p ~/photobox-speech && cp build-static/bin/whisper-cli models/ggml-base.bin ~/photobox-speech/
```
Check it with `python -m photobox_client.speech test`, which prints volume figures and the recognized text on your terminal only.
