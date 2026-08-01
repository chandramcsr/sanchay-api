#!/bin/sh
set -e

# trace-agent normally shells out to the full core `agent` binary to
# resolve its own hostname when it isn't told one -- a binary this
# image deliberately doesn't include (see the Dockerfile comment).
# RENDER_INSTANCE_ID is Render's own per-instance runtime identifier
# (auto-injected, not something set on the dashboard) -- exactly what
# distinguishes one running instance from another if this ever scales
# to more than one. Falls back to a static name for local dev /
# docker-compose, where that variable doesn't exist.
export DD_HOSTNAME="${RENDER_INSTANCE_ID:-sanchay-api-local}"

# Remote Configuration (Datadog pushing config changes to the agent
# without a redeploy) polls the core agent's local gRPC API for
# updates -- another thing the core `agent` binary would normally
# answer and this image doesn't include. Without this, trace-agent
# retries forever and logs an ERROR-level line every ~15-30s that
# looks alarming but changes nothing -- traces already send fine
# without it, and it's not a feature this setup uses anyway.
export DD_REMOTE_CONFIGURATION_ENABLED=false

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
