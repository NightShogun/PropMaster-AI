# Marine Propeller Advisor

Marine Propeller Advisor is an educational propeller sizing tool for common ship
types. It estimates a rough propeller configuration from power, vessel speed, and
ship type, then shows a matching local photo or video preview.

The project includes a Tkinter desktop app and a static browser version. The
desktop code also contains an Internet Archive/Ollama video search helper, but
the current calculation flow displays local media from the repository.

## Features

- Estimates RPM, blade count, diameter, pitch, slip, and efficiency.
- Recommends fixed pitch, ducted, or controllable pitch propellers.
- Supports English and Arabic UI labels.
- Matches local photos and videos by ship type, blade count, and recommendation.
- Plays video previews in the desktop app with `ffmpeg` when available.
- Includes a static web app with the same core estimation rules.
- Includes a PyInstaller spec and Windows build script.

## Requirements

### Desktop app

- Python 3 with Tkinter.
- Pillow for image loading:

```bash
python -m pip install pillow
```

Optional:

- `ffmpeg` for in-app desktop video playback. Without it, local media can still
  be opened externally with **Open Media**.
- Ollama if you wire the archive search helper into the UI or call
  `video_search.py` functions directly.

On some Linux systems, Tkinter is packaged separately:

```bash
sudo apt install python3-tk
```

### Web app

The web app has no build step and no package dependencies. It runs as static
HTML, CSS, and JavaScript.

## Run The Desktop App

From the project root:

```bash
python app.py
```

If you use a virtual environment:

```bash
python -m venv .venv
.venv/bin/python -m pip install pillow
.venv/bin/python app.py
```

On Windows, activate the environment with `.venv\Scripts\activate` or run the
Python executable from `.venv\Scripts\python.exe`.

## Run The Web App

Open `web_app/index.html` directly in a browser.

For a local URL:

```bash
python -m http.server 8000
```

Then open:

```text
http://127.0.0.1:8000/web_app/
```

The web app references the existing `photos/` and `videos/` folders from the
project root.

## Inputs

- **Power (kW)**: engine or shaft power used by the rough sizing formula.
- **Speed (knots)**: vessel speed used to estimate pitch ratio and slip.
- **Ship Type**: one of cargo, tanker, container ship, passenger ship, yacht,
  tugboat, fishing vessel, or naval ship.
- **Media Type**: choose local videos or photos for the preview panel.

## Estimation Model

The core estimation logic lives in `propeller.py` and is mirrored in
`web_app/static/app.js`.

- RPM is estimated from `SHIP_RPM_FACTORS[ship_type] * sqrt(power_kw)`.
- Diameter is estimated from `(power_kw / (rpm * 3)) * 2`.
- Pitch ratio is `0.6` below 12 knots, `0.8` below 20 knots, and `1.0` at
  higher speeds.
- Slip compares vessel speed against theoretical propeller advance.
- Efficiency is clamped from `1 - abs(slip) * 0.7`.
- Cargo, tanker, and container ships use 5 blades; other ship types use 4.
- Speeds above 25 knots recommend a controllable pitch propeller.
- Slip above 0.5 recommends a ducted propeller.
- Other cases recommend a fixed pitch propeller.

This is a simplified educational model, not a substitute for naval architecture
or manufacturer design calculations.

## Local Media

The desktop and web apps score media filenames and paths using:

- blade count, such as `4_blade`, `5_blade`, `four blade`, or `five blade`;
- ship type names and aliases, such as `cargo`, `tanker`, or `tugboat`;
- propeller recommendation terms, such as `fixed`, `ducted`, or
  `controllable`;
- the word `propeller`.

Supported desktop photo extensions are `.jpg`, `.jpeg`, `.png`, `.webp`,
`.avif`, `.bmp`, and `.gif`. Supported desktop video extensions are `.mp4`,
`.m4v`, `.webm`, `.ogv`, and `.mov`.

## Optional Archive/Llama Search Helper

`video_search.py` can build Internet Archive queries, inspect archive metadata,
score candidate videos, and optionally ask a local Ollama model for query and
ranking help.

Environment variables:

```bash
LLAMA_VIDEO_SEARCH=1
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.2:1b
```

Set `LLAMA_VIDEO_SEARCH=0` to disable Ollama-assisted query/ranking calls.

## Build A Windows Executable

On Windows, run:

```bat
build_windows_exe.bat
```

The script creates `.venv-win`, installs Pillow and PyInstaller, and builds
`dist\MarinePropellerAdvisor.exe` using `MarinePropellerAdvisor.spec`. The spec
bundles the `photos/` and `videos/` directories.

## Project Structure

```text
app.py                         Tkinter desktop UI and local media preview logic
propeller.py                   Propeller estimation and recommendation rules
ships.py                       Ship labels and display-name mapping
i18n.py                        Small English/Arabic translation helper
constants.py                   UI constants, ship data, archive/Ollama settings
video_search.py                Internet Archive and Ollama search helper
photos/                        Local propeller images used by previews
videos/                        Local propeller videos used by previews
web_app/                       Static browser version
build_windows_exe.bat          Windows PyInstaller build script
MarinePropellerAdvisor.spec    PyInstaller build configuration
backup/                        Older backup copy of the desktop app
```

## Verification

Compile the Python modules:

```bash
python -m py_compile app.py constants.py i18n.py ships.py propeller.py video_search.py
```

For the web app, open `web_app/index.html` or serve the folder with
`python -m http.server` and run a calculation in the browser.
