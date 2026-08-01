FROM datadog/agent:7 AS dd-agent

FROM python:3.12-slim

WORKDIR /code

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc git \
    && rm -rf /var/lib/apt/lists/*

# Just the trace-agent binary + a starting config, lifted out of
# Datadog's own agent image -- not the full agent (no log tailing, no
# infra/process checks, no embedded Python collector). See start.sh
# for why this runs as a second process here instead of a separate
# Render private service.
COPY --from=dd-agent /opt/datadog-agent/embedded/bin/trace-agent /opt/datadog-agent/embedded/bin/trace-agent
COPY --from=dd-agent /etc/datadog-agent/datadog.yaml.example /etc/datadog-agent/datadog.yaml

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY start.sh .
RUN chmod +x start.sh

EXPOSE 8000
CMD ["./start.sh"]
