FROM python:3.11-slim

WORKDIR /app

# System deps for OpenCV, torch, and audio (ffmpeg + libsndfile1 for BEATs pipeline)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    ffmpeg \
    libsndfile1 \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create required runtime dirs
RUN mkdir -p evidence data/staging_uploads data/entity_store logs

# Use the Docker-specific camera config (RTSP from MediaMTX)
RUN cp configs/cameras.docker.yaml configs/cameras.yaml

EXPOSE 8080
ENV PYTHONUNBUFFERED=1

# Start the AI module — runs FastAPI on port 8080
CMD ["python", "run.py"]
