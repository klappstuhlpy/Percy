# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 – builder
#
# Official Rust image. git is needed because the shared crates (kls-web-core /
# kls-ui / percy-client) are pulled from the public klappstuhlpy/kls-core repo as
# a tag-pinned HTTPS git dependency — no credentials required.
# TLS is rustls (no OpenSSL) and SQLite is bundled, so no libssl/pkg-config here.
# ─────────────────────────────────────────────────────────────────────────────
FROM rust:1.88-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# BuildKit cache mounts persist the cargo registry/git checkouts and target/ dir
# across builds (incremental recompiles on a `git pull`). Because target/ is a
# cache mount (not a layer), copy the finished binary out before the RUN ends.
COPY . .
RUN --mount=type=cache,target=/usr/local/cargo/registry \
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
