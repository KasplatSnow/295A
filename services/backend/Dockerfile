FROM python:3.11-slim

WORKDIR /app

ARG DJANGO_SECRET_KEY_BUILD=docker-build-only-secret
ARG DATABASE_URL_BUILD=postgresql://vigilzone:vigilzone@postgres:5432/vigilzone
ENV DJANGO_SECRET_KEY=$DJANGO_SECRET_KEY_BUILD
ENV DATABASE_URL=$DATABASE_URL_BUILD
ENV AI_BASE_INTERNAL=http://ai:8080
ENV MEDIAMTX_API_URL=http://mediamtx:9997
ENV MEDIAMTX_INTERNAL_RTSP_URL=rtsp://mediamtx:8554
ENV REDIS_URL=redis://redis:6379/0
ENV REDIS_HOST=redis
ENV REDIS_PORT=6379
ENV ALLOW_LOCALHOST_SERVICE_URLS=1
ENV DJANGO_DEBUG=0

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Collect static files (including Django admin CSS/JS)
RUN python manage.py collectstatic --noinput

# Production entrypoint: start the ASGI server only.
# Migrations should run as an explicit one-shot release task/service.
EXPOSE 8000
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "server.asgi:application"]
