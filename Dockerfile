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

# trace-agent normally shells out to the full core `agent` binary to
# resolve its own hostname when it isn't told one -- a binary we
# deliberately didn't copy in (see the comment above). Setting this
# explicitly skips that fallback entirely instead of needing the full
# agent just to answer one question trace-agent otherwise asks it.
ENV DD_HOSTNAME=sanchay-api

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY start.sh .
RUN chmod +x start.sh

EXPOSE 8000
CMD ["./start.sh"]
