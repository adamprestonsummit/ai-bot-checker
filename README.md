# AI Crawler Access Tester (Streamlit)

A Streamlit app that simulates requests from popular AI crawlers by sending real HTTP requests with each crawler’s **User-Agent**, following redirects and lightly classifying the outcome.

> ⚠️ This app **does not** attempt to bypass bot defenses. It simply helps you observe how your site responds to these UAs. It also does **not** enforce `robots.txt`—that file is shown only for context.

## Features
- Choose one or many AI crawlers (editable UA list in `app.py`)
- `HEAD` then `GET` fallback, redirect following, HTTP/2 when available
- Block/allow heuristic (status codes + body/headers hints)
- Results table + per-result detail panels
- Export CSV/JSON
- Optional `robots.txt` viewer

## Quickstart (local)
```bash
python -m venv .venv && source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
streamlit run app.py
