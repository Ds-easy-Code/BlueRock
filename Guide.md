# Running & Updating This App (Podman)

## 1. Start Podman

```bash
podman machine start
```

## 2. Run the container

For the **first run**, or whenever `Dockerfile`, `requirements.txt`, or any
file under `app.py` / `core/` / `frontend/` has changed:

```bash
podman compose up --build
```

`--build` forces Podman to rebuild the image from the current files instead
of reusing whatever was built last time.

If nothing has changed since your last build, you can just do:

```bash
podman compose up
```

Then open **http://localhost:8501**.

## 3. Stopping the app

```bash
podman compose down
```

---

## Why `--build` matters

`compose.yaml` does not mount the source as a live volume — the Dockerfile
does `COPY . .` at **build time**. That means:

- Editing any file and running `podman compose up` (without `--build`) will
  **silently reuse the old image** and your changes won't appear.
- You must rebuild (`--build` or `podman compose build`) any time you change
  `app.py`, anything under `core/` or `frontend/`, `requirements.txt`, or
  the `Dockerfile` itself.

### Rebuilding from scratch (ignores Podman's layer cache entirely)

Use this if a normal `--build` still doesn't seem to pick up your changes:

```bash
podman compose build --no-cache
podman compose up
```

### Enable live-reload instead (optional)

If you're iterating frequently and don't want to rebuild every time, mount
the source as a volume in `compose.yaml`:

```yaml
services:
  app:
    build: .
    container_name: bluerock
    working_dir: /app
    ports:
      - "8501:8501"
    volumes:
      - ./app.py:/app/app.py:ro
      - ./core:/app/core:ro
      - ./frontend:/app/frontend:ro
```

With this in place, Streamlit's own file-watcher picks up changes to any
mounted `.py` file on a page refresh — no rebuild needed. You'll still need
to rebuild whenever `requirements.txt` or the `Dockerfile` changes.

---

## Pushing this project to GitHub

**Upload the source code, not a built container image.** The image is a
compiled artifact — anyone with this repo and Podman/Docker installed can
rebuild the exact same image from the `Dockerfile`, so committing the image
itself would only bloat the repo with binary layers (and often doesn't work
well with git at all). What belongs in the repo is everything already in
this project folder:

```
app.py
core/
frontend/
.streamlit/config.toml
requirements.txt
Dockerfile
compose.yaml
README.md
Guide.md
```

A minimal `.gitignore` (included in this project) keeps Python bytecode and
local virtual environments out of the repo:

```
__pycache__/
*.pyc
.venv/
venv/
```

Typical first push:

```bash
git init
git add .
git commit -m "Initial commit: isolated ZIP media viewer"
git branch -M main
git remote add origin <your-repo-url>
git push -u origin main
```

Anyone cloning the repo can then get a running app with:

```bash
podman machine start
podman compose up --build
```

If you'd rather distribute a pre-built image instead of asking people to
build it themselves (e.g. for a deployment pipeline), that's a separate
concern from source control — you'd push the built image to a container
registry (Docker Hub, GitHub Container Registry, etc.) with `podman push`,
not commit it to git.

---

## Troubleshooting

| Symptom                                                      | Likely Cause                                                                    | Fix                                                                                                                                          |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Code changes don't appear at all                             | Image wasn't rebuilt                                                            | `podman compose up --build`                                                                                                                  |
| Still stale after `--build`                                  | Cached layer wasn't invalidated                                                 | `podman compose build --no-cache` then `up`                                                                                                  |
| UI looks visually broken/outdated after a fix                | Browser cached old frontend assets                                              | Hard refresh: `Ctrl+Shift+R` (or `Cmd+Shift+R` on Mac)                                                                                       |
| `ModuleNotFoundError` after adding a package                 | `requirements.txt` updated but image not rebuilt                                | Rebuild — dependency changes always require a rebuild, volume mount or not                                                                   |
| `ModuleNotFoundError: No module named 'core'` / `'frontend'` | Missing `__init__.py`, or working directory isn't the project root              | Confirm `core/__init__.py` and `frontend/__init__.py` exist; confirm Dockerfile's `WORKDIR` matches where `app.py` lives                     |
| Port already in use / seeing an old version                  | An older container is still running                                             | `podman ps -a` to find it, then `podman stop <container_id>`                                                                                 |
| Video thumbnails fail to generate                            | OpenCV missing or unreadable codec                                              | Confirm `opencv-python-headless` is in `requirements.txt` and the image was rebuilt                                                          |
| A vertical/horizontal scrollbar appears on the page          | The gallery's fixed component height doesn't match your actual browser viewport | Confirmed fixed via CSS locking the outer page + iframe to `100vh` — if it recurs, check `app.py`'s injected `<style>` block wasn't reverted |

## Quick Reference

```bash
podman machine start              # start the podman VM
podman compose up --build         # build (if needed) + run
podman compose down               # stop and remove the container
podman ps -a                      # list all containers, including stopped ones
podman compose build --no-cache   # nuke the build cache and rebuild fresh
```
