# Developer guide

For people who want to understand or change the code. To install and run the photo booth, see [INSTALL.md](../INSTALL.md).

## Project layout

| Folder | Contents |
|---|---|
| `src/photobox_server` | FastAPI server, SQLite storage, matching, generation jobs, image providers, strip renderer, admin commands, and the dashboard (`dashboard/`) |
| `src/photobox_client` | Pi side: the keyboard kiosk, setup menu, camera, framebuffer display and command line client |
| `tests` | `pytest` tests, using synthetic images only |
| `scripts` | Start scripts for server and dashboard, the M1 acceptance test, the evaluation tool |
| `deploy` | launchd templates for the Mac, systemd unit and polkit rule for the Pi |
| `benchmarks/ai_feasibility` | The standalone model benchmark |
| `docs` | Architecture, API, decisions, privacy, security, milestones; see [docs/README.md](README.md) |

## Development setup (macOS or Linux, Python 3.11+)

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev,server-ai]'
```
```bash
.venv/bin/python -m pytest -q
```
All tests run without a camera, a model or a network, using synthetic images and stand-ins.

For a server without the AI model, use the **mock** provider. It is not AI, needs developer mode, and labels every frame "MOCK / NOT AI":
```bash
cp .env.example .env
```
`.env.example` already sets `PHOTOBOX_PROVIDER=mock` and `PHOTOBOX_DEVELOPER_MODE=true`. Then:
```bash
.venv/bin/python -m uvicorn photobox_server.app:app --host 127.0.0.1 --port 8000
```

In a second terminal, add a counterpart and register a device. The token is printed once; only its hash is stored:
```bash
.venv/bin/python scripts/create_synthetic_seed.py
```
```bash
.venv/bin/python -m photobox_server.admin device add dev-01
```

## The command line client

Useful for testing without the kiosk. The token is read from a file or `$PHOTOBOX_DEVICE_TOKEN`, never from the command line:
```bash
export PHOTOBOX_DEVICE_TOKEN=PASTE_TOKEN_HERE
```
```bash
.venv/bin/python -m photobox_client.cli --server http://127.0.0.1:8000 capture --file path/to/synthetic.jpg --attest-documented-consent
```
`capture` refuses to run without `--attest-documented-consent`. Pass it only for synthetic images or with documented consent obtained outside this tool; it is an operator assertion, **not proof of consent**. Other commands: `health`, `status`, `result --profile color|grayscale|thermal`, `cancel`, and on the Pi `camera-list`, `camera-probe` and `camera-test`.

The kiosk can run against a test image instead of the camera:
```bash
.venv/bin/python -m photobox_client.kiosk --server http://127.0.0.1:8000 --test-image path/to/synthetic.png --once
```

## Rules for changes

The project rules are in [`CLAUDE.md`](../CLAUDE.md). In short:
- **Images:** treat portraits as sensitive personal data. Never commit images, credentials or `.env`, and use only synthetic or expressly authorized test images.
- **Model boundary:** route handlers never name a model; models are reached only through the provider interface (`photobox_server/providers`).
- **Network:** never bind the booth server to `0.0.0.0`, and never expose the dashboard beyond `127.0.0.1`.
- **Every change:** run the tests, update the docs, and record architectural decisions in [`DECISIONS.md`](DECISIONS.md).

## Deploying a working copy without Git

To try uncommitted changes on the Mac's service folder or the Pi, copy the tracked and new files, which leaves ignored files such as `.env` or `storage/` behind:
```bash
git ls-files -co --exclude-standard | tar -c -T - | tar -x -C ~/photobox-server
```
```bash
git ls-files -co --exclude-standard | tar -c -T - | ssh admin@fotobox.local 'tar -x -C ~/photobox'
```
Then restart the service or kiosk; see the [operator guide](OPERATOR_GUIDE.md#starting-stopping-and-restarting).

## Working with Claude Code

Run `claude` in the repository root. The curated [`CLAUDE.md`](../CLAUDE.md) is loaded automatically; don't run `/init`. Ready-made prompts are in [`CLAUDE_PROMPTS.md`](CLAUDE_PROMPTS.md), and progress is tracked in [`MILESTONES.md`](MILESTONES.md).
