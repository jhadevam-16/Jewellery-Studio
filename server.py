import os
import time
import requests
from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
from flask_cors import CORS

load_dotenv()

app = Flask(__name__, static_folder='public')
CORS(app)

ANTHROPIC_KEY = os.getenv('ANTHROPIC_KEY')
GOOGLE_KEY    = os.getenv('GOOGLE_KEY')

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
        # No record or window expired — fresh slate
        return 0, USAGE_LIMIT, False, 0

    used    = rec['used']
    elapsed = now - rec['window_start']
    resets  = rec['window_start'] + WINDOW_SECONDS

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
        rec['used']     += seconds
        rec['last_req']  = now

def _check_limit():
    """Return a 429 JSON response if the user is blocked, else None."""
    ip = _get_ip()
    used, remaining, blocked, resets = _get_usage(ip)
    if blocked:
        wait = max(0, int(resets - time.time()))
        return jsonify({
            'error': f'Usage limit reached (25 min / hour). Try again in {wait // 60}m {wait % 60}s.',
            'rate_limited': True,
            'wait_seconds': wait,
            'resets_at': resets
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
    # Rate-limit check
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
        elapsed = time.time() - start
        _record_usage(ip, elapsed)
        return jsonify(response.json())
    except Exception as e:
        elapsed = time.time() - start
        _record_usage(ip, elapsed)
        return jsonify({'error': str(e)}), 500


# ── Gemini 2.5 Flash Image generation ────────────────────
@app.route('/api/generate-image-gemini', methods=['POST'])
def generate_image_gemini():
    # Rate-limit check
    blocked = _check_limit()
    if blocked:
        return blocked

    ip    = _get_ip()
    start = time.time()

    try:
        data   = request.get_json()
        prompt = data.get('prompt', '')

        MODEL_ID = 'gemini-2.5-flash-image'
        url = f'https://generativelanguage.googleapis.com/v1beta/models/{MODEL_ID}:generateContent?key={GOOGLE_KEY}'

        body = {
            'contents': [{'parts': [{'text': prompt}]}],
            'generationConfig': {
                'responseModalities': ['TEXT', 'IMAGE']
            }
        }

        print(f'\n[Gemini] Prompt: {prompt[:100]}...')

        response = requests.post(
            url,
            headers={'Content-Type': 'application/json'},
            json=body,
            timeout=45
        )

        result = response.json()
        print(f'[Gemini] HTTP {response.status_code}')

        elapsed = time.time() - start
        _record_usage(ip, elapsed)

        if response.status_code != 200:
            err = result.get('error', {}).get('message') or f'HTTP {response.status_code}'
            return jsonify({'error': err}), response.status_code

        try:
            parts = result['candidates'][0]['content']['parts']
            for part in parts:
                if 'inlineData' in part:
                    return jsonify({
                        'imageData': part['inlineData']['data'],
                        'mimeType':  part['inlineData'].get('mimeType', 'image/png')
                    })
            return jsonify({'error': 'No image in response. Check billing.'}), 500
        except (KeyError, IndexError) as e:
            return jsonify({'error': f'Unexpected response: {e}'}), 500

    except requests.exceptions.Timeout:
        elapsed = time.time() - start
        _record_usage(ip, elapsed)
        print('[Gemini] Request timed out after 45s')
        return jsonify({'error': 'Gemini timed out — model may be overloaded, try again'}), 504
    except Exception as e:
        elapsed = time.time() - start
        _record_usage(ip, elapsed)
        print(f'[Gemini] Exception: {e}')
        return jsonify({'error': str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)