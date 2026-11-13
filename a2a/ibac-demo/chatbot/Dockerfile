FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

WORKDIR /app

# Prevent Python from writing pyc files
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user (trivy AVD-DS-0002); the session store holds
# authenticated user tokens, so it is owned by that user and closed to others
RUN groupadd --system --gid 1001 app \
 && useradd --system --uid 1001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /tmp/flask_session \
 && chown -R app:app /app /tmp/flask_session \
 && chmod 700 /tmp/flask_session
USER app

# The container always listens on 3300; the published host port is chosen in
# compose (CHATBOT_PORT), so the gateways' static audit webhook stays valid.
EXPOSE 3300

# Healthcheck — verify the Flask port is accepting connections (trivy AVD-DS-0026)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import socket,sys; s=socket.socket(); s.settimeout(3); s.connect(('127.0.0.1', 3300)); s.close()" || exit 1

CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "3300"]
