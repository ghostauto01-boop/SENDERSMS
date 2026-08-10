# ============================================================
# SendSMS - Multi-stage Dockerfile
# ============================================================

# ---------- Backend Stage ----------
FROM python:3.12-slim AS backend

WORKDIR /app

# Install system dependencies for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy backend source code
COPY backend/ ./backend/

WORKDIR /app/backend

# Default command (uvicorn web server)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


# ---------- Celery Worker Stage ----------
FROM backend AS worker

WORKDIR /app/backend

CMD ["celery", "-A", "app.tasks.celery_app", "worker", "--loglevel=INFO"]


# ---------- Frontend Build Stage ----------
FROM node:22-alpine AS frontend-build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json vite.config.ts tailwind.config.js postcss.config.js ./
COPY frontend/ ./frontend/

RUN npm run build


# ---------- Production Nginx Stage ----------
FROM nginx:alpine AS frontend

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /app/frontend/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
