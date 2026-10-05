FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MPLBACKEND=Agg

# шрифт DejaVu нужен matplotlib для кириллицы на графиках
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY signalbot ./signalbot
COPY config.yaml .
COPY assets ./assets

# SQLite хранится в /app/data: монтируйте том, чтобы статистика переживала перезапуски
VOLUME ["/app/data"]
CMD ["python", "-m", "signalbot.main"]
