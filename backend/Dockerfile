# Backend image for Render / Railway / any Docker host.
# Includes ffmpeg, which is required for clip cutting and caption burning.

FROM python:3.11-slim

# ffmpeg is not bundled with python:slim, install it from apt
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/storage

ENV PORT=5000
EXPOSE 5000

CMD gunicorn --bind 0.0.0.0:5000 --workers 1 --threads 4 --timeout 600 --access-logfile - --error-logfile - app:app
