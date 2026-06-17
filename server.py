import os
import time
import json
import base64
import requests
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from dotenv import load_dotenv
from flask_cors import CORS

load_dotenv()

app = Flask(__name__, static_folder='public')
CORS(app)

ANTHROPIC_KEY = os.getenv('ANTHROPIC_KEY')
FAL_KEY       = os.getenv('FAL_KEY')

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


# ── Usage status endpoint (polled by frontend) ────────────
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


# ── Serve frontend ─────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('public', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('public', path)


# ── Anthropic (Claude) route — LIVE STREAMING TO BROWSER ──
#
# Uses stream=True upstream AND streams the text deltas straight
# through to the browser as Server-Sent Events. The frontend can
# render each idea the moment it is written, instead of waiting
# for all 6 ideas to finish — big perceived speed-up.
#
# SSE events sent to the browser:
#   data: {"type":"text","text":"<delta>"}
#   data: {"type":"done","usage":{...}}
#   data: {"type":"error","error":"<message>"}
#
@app.route('/api/analyze', methods=['POST'])
def analyze():
    blocked = _check_limit()
    if blocked:
        return blocked

    ip      = _get_ip()
    start   = time.time()
    payload = request.get_json()

    # Force streaming on — frontend payload may or may not include it
    payload['stream'] = True
    payload.setdefault('max_tokens', 2500)

    print(f'\n[CLAUDE] Starting live-streaming request (max_tokens={payload["max_tokens"]})')

    def generate():
        input_tokens  = 0
        output_tokens = 0
        char_count    = 0
        try:
            upstream = requests.post(
                'https://api.anthropic.com/v1/messages',
                headers={
                    'Content-Type':      'application/json',
                    'x-api-key':         ANTHROPIC_KEY,
                    'anthropic-version': '2023-06-01'
                },
                json=payload,
                stream=True,
                timeout=180
            )

            if upstream.status_code != 200:
                try:
                    err_body = upstream.json()
                    err_msg  = err_body.get('error', {}).get('message') if isinstance(err_body.get('error'), dict) else err_body.get('error')
                except Exception:
                    err_msg = f'Anthropic HTTP {upstream.status_code}'
                print(f'[CLAUDE] Non-200 from Anthropic: {upstream.status_code}')
                yield 'data: ' + json.dumps({'type': 'error', 'error': err_msg or f'Anthropic HTTP {upstream.status_code}'}) + '\n\n'
                return

            for raw_line in upstream.iter_lines():
                if not raw_line:
                    continue

                line = raw_line.decode('utf-8') if isinstance(raw_line, bytes) else raw_line

                if not line.startswith('data: '):
                    continue

                data_str = line[6:]
                if data_str.strip() == '[DONE]':
                    break

                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                etype = event.get('type', '')

                if etype == 'content_block_delta':
                    delta = event.get('delta', {})
                    if delta.get('type') == 'text_delta':
                        chunk = delta.get('text', '')
                        char_count += len(chunk)
                        yield 'data: ' + json.dumps({'type': 'text', 'text': chunk}) + '\n\n'

                elif etype == 'message_start':
                    input_tokens = event.get('message', {}).get('usage', {}).get('input_tokens', 0)

                elif etype == 'message_delta':
                    output_tokens = event.get('usage', {}).get('output_tokens', 0)

                elif etype == 'error':
                    err_msg = event.get('error', {}).get('message', 'Unknown stream error')
                    print(f'[CLAUDE] Stream error: {err_msg}')
                    yield 'data: ' + json.dumps({'type': 'error', 'error': err_msg}) + '\n\n'
                    return

            print(f'[CLAUDE] Stream complete — {char_count} chars, '
                  f'{input_tokens} in / {output_tokens} out tokens, {time.time() - start:.1f}s')
            yield 'data: ' + json.dumps({'type': 'done', 'usage': {'input_tokens': input_tokens, 'output_tokens': output_tokens}}) + '\n\n'

        except requests.exceptions.Timeout:
            print('[CLAUDE] Timeout waiting for Anthropic stream')
            yield 'data: ' + json.dumps({'type': 'error', 'error': 'Claude API timed out — please try again'}) + '\n\n'

        except requests.exceptions.ConnectionError as e:
            print(f'[CLAUDE] Connection error: {e}')
            yield 'data: ' + json.dumps({'type': 'error', 'error': 'Could not connect to Claude API — check your network'}) + '\n\n'

        except Exception as e:
            print(f'[CLAUDE] Unexpected error: {e}')
            yield 'data: ' + json.dumps({'type': 'error', 'error': str(e)}) + '\n\n'

        finally:
            _record_usage(ip, time.time() - start)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control':     'no-cache',
            'X-Accel-Buffering': 'no'   # disable proxy buffering so events arrive live
        }
    )


