FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

# Run as a non-root user (trivy AVD-DS-0002)
RUN groupadd --system --gid 1001 app \
 && useradd --system --uid 1001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && chown -R app:app /app
USER app

EXPOSE 8090

# Healthcheck (trivy AVD-DS-0026): the bridge answers /healthz once it has loaded its settings.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('BRIDGE_PORT','8090'), timeout=3)" || exit 1

CMD ["python", "server.py"]
