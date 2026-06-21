FROM --platform=$BUILDPLATFORM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DB_PATH=/app/data/healthquery.db
ENV HEALTHQUERY_LOG_LEVEL=INFO

# The build context for this image is the repo root (see docker-compose.yml),
# not just ./backend, so the in-tree healthquery_client package can be
# installed alongside the api.
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install the in-tree healthquery-client package so the api (and any
# spawned MCP subprocess) can `import healthquery_client` without relying
# on PYTHONPATH manipulation.
COPY healthquery_client ./healthquery_client
RUN pip install --no-cache-dir ./healthquery_client

COPY backend/ ./

RUN useradd --create-home --uid 1001 --shell /bin/bash healthquery \
    && mkdir -p /app/data \
    && chown -R healthquery:healthquery /app

USER 1001:1001

EXPOSE 3136

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3136"]