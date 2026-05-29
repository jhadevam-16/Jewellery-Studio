import os
import time
import base64
import requests
from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
from flask_cors import CORS

load_dotenv()

app = Flask(__name__, static_folder='public')
CORS(app)

ANTHROPIC_KEY = os.getenv('ANTHROPIC_KEY')
FAL_KEY       = os.getenv('FAL_KEY')        # fal.ai API key — replaces GOOGLE_KEY

# ── Rate-limit config ─────────────────────────────────────
WINDOW_SECONDS = 3600    # 1-hour rolling window
USAGE_LIMIT    = 1500    # 25 minutes in seconds

# In-memory store:  ip -> { "window_start": float, "used": float, "last_req": float }
_usage = {}

def _get_ip():
    """Get real client IP (works behind Render's proxy)."""
    return request.headers.get('X-Forwarded-For', request.remote_addr or '0.0.0.0').split(',')[0].strip()

def _get_usage(ip):
    """Return (seconds_used, seconds_remaining, blocked, window_resets_at) for an IP."""
    now = time.time()
    rec = _usage.get(ip)

    if rec is None or (now - rec['window_start']) >= WINDOW_SECONDS:
        return 0, USAGE_LIMIT, False, 0

    used   = rec['used']
    resets = rec['window_start'] + WINDOW_SECONDS

    if used >= USAGE_LIMIT:
        return used, 0, True, resets

    return used, USAGE_LIMIT - used, False, resets

def _record_usage(ip, seconds):
    """Add `seconds` of usage for this IP."""
    now = time.time()
    rec = _usage.get(ip)

    if rec is None or (now - rec['window_start']) >= WINDOW_SECONDS:
        _usage[ip] = {'window_start': now, 'used': seconds, 'last_req': now}
    else:
        rec['used']    += seconds
        rec['last_req'] = now

def _check_limit():
    """Return a 429 JSON response if the user is blocked, else None."""
    ip = _get_ip()
    used, remaining, blocked, resets = _get_usage(ip)
    if blocked:
        wait = max(0, int(resets - time.time()))
        return jsonify({
            'error':        f'Usage limit reached (25 min / hour). Try again in {wait // 60}m {wait % 60}s.',
            'rate_limited': True,
            'wait_seconds': wait,
            'resets_at':    resets
        }), 429
    return None


# ── Usage status endpoint (polled by frontend) ───────────
@app.route('/api/usage-status')
def usage_status():
    ip   = _get_ip()
    used, remaining, blocked, resets = _get_usage(ip)
    now  = time.time()
    return jsonify({
        'used_seconds':      int(used),
        'remaining_seconds': int(remaining),
        'blocked':           blocked,
        'wait_seconds':      max(0, int(resets - now)) if blocked else 0,
        'limit_seconds':     USAGE_LIMIT,
        'window_seconds':    WINDOW_SECONDS
    })


# ── Serve frontend ────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('public', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('public', path)


# ── Anthropic (Claude) route ──────────────────────────────
@app.route('/api/analyze', methods=['POST'])
def analyze():
    blocked = _check_limit()
    if blocked:
        return blocked

    ip    = _get_ip()
    start = time.time()

    try:
        response = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'Content-Type':      'application/json',
                'x-api-key':         ANTHROPIC_KEY,
                'anthropic-version': '2023-06-01'
            },
            json=request.get_json(),
            timeout=60
        )
        _record_usage(ip, time.time() - start)
        return jsonify(response.json())
    except Exception as e:
        _record_usage(ip, time.time() - start)
        return jsonify({'error': str(e)}), 500


# ── FLUX.1 Dev image generation (replaces Gemini) ────────
#
# OLD: POST https://generativelanguage.googleapis.com/...gemini-2.5-flash-image
#      Auth: ?key=GOOGLE_KEY
#      Body: { contents, generationConfig: { responseModalities } }
#      Response: candidates[0].content.parts[].inlineData.data  (base64)
#
# NEW: POST https://fal.run/fal-ai/flux/dev
#      Auth: Authorization: Key FAL_KEY
#      Body: { prompt, image_size, num_images, num_inference_steps, guidance_scale }
#      Response: images[0].url  (we fetch → base64 to keep frontend unchanged)
#
@app.route('/api/generate-image-gemini', methods=['POST'])   # endpoint name kept — frontend unchanged
def generate_image_gemini():
    blocked = _check_limit()
    if blocked:
        return blocked

    ip    = _get_ip()
    start = time.time()

    try:
        data   = request.get_json()
        prompt = data.get('prompt', '')

        print(f'\n[FLUX] Prompt: {prompt[:120]}...')

        # ── Step 1: Call FLUX.1 Dev on fal.ai ────────────
        flux_response = requests.post(
            'https://fal.run/fal-ai/flux/dev',
            headers={
                'Content-Type':  'application/json',
                'Authorization': f'Key {FAL_KEY}'
            },
            json={
                'prompt':                prompt,
                'image_size':            'square_hd',   # 1024×1024
                'num_images':            1,
                'num_inference_steps':   28,             # FLUX Dev default
                'guidance_scale':        3.5,            # FLUX Dev default
                'enable_safety_checker': True
            },
            timeout=120    # FLUX Dev can take up to 90s on cold start
        )

        print(f'[FLUX] HTTP {flux_response.status_code}')

        if flux_response.status_code != 200:
            err = flux_response.json().get('detail') or f'FLUX API error {flux_response.status_code}'
            _record_usage(ip, time.time() - start)
            return jsonify({'error': err}), flux_response.status_code

        flux_result = flux_response.json()

        # ── Step 2: Extract image URL from FLUX response ──
        # FLUX response: { "images": [{ "url": "https://...", "content_type": "image/jpeg" }], ... }
        images = flux_result.get('images', [])
        if not images:
            _record_usage(ip, time.time() - start)
            return jsonify({'error': 'FLUX returned no images. Check your FAL_KEY and quota.'}), 500

        image_url    = images[0]['url']
        content_type = images[0].get('content_type', 'image/jpeg')

        # ── Step 3: Fetch image → base64 ─────────────────
        # Frontend expects { imageData: base64string, mimeType: string }
        # so we download the image and convert — zero frontend changes needed
        img_response = requests.get(image_url, timeout=30)
        if img_response.status_code != 200:
            _record_usage(ip, time.time() - start)
            return jsonify({'error': 'Failed to download generated image from FLUX.'}), 500

        image_b64 = base64.b64encode(img_response.content).decode('utf-8')

        _record_usage(ip, time.time() - start)

        # Same response shape as Gemini — frontend unchanged
        return jsonify({
            'imageData': image_b64,
            'mimeType':  content_type
        })

    except requests.exceptions.Timeout:
        _record_usage(ip, time.time() - start)
        print('[FLUX] Request timed out')
        return jsonify({'error': 'FLUX timed out — model may be busy, try again'}), 504
    except Exception as e:
        _record_usage(ip, time.time() - start)
        print(f'[FLUX] Exception: {e}')
        return jsonify({'error': str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)