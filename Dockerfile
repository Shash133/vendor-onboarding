# FastAPI backend image for Fly.io (and any container host).
# Repo root is vendor_onboarding/, so build context = repo root.

FROM python:3.11-slim

# Faster, cleaner Python in containers.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source.
COPY . .

# Fly routes to this internal port (see fly.toml [http_service].internal_port).
EXPOSE 8080

# Bind 0.0.0.0 so the service is reachable from outside the container.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080"]
