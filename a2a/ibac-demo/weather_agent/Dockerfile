FROM python:3.15-slim@sha256:08a4c22c92b79ec821a7ad49768480039ee5b7accac3955228e75366e30abc8e

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user (trivy AVD-DS-0002)
RUN groupadd --system --gid 1001 app \
 && useradd --system --uid 1001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && chown -R app:app /app
USER app

EXPOSE 6004

# Healthcheck — verify the A2A port is accepting connections (trivy AVD-DS-0026)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import socket,os; s=socket.socket(); s.settimeout(3); s.connect(('127.0.0.1', int(os.environ.get('WEATHER_PORT','6004')))); s.close()" || exit 1

CMD ["python", "weather_agent.py"]
