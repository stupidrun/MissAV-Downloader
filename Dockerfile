FROM python:3.12-slim

WORKDIR /app

# Install ffmpeg and clean up
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml ./
COPY miyuki/ ./miyuki/

# Install project with dependencies
RUN pip install --no-cache-dir .

# Create default download directory
RUN mkdir -p /downloads

ENV MIYUKI_OUTPUT=/downloads
ENV MIYUKI_HOST=0.0.0.0
ENV MIYUKI_PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "miyuki.api:app", "--host", "0.0.0.0", "--port", "8000"]
