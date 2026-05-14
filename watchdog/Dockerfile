FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY watchdog.py .

# /data is the mount point for the persistent SQLite volume
VOLUME ["/data"]
ENV DB_PATH=/data/panamacompra_tenders.db

CMD ["python", "watchdog.py"]
