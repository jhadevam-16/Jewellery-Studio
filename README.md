# Design Innovation Studio — Python Backend

## Setup (first time only)

### 1. Open this folder in VS Code
File → Open Folder → select jewelry-studio-python

### 2. Open Terminal in VS Code
Press Ctrl + `

### 3. Create virtual environment (recommended)
```bash
python -m venv venv
```

Activate it:
- Windows: `venv\Scripts\activate`
- Mac/Linux: `source venv/bin/activate`

### 4. Install dependencies
```bash
pip install -r requirements.txt
```

### 5. Add your API keys
Open the `.env` file and paste your keys:
```
ANTHROPIC_KEY=sk-ant-your-actual-key
GOOGLE_KEY=AIza-your-actual-key
```

Get keys:
- Anthropic → https://console.anthropic.com
- Google    → https://aistudio.google.com/app/apikey

### 6. Run the server
```bash
python server.py
```

### 7. Open in any normal browser
```
http://localhost:5000
```

## Every time after that
```bash
venv\Scripts\activate   ← Windows only
python server.py
```
Then open http://localhost:5000

## How it works
```
Browser (index.html)
    ↓
Python Flask Server (server.py) ← API keys live here
    ↓              ↓
Anthropic API   Google Imagen 3
```
Keys never touch the browser. Works in any normal browser.
