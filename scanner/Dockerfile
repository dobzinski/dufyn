FROM python:3.14-slim-trixie@sha256:82abc9f53600280b0757126f83c5a7d47088592d2acb6b4c0b9e97f76865508c
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LC_ALL=C
RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping nmap && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/dufyn
COPY --chmod=0644 requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
COPY --chmod=0644 version.yaml ./version.yaml
COPY --chmod=0644 config.yaml ./config.yaml
CMD ["python", "-m", "app.main"]
