# OPTIONAL alternative to the venv install in DEPLOY.md, which is the
# supported path. Kept for containerized deployments; if you use this,
# tagent-run must be adapted to `docker run` (it currently calls the venv).
#
# One-shot invocation image. The container starts, runs a single cycle, writes
# everything it learned to the mounted volume, and exits. Nothing is held in
# memory between cycles - that is what makes the agent's knowledge durable.
FROM python:3.11-slim

RUN useradd -m -u 10001 tagent
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# The token file must stay READ-WRITE: the broker client re-encrypts refreshed
# OAuth tokens back to this path. Mounting it read-only breaks auth silently.
VOLUME ["/data"]
ENV TAGENT_DB=/data/tagent.db

USER tagent
ENTRYPOINT ["tagent"]
CMD ["cycle"]
