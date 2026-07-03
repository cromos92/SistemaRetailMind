FROM python:3.11-slim

# Zona horaria del contenedor (defensa en profundidad: si algún punto del
# código todavía usa datetime.now()/date.today() naive, retorne hora Chile).
ENV TZ=America/Santiago

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    pkg-config \
    default-libmysqlclient-dev \
    libffi-dev \
    libpq-dev \
    zlib1g-dev \
    libjpeg-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libwebp-dev \
    tcl8.6-dev \
    tk8.6-dev \
    python3-tk \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements-railway.txt .
RUN pip install --no-cache-dir -r requirements-railway.txt

# Copy project
COPY . .

# Change to project directory
WORKDIR /app/retailmind

# Create staticfiles directory
RUN mkdir -p staticfiles

# Collect static files (skip missing files)
RUN python manage.py collectstatic --noinput --ignore="*.map" || echo "Static files collection completed with warnings"

# Expose port
EXPOSE 8000

# Start command — workers/threads/timeout ajustables por env sin rebuild.
# El default anterior era 1 worker síncrono: un reporte pesado congelaba el
# POS de todas las sucursales. 2 workers × 2 threads es conservador en RAM;
# subir GUNICORN_WORKERS según el plan de la instancia.
CMD gunicorn retailmind.wsgi --bind 0.0.0.0:8000 \
    --workers ${GUNICORN_WORKERS:-2} \
    --threads ${GUNICORN_THREADS:-2} \
    --timeout ${GUNICORN_TIMEOUT:-60} \
    --log-file -
