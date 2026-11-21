FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN sed -i 's/\r$//' /app/scripts/*.sh \
    && chmod +x /app/scripts/*.sh \
    && DJANGO_SETTINGS_MODULE=dayleey.settings.dev python manage.py collectstatic --noinput \
    && useradd --create-home --shell /bin/sh dayleey \
    && chown -R dayleey:dayleey /app

USER dayleey

ENV DJANGO_SETTINGS_MODULE=dayleey.settings.prod

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz/', timeout=3)" || exit 1

CMD ["/app/scripts/start_web.sh"]
