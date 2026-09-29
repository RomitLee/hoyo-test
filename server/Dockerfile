FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv/hoyo-relay

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && addgroup --system --gid 10001 relay \
    && adduser --system --uid 10001 --gid 10001 --home /nonexistent relay \
    && mkdir -p /data \
    && chown relay:relay /data

COPY app ./app

USER relay
EXPOSE 8000
VOLUME ["/data"]

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=*"]
