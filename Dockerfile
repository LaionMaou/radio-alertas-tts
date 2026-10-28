FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    KOKORO_JA_CACHE=/opt/kokoro-cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Ensamblar el modelo en la imagen evita el retraso del primer aviso.
RUN python -c "from kokoro_ja_voice import model_path; print(model_path())"

COPY src ./src
COPY scripts ./scripts

RUN useradd --system --uid 10001 --home-dir /app alerts \
    && mkdir -p /data \
    && chown -R alerts:alerts /data /opt/kokoro-cache

USER alerts

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD python /app/scripts/healthcheck.py

CMD ["python", "-m", "radio_alerts_tts.api"]
