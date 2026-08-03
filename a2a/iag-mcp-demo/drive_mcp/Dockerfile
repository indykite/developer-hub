FROM node:22-slim@sha256:f32b81066cde10a75dbac96646099533316d94bac4150c55da1636e1f0ffdc46

WORKDIR /app

# supergateway wraps a stdio MCP server and exposes it over Streamable HTTP.
# The Google Drive MCP server is vendored under ./vendor (the source of the
# archived @modelcontextprotocol/server-gdrive reference server) so the build
# depends only on its non-deprecated runtime deps, not the deprecated package.
# supergateway is pinned; bump deliberately.
RUN npm install -g supergateway@3.4.3 && npm cache clean --force

# Install the vendored Drive server and its runtime dependencies.
COPY vendor/ /app/vendor/
WORKDIR /app/vendor
RUN npm install --omit=dev && npm cache clean --force
WORKDIR /app

# Google OAuth material is mounted at runtime under /gdrive; the
# GDRIVE_OAUTH_PATH / GDRIVE_CREDENTIALS_PATH env vars pointing at it are set
# in docker-compose.yaml (see README).

# Run as a non-root user (trivy AVD-DS-0002)
RUN groupadd --system --gid 1001 app \
 && useradd --system --uid 1001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /gdrive && chown -R app:app /app /gdrive
USER app

EXPOSE 8000

# Healthcheck — supergateway serves "ok" on /healthz (trivy AVD-DS-0026)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["node", "-e", "require('http').get('http://127.0.0.1:8000/healthz', r => process.exit(r.statusCode === 200 ? 0 : 1)).on('error', () => process.exit(1))"]

# --stateful keeps one gdrive process per MCP session and issues Mcp-Session-Id
# headers, which the IAG passes through untranslated in both directions.
CMD ["supergateway", \
     "--stdio", "node /app/vendor/index.js", \
     "--outputTransport", "streamableHttp", \
     "--stateful", \
     "--port", "8000", \
     "--streamableHttpPath", "/mcp", \
     "--healthEndpoint", "/healthz"]
