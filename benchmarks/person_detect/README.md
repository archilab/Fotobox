# Person detection benchmark on the Pi (milestone D1, ADR-029)

Compares freely licensed models for telling whether someone is at the box. Run live from the camera on a Raspberry Pi 5 with 4 GB next to the running browser screen, 640 x 360, 2 threads.

Nothing is recorded: every frame is analysed at once and dropped, and only scores and timings are kept in `results.json`. Run `live_benchmark.py` yourself, in front of the camera, with nobody else in view.

Models, both Apache 2.0, from the OpenCV Zoo on Hugging Face, in `~/photobox-person-bench/models` on the Pi:
- `person.onnx`: `opencv/person_detection_mediapipe`, `person_detection_mediapipe_2023mar.onnx`, 12 MB
- `nanodet.onnx`: `opencv/object_detection_nanodet`, `object_detection_nanodet_2022nov.onnx`, 3.8 MB, class "person" only

## Results, 2026-11-08: one person, one room
Share of frames scoring at least 0.5.

| Situation | MediaPipe | NanoDet |
|---|---:|---:|
| Standing centre | 99% | 96% |
| Standing at the side | 100% | 99% |
| Back to the camera | 100% | 76% |
| Very close | 58% | 100% |
| Far away | 98% | 100% |
| Half in view | 100% | 100% |
| Moving | 100% | 98% |
| **Empty booth, three runs of 20 s** | **84-92% wrongly detected** | **0-4% wrongly detected** |
| Time per frame | 38 ms | 51 ms |
| CPU at 3 frames/s, of one core | 24% | 30% |

MediaPipe often scored the empty booth 1.0, so no threshold separates it. **NanoDet was chosen:** at 0.6 the empty booth stays at or below 1%, at 0.5 at or below 4%. A first empty run read higher for both models (61% for NanoDet), probably because the person was still leaving the view: it was not repeated in the three later runs, in which the person left the view fully. The temperature stayed at 44 °C.

## What this does not show
One person in one room and light. Not tested: several people, children, wheelchair users, darkness, changing light, a screen or poster showing people. A single frame is not trusted: the detector must smooth over time, so that a few frames at the threshold neither raise nor end a detection. Calibrate at the real location.
