# QuantaColony study

A browser version of QuantaColony's Colony Detection for a study of how closely measurements agree
between users. Participants open one link, fill in a short form, read what to measure, practice on a
guided plate, then measure six plates in a random order. The results upload automatically at the end.

**Study link:** https://anava13.github.io/QuantaColony-External-Testing/

## How it works

- **The same code as the desktop.** The page runs QuantaColony's own Python (detection, settings
  estimate, undo, the benchmark recorder and the project file) in the browser with
  [Pyodide](https://pyodide.org). `python/quantacolony` is exported from the QuantaColony repository by
  `tools/study/export_web.py`; `python/SOURCE.json` lists the version and a hash of every file.
- **Library versions.** Pyodide 0.27.7 ships numpy 2.0.2, scipy 1.14.1 and scikit-image 0.25.0.
  Compared with the desktop on the study plates, every plate gives the same colony count; radii agree
  exactly on four plates and within 0.01% in total on the other three.
- **Results.** Each participant's results are one zip: `<name>.qcproj` (the project, with the
  benchmark tables `dev_detection_sessions`, `dev_detection_events`, `dev_initial_detection`, ...) and
  `participant.json` (answers, plate order and seed, stage times, tutorial steps skipped). The page posts
  the zip to the upload service (`worker/`), which commits it to the private repository
  `Anava13/quantacolony-study-results` under `results/<date>/`. If the upload fails, the participant
  can download the zip.

## Layout

| Path | What it is |
|---|---|
| `index.html`, `app/` | the page: `main.js` (flow and tools), `canvas.js` (the plate view), `bridge.js` (Pyodide) |
| `study.json`, `assets/concepts/` | the form's questions and the "What to measure" figures |
| `python/quantacolony/` | QuantaColony's Python, exported; `study/bundle/` holds the seven plates |
| `worker/` | the upload service (Cloudflare Worker) |
| `.github/workflows/deploy.yml` | deploys the upload service, then the page, on every push to `main` |

Setup steps (accounts and secrets): see [SETUP.md](SETUP.md).

## Running it locally

    printf 'window.STUDY_CONFIG = {};\n' > config.js
    (cd python && zip -qr ../python.zip quantacolony)
    python -m http.server 8000

Then open http://localhost:8000. Without an `uploadUrl` the page offers the results as a download.
