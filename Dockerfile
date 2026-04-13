FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BOT_DATA_DIR=/data

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY run_telegram_bot.py ./
COPY telegram_bot ./telegram_bot

RUN mkdir -p /data

HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
    CMD pgrep -f run_telegram_bot || exit 1

CMD ["python", "run_telegram_bot.py"]
