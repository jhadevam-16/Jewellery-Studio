# Design Innovation Studio

Upload a product photo -> **Claude** analyzes it and proposes 6 product-development ideas -> **FLUX** (via fal.ai) renders the top pick.

The whole app is one small Flask server (`server.py`) that serves the frontend (`public/index.html`) and proxies two APIs, so your keys never reach the browser. **The same code runs locally and on Render** - only the API keys and the port differ, and both come from the environment.

```
Browser (public/index.html)
        |
Flask server (server.py)   <- API keys live here, never in the browser
        |                        |
   Anthropic (Claude)       fal.ai (FLUX image generation)
```

---

## Run locally (Windows)

### Option A - one click
1. Create your `.env` file: `copy .env.example .env`, then open `.env` and paste your keys (see **API keys** below).
2. Double-click **`run-local.bat`** (or run `.\run-local.bat` in a terminal). It creates a virtual environment, installs dependencies, and starts the server.
3. Open **http://localhost:5000** in any browser.

### Option B - manual steps (does the same thing)
```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python server.py
```
Then open **http://localhost:5000**.

> Local port defaults to **5000**. To use a different port for one session: `$env:PORT=8080` (PowerShell), then `python server.py`.

---

## API keys

Create a file named **`.env`** in this folder (copy it from `.env.example`) containing:
```
ANTHROPIC_KEY=sk-ant-...
FAL_KEY=...
```
- Anthropic (Claude): https://console.anthropic.com
- fal.ai (FLUX):       https://fal.ai/dashboard/keys

`.env` is **gitignored** - it is never committed or pushed. (An older `GOOGLE_KEY` from a previous version is no longer used and can be deleted.)

---

## Deploy to Render

Render runs the **same `server.py`**. Because `.env` is never uploaded, you set the keys in Render's dashboard instead.

**Service type:** Web Service (Python 3)

| Setting | Value |
|---|---|
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn server:app --bind 0.0.0.0:$PORT` |
| Environment variables | `ANTHROPIC_KEY` and `FAL_KEY` (add under **Environment**) |

Notes:
- `gunicorn` is already in `requirements.txt`. The `--bind 0.0.0.0:$PORT` part is required - Render provides `$PORT` automatically.
- `python server.py` also works as a start command (it reads `$PORT` too), but `gunicorn` is the production-grade option.
- After you push the code and set the two environment variables, Render builds and deploys automatically.

---

## Files
| File | Purpose |
|---|---|
| `server.py` | Flask server: serves the UI, proxies Claude + FLUX, in-memory rate limit |
| `public/index.html` | The entire frontend (UI + the idea-generation prompt) |
| `requirements.txt` | Python dependencies (includes `gunicorn` for Render) |
| `.env` | Your API keys (local only, gitignored) |
| `.env.example` | Template to copy into `.env` |
| `run-local.bat` | One-click local launcher (Windows) |
