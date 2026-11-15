# Blanc backend (FastAPI) for the optional hosted app path (BLA-61).
# Evidence stays out of the image: the case database and media live on a mounted
# volume (/data) that the operator controls. The Gemini key comes from the host's
# secret store as GEMINI_API_KEY; no .env file is copied in.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_URL=sqlite:////data/blanc.db \
    CASES_DIR=/data/cases

WORKDIR /srv/blanc
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app
COPY evidence_prompt.py ./
COPY prompts ./prompts

# Run as an unprivileged user that owns only the data volume.
RUN useradd --uid 10001 --create-home blanc && mkdir -p /data && chown blanc /data
USER blanc
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
