# Validation sandbox for LLM-generated code.
#
# Everything the seed suite needs is installed HERE, at build time, with
# network. At run time the container is started with --network=none, so a
# dependency missing from this image cannot be fetched - it surfaces as an
# execution error instead (Principle II, and the clarified no-network decision).
#
# There is deliberately no ENTRYPOINT: the command comes from the sandbox
# adapter, which owns the isolation flags.

FROM python:3.12-slim

RUN pip install --no-cache-dir "pytest==8.3.4"

# Non-root. The workspace is bind-mounted at /work and is the only writable path.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin runner
USER runner

WORKDIR /work