# ── FLUX.1 Dev image generation ───────────────────────────
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

        print(f'\n[FLUX] Prompt ({len(prompt)} chars): {prompt[:120]}...')

        # ── Step 1: Call FLUX.1 Pro on fal.ai ────────────
        #
        # Using flux-pro/v1.1 — sharper and more photorealistic than Dev.
        # negative_prompt suppresses the blur/soft-focus artefacts Dev produces.
        # guidance_scale 7.5 = strict prompt-following for precise product detail.
        #
        flux_response = requests.post(
            'https://fal.run/fal-ai/flux-pro/v1.1',
            headers={
                'Content-Type':  'application/json',
                'Authorization': f'Key {FAL_KEY}'
            },
            json={
                'prompt':          prompt,
                'negative_prompt': (
                    'blurry, soft focus, out of focus, bokeh, hazy, foggy, '
                    'low resolution, low quality, jpeg artifacts, noise, grain, '
                    'overexposed, underexposed, washed out, dull, flat lighting, '
                    'watermark, text, logo, signature, frame, border, '
                    'human hands, fingers, body parts, mannequin, jewellery stand, '
                    'extra products, duplicate, cropped, deformed, distorted'
                ),
                'image_size':           'square_hd',   # 1024×1024
                'num_images':           1,
                'num_inference_steps':  30,             # Pro converges faster than Dev
                'guidance_scale':       7.5,            # strict prompt-following
                'safety_tolerance':     '2',            # 1=strict … 6=permissive
            },
            timeout=120
        )

        print(f'[FLUX] HTTP {flux_response.status_code}')

        if flux_response.status_code != 200:
            err = flux_response.json().get('detail') or f'FLUX API error {flux_response.status_code}'
            _record_usage(ip, time.time() - start)
            return jsonify({'error': err}), flux_response.status_code

        flux_result = flux_response.json()

        # ── Step 2: Extract image URL ──────────────────────
        images = flux_result.get('images', [])
        if not images:
            _record_usage(ip, time.time() - start)
            return jsonify({'error': 'FLUX returned no images — check your FAL_KEY and quota.'}), 500

        image_url    = images[0]['url']
        content_type = images[0].get('content_type', 'image/jpeg')

        # ── Step 3: Fetch image → base64 ──────────────────
        img_response = requests.get(image_url, timeout=30)
        if img_response.status_code != 200:
            _record_usage(ip, time.time() - start)
            return jsonify({'error': 'Failed to download generated image from FLUX.'}), 500

        image_b64 = base64.b64encode(img_response.content).decode('utf-8')

        elapsed = time.time() - start
        _record_usage(ip, elapsed)
        print(f'[FLUX] Done — image delivered in {elapsed:.1f}s')

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
    # Local development server.
    # Locally PORT is usually unset, so we default to 5000  ->  http://localhost:5000
    # On Render the PORT env var is provided automatically and overrides this default.
    # host="0.0.0.0" makes the app reachable both locally and on Render.
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  Design Innovation Studio  ->  http://localhost:{port}")
    print("  Press CTRL+C to stop.\n")
    app.run(host="0.0.0.0", port=port)