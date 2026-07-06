# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 – builder
#
# Official Rust image. git + openssh-client are needed because the shared crates
# (kls-web-core / kls-ui / percy-client) are pulled from the PRIVATE
# klappstuhlpy/klappstuhl_me-shared repo as a tag-pinned SSH git dependency.
# .cargo/config.toml sets net.git-fetch-with-cli so cargo uses the git CLI + SSH.
# TLS is rustls (no OpenSSL) and SQLite is bundled, so no libssl/pkg-config here.
# ─────────────────────────────────────────────────────────────────────────────
FROM rust:1.88-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Seed github.com's host key so the SSH fetch doesn't fail strict host
# verification, then forward an SSH key at build time via BuildKit's ssh mount.
# Build with:  DOCKER_BUILDKIT=1 docker compose build   (BuildKit is default on
# modern Docker; the invoking shell's SSH agent must hold a key with read access
# to klappstuhlpy/klappstuhl_me-shared).
RUN mkdir -p -m 0700 ~/.ssh && ssh-keyscan github.com >> ~/.ssh/known_hosts 2>/dev/null

# BuildKit cache mounts persist the cargo registry/git checkouts and target/ dir
# across builds (incremental recompiles on a `git pull`). Because target/ is a
# cache mount (not a layer), copy the finished binary out before the RUN ends.
COPY . .
RUN --mount=type=ssh \
    --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/build/target \
    cargo build --release \
 && cp target/release/dashboard /build/dashboard


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 – runtime
#
# Minimal Debian. ca-certificates is the only runtime dependency — it validates
# the TLS chain for the dashboard's outbound HTTPS calls (Discord OAuth, and the
# optional klappstuhl.me /api/v1/scan). SQLite is compiled into the binary; Askama
# templates and the SQL migrations are embedded at build time. Only the runtime
# `static/` assets (served by tower_http's ServeDir) are copied in.
# ─────────────────────────────────────────────────────────────────────────────
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Binary + the runtime static assets (served at /static/*; /kls/* is embedded).
COPY --from=builder /build/dashboard ./dashboard
COPY static/ ./static/

# ── Persistent data layout ────────────────────────────────────────────────────
# The dashboard resolves its config + database via XDG dirs, rooted under /data:
#
#   /data/config/percy-dashboard/config.json   ← application config (auto-created)
#   /data/data/percy-dashboard/dashboard.db     ← SQLite database (owned tables)
#
# Override either explicitly with DASHBOARD_CONFIG / DASHBOARD_DB if preferred.
ENV XDG_CONFIG_HOME=/data/config \
    XDG_DATA_HOME=/data/data

VOLUME ["/data"]

# Default listen port (the app binds 127.0.0.1:8091 unless config.bind says
# otherwise). Under host networking this is reachable on the host directly.
EXPOSE 8091

CMD ["./dashboard"]
