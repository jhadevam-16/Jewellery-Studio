import os
import requests
from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
from flask_cors import CORS

load_dotenv()

app = Flask(__name__, static_folder='public')
CORS(app)

ANTHROPIC_KEY = os.getenv('ANTHROPIC_KEY')
GOOGLE_KEY    = os.getenv('GOOGLE_KEY')

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
        return jsonify(response.json())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Gemini 2.5 Flash Image generation ────────────────────
@app.route('/api/generate-image-gemini', methods=['POST'])
def generate_image_gemini():
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
            timeout=45   # hard 45s per image — fail fast if stuck
        )

        result = response.json()
        print(f'[Gemini] HTTP {response.status_code}')

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
        print('[Gemini] Request timed out after 45s')
        return jsonify({'error': 'Gemini timed out — model may be overloaded, try again'}), 504
    except Exception as e:
        print(f'[Gemini] Exception: {e}')
        return jsonify({'error': str(e)}), 500

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)