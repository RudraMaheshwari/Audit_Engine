FROM python:3.11-slim

# Tesseract OCR + system dependencies. Azure Document Intelligence is the PRIMARY OCR; Tesseract is the
# fallback (configured for English + Japanese). If you rely on the fallback for CJK docs, also add
# `tesseract-ocr-jpn` and `tesseract-ocr-kor` to the install list below.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libglib2.0-0 \
    libgl1 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Tesseract is at /usr/bin/tesseract in the container
ENV TESSERACT_CMD=/usr/bin/tesseract

# API bind host/port + worker count (overridable at runtime via -e / --env-file).
# IMPORTANT: the run-status store (app/core/run_store.py) is IN-PROCESS, so the API MUST run as a
# SINGLE uvicorn worker — multiple workers would each hold a separate store, and a status/stream request
# could land on a worker that never saw the run. Keep API_WORKERS=1 until that store is backed by Redis
# or the observability DB.
ENV API_HOST=0.0.0.0 \
    API_PORT=8000 \
    API_WORKERS=1
EXPOSE 8000

# Databricks / Azure credentials must be injected at runtime via --env-file or -e flags.
# Do NOT bake .env into the image. (main.py calls load_dotenv() when main:app is imported, so a mounted
# / --env-file .env is still picked up.)

# Liveness probe — the app exposes GET / (service banner). Uses only the Python stdlib (no curl needed).
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.getenv('API_PORT','8000')+'/',timeout=4).status==200 else 1)"

# Serve the FastAPI app (Risk Analysis, Scoping AI, Scoping Deck, Admin) with uvicorn.
# Shell form + `exec` so ${API_*} expand at start AND uvicorn becomes PID 1 (clean SIGTERM shutdown).
# Single worker by design (see the note above). Long audit runs execute as background tasks, so the
# request itself returns immediately with a run_id — no per-request timeout concern.
CMD exec uvicorn main:app --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}" --workers "${API_WORKERS:-1}" --timeout-keep-alive 75
