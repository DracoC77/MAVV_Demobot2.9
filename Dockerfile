FROM python:3.12-slim

LABEL maintainer="MAVV Gaming Group"
LABEL description="MAVV Demobot 2.9 — Discord Game Night Voting Bot"

# Don't buffer stdout/stderr so logs appear immediately
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot source
COPY bot/ bot/

# Data directory for SQLite (mount as volume)
RUN mkdir -p /app/data

VOLUME /app/data

CMD ["python", "-m", "bot.main"]
