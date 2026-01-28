# voice-assistant

[中文 README](README_zh.md)

Voice-driven schedule assistant (Google Calendar automation).

This project is a **voice-first smart scheduling web app**:
- The user clicks **Start Voice Conversation** in the frontend.
- Audio is transcribed → parsed into a calendar command → Playwright automates Google Calendar to create the event.

## Environment Setup

### Frontend

`node` v20.19.5

```bash
cd Frontend
npm i
```

### Backend

`Python` 3.10.11

```bash
pip install fastapi uvicorn[standard] python-multipart faster-whisper edge-tts opencc-python-reimplemented dateparser playwright
```

Install browser:

```bash
python -m playwright install chromium
```

Then move `chrome-win` into `Backend\tools`.

## Overview

```
Frontend/
  src/
    pages/Home/         # Voice conversation page
    utils/              # Axios wrapper
    router/             # React Router
    styles/             # Global SCSS variables
Backend/
  main.py               # FastAPI entry
  tools/
    speech.py           # Whisper STT + TTS
    nlp.py              # NLP for schedule parsing
    calendar_agent.py   # Playwright automation
    file_utils.py       # Temp file helpers
    models.py           # Data models
    chrome_profile/     # Persistent login profile
    chrome-win/         # Portable Chrome
```

### Frontend (React + Vite + AntD)

- **Entry**: `main.jsx`
- **Routing**: `App.jsx` + `router/routes.jsx`
- **Home page**: `pages/Home/index.jsx`
- **HTTP wrapper**:
  - `request.js`: axios instance + interceptors + error handling
  - `http.js`: `get/post/put/delete`
  - `api.js`: e.g. `postAPI("/voice", formData)`
- **Vite proxy**: `vite.config.js`
- **Global styles**: `src/styles/variables.scss`

### Backend (FastAPI + Whisper + Edge TTS + Playwright)

- **Entry**: `Backend/main.py`
  - FastAPI app + CORS (allows `http://localhost:5173`)
- **Speech**: `tools/speech.py`
  - Whisper `small`, `device="cpu"`, `compute_type="int8"`
  - OpenCC `t2s` for Traditional → Simplified conversion
  - TTS via `edge_tts` with bilingual voices + fallback
- **NLP**: `tools/nlp.py`
  - English and Chinese date/time parsing
- **Google Calendar Agent**: `tools/calendar_agent.py`
  - Playwright + local Chrome
  - Persistent login via `chrome_profile`
  - Compatible with EN/ZH time labels (e.g., "10am to 11am", "下午10点 - 下午11点")
- **Models**: `tools/models.py`

## Key Features

### 1. Frontend

#### Bilingual support(Chinese/English)

across UI, logs, errors, and NLP parsing

#### Waiting Animation

![image-20260127235511409](assets/image-20260127235511409.png)

![image-20260127235406224](assets/image-20260127235406224.png)

![image-20260127235446020](assets/image-20260127235446020.png)

### 2. Voice Interaction Flow

- Click button to start a voice conversation
- Backend acts as a voice assistant
- First response is a fixed greeting
- User speaks, system parses and replies

### 3. Google Calendar Automation

- Browser automation via Playwright
- Backend opens Calendar and creates events

### 4. Login & Session Persistence

- No Google Calendar API; UI automation only
- First run: real browser opens for login + MFA
- Session is persisted and reused

### 5. Conflict Detection & Creation

- Extract date/time from speech
- If conflict exists: respond with a conflict message
- Otherwise: create the event

## Run

Frontend:

```bash
cd Frontend
npm run dev
```

Backend:

```bash
cd Backend
python main.py
```

Open: `http://localhost:5173`

Click **Start Voice Conversation**.

![image-20251128155012810](assets/image-20251128155012810.png)

After the greeting, start recording and click **Stop Recording** when done.

![image-20251128155114969](assets/image-20251128155114969.png)

The app will open the browser and create the event:

![image-20251128155220583](assets/image-20251128155220583.png)

If it's your first login, complete the login in the opened browser window.

If there is a conflict, it returns a conflict response:

![image-20251128155313223](assets/image-20251128155313223.png)

## Known Issues & Limitations

### Manual Google Login

- Login must be completed manually in the browser.

### Playwright Depends on Network

- Slow networks can delay Calendar loading.
- There are timeouts and error messages, but unstable networks can still affect flow.

### Whisper Performance on CPU

- `small` on CPU can be slow on weaker machines.
- Consider `distil` or `tiny` for faster speed.

### NLP Is Not Full Semantics

- It has limited context handling.
- You can integrate LLMs (e.g., ChatGPT) for better semantic understanding.

### Same-Day Events Only

- Cross-day events are not supported yet.
- Hooks are reserved for future expansion.

### History

- No logging UI yet; Record page is reserved.

## Repository

- GitHub: https://github.com/Jayden3422/voice-assistant
