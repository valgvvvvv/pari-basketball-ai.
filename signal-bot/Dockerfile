FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app/signal-bot/requirements.txt
RUN pip install --no-cache-dir -r /app/signal-bot/requirements.txt && python -m playwright install --with-deps chromium
COPY monitor.py /app/signal-bot/monitor.py
RUN mkdir -p /app/data
CMD ["python", "-u", "/app/signal-bot/monitor.py"]
