# Development toolchain only. Application source and credentials are never baked in.
FROM node:24.20.0-bookworm-slim@sha256:6642ef280aebc09c4541bee0b15c9f89f0f3f3c247ddee79ae1d37eddfdcbbaa
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates python3 make g++ git openssl \
    && rm -rf /var/lib/apt/lists/*
RUN npm install --global --ignore-scripts pnpm@11.28.2
ENV HUSKY=0 PNPM_HOME=/tmp/pnpm
USER 1000:1000
WORKDIR /app
