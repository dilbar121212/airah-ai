# Airah AI

Personal multilingual AI assistant — React + Vite frontend, Groq-powered chat.

- **Web app:** the Vite frontend in this repo (served from `/`)
- **Chat API:** `POST /api/chat` (Python serverless function in `api/chat.py`)
- **Mobile app:** the same AI behind the Airah AI Android app (APK)

## Live on Vercel (free Hobby plan)

1. Import this repo into Vercel (framework: Vite — auto-detected).
2. Add the environment variable **`GROQ_API_KEY`** in
   Vercel → Project → Settings → Environment Variables
   (get a free key at https://console.groq.com — no card needed),
   then redeploy. Without the key, `/api/chat` returns a clear error.
3. Optional: `GROQ_MODEL` (default `openai/gpt-oss-20b`).

## Local dev

```bash
npm install
npm run dev        # frontend on :5000, /api proxied to the FastAPI backend
```

The original FastAPI backend lives in `backend/` of the MYRA/Airah starter
project; the Vercel `api/chat.py` function replaces it for hosting.

Made by Silkylineai Studio.
