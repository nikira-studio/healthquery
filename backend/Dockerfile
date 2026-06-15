FROM --platform=$BUILDPLATFORM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DB_PATH=/app/data/healthquery.db
ENV HEALTHQUERY_LOG_LEVEL=INFO

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 1001 --shell /bin/bash healthquery \
    && mkdir -p /app/data \
    && chown -R healthquery:healthquery /app

USER 1001:1001

EXPOSE 3136

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3136"]
