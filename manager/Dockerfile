FROM python:3.14-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 dufyn \
    && useradd --uid 10001 --gid dufyn --no-create-home --shell /usr/sbin/nologin dufyn

WORKDIR /opt/dufyn

RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/*

COPY --chmod=0644 requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY --chmod=0644 version.yaml ./version.yaml
COPY --chmod=0644 config.yaml ./config.yaml

RUN chmod -R a=rX /opt/dufyn/app

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
