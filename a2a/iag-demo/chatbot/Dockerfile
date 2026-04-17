FROM python:3.11-slim

WORKDIR /app

# Prevent Python from writing pyc files
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /tmp/flask_session && chmod 777 /tmp/flask_session

# Run as a non-root user (trivy AVD-DS-0002)
RUN groupadd --system --gid 1001 app \
 && useradd --system --uid 1001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && chown -R app:app /app
USER app

EXPOSE 3000

# Healthcheck — verify the Flask port is accepting connections (trivy AVD-DS-0026)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import socket,os,sys; s=socket.socket(); s.settimeout(3); s.connect(('127.0.0.1', int(os.environ.get('CHATBOT_PORT','3000')))); s.close()" || exit 1

CMD ["python", "app.py"]
