FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV DB_PATH=runtime/trades.db

RUN mkdir -p runtime/logs runtime/reports runtime/backups

CMD ["python", "main.py"]
