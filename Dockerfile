FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir flask

COPY app.py .
COPY wireless_monitor.py .

EXPOSE 514/udp
EXPOSE 8088/tcp

VOLUME ["/app/data"]

ENV DB_PATH=/app/data/wireless_stats.db
ENV LOG_PATH=/app/data/wireless_service.log

CMD ["python", "app.py"]
