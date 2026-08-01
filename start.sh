#!/bin/sh
set -e

# trace-agent (not the full Datadog Agent) runs as a second process in
# this same container -- one paid Render service instead of two.
# Logs and DB metrics are already covered agentlessly (Render Log
# Streams, Neon's own Datadog integration), so APM/trace collection is
# the only piece that ever needed an agent process at all.
#
# Safe with DD_API_KEY unset: trace-agent starts and listens on 8126
# regardless, it just has nowhere real to forward to -- same
# documented no-op-until-configured shape as ddtrace-run itself.
/opt/datadog-agent/embedded/bin/trace-agent -config /etc/datadog-agent/datadog.yaml &

alembic upgrade head
exec ddtrace-run uvicorn app.main:app --host 0.0.0.0 --port 8000
