# Marine Propeller Advisor

Tkinter application for estimating a marine propeller configuration from power,
speed, and ship type. The app also searches public Internet Archive videos and
can use a local Ollama/Llama model to improve search queries and candidate
ranking.

## Structure

- `app.py` contains the Tkinter application shell, widget layout, event handlers,
  and in-app video playback orchestration.
- `constants.py` centralizes UI colors, video playback settings, ship metadata,
  archive endpoints, and Llama environment settings.
- `ships.py` handles ship labels and mapping UI selections back to ship type ids.
- `propeller.py` contains propeller estimation, recommendation, and slip note
  rules.
- `video_search.py` builds search queries, queries Internet Archive metadata,
  scores video candidates, and optionally asks local Llama for query/ranking
  help.
- `i18n.py` provides the small English/Arabic translation selector used across
  modules.

## Running

```bash
.venv/bin/python app.py
```

The app requires Pillow. In-app video playback also requires `ffmpeg`; when it is
not installed, the app still opens matched videos externally with **Open Video**.

## Verification

```bash
.venv/bin/python -m py_compile app.py constants.py i18n.py ships.py propeller.py video_search.py
```

Use `python -B` for import or smoke checks if you want to avoid writing
`__pycache__` files.
