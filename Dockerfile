# Monorepo root Dockerfile for Railway (when Root Directory is NOT set).
# Prefer setting Root Directory = lenswrite-backend; that uses lenswrite-backend/Dockerfile.
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    USE_HTTP_PROXY=0 \
    PORT=8000

COPY lenswrite-backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY lenswrite-backend/app ./app

EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
