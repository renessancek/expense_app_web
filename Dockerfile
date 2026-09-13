# Multi-arch friendly (linux/amd64 + linux/arm64), e.g. Raspberry Pi / x86 NAS.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    EXPENSE_DATA_DIR=/data \
    PORT=8080

WORKDIR /app

# Runtime certs + Tesseract (deu/eng) for Belege OCR on photos/scanned PDFs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        tesseract-ocr \
        tesseract-ocr-deu \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY src /app/src
COPY rules /app/rules

RUN mkdir -p /data/BankStatements /data/uploads /data/backups /data/receipts /data/receipts/uploads \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin expense \
    && chown -R expense:expense /data /app

USER expense
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"

CMD ["uvicorn", "web.main:app", "--host", "0.0.0.0", "--port", "8080"]
