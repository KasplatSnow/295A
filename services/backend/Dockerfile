FROM python:3.11-slim

WORKDIR /app

ARG DJANGO_SECRET_KEY_BUILD=docker-build-only-secret
ENV DJANGO_SECRET_KEY=$DJANGO_SECRET_KEY_BUILD
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
