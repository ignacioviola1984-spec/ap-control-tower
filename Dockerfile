# AP Control Tower - imagen de la demo (patron del repo publico de demos CFO)
# Sin puertos hardcodeados: Cloud Run inyecta PORT; local se pasa -e PORT=...
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# requirements primero: cache de capas para builds rapidos.
# La misma imagen sirve la Demo y "Prueba con tus facturas" (AP_APP_MODE).
# Gmail (solo lectura) se incluye en la imagen; las credenciales NO (van por env).
COPY requirements.txt requirements-gmail.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-gmail.txt

# commit hash para el audit trail (envutil.resolve_commit lee GIT_COMMIT)
ARG GIT_COMMIT=sin-git
ENV GIT_COMMIT=${GIT_COMMIT}

COPY . .

# AP_DEMO_PASSWORD se inyecta en runtime (nunca en la imagen ni en el repo)
CMD ["sh", "-c", "streamlit run app.py --server.port=${PORT:-8501} --server.address=0.0.0.0 --server.headless=true --browser.gatherUsageStats=false"]
