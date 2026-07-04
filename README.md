# percy-dashboard

The web dashboard for configuring **[Percy](https://percy.klappstuhl.me)** — a standalone Axum binary that runs **independently out of the box** and, optionally, connects to the [klappstuhl.me](https://github.com/klappstuhlpy/klappstuhl_me) main site for a shared login.

It shares its design system, auth-cookie format, and Percy API client with the main site via the [`klappstuhl_me-shared`](https://github.com/klappstuhlpy/klappstuhl_me-shared) crates (a tag-pinned git dependency), so the two apps stay visually and behaviourally consistent without a common monolith.

> **Status: skeleton.** This is the Phase 4c/5 scaffold from the dashboard decoupling effort — it boots, serves the shared design assets, and health-checks the kernel database. The real dashboard routes (guild config, members, leveling, …) are ported from the klappstuhl.me monolith in later phases.

## Running

```bash
cargo run                      # serves on http://127.0.0.1:8091
DASHBOARD_PORT=9000 cargo run  # override the port
```

Its only hard runtime dependency is Percy's internal API, and even that is optional at boot — with no `PERCY_API_URL` set the server still starts.

| Env var | Purpose |
|---------|---------|
| `DASHBOARD_PORT` | Listen port (default `8091`). |
| `PERCY_API_URL` | Base URL of Percy's internal API. Unset → the dashboard runs without a Percy connection. |
| `PERCY_API_TOKEN` | Bearer token for Percy's internal API. |

## Routes (skeleton)

- `GET /` — landing page on the shared stylesheet.
- `GET /health` — liveness, backed by a live kernel database.
- `GET /api-info` — reports whether the Percy client is configured.
- `GET /kls/*` — the shared design assets (`base.css`, `base.js`, `timestamps.js`).

## Development

```bash
cargo build && cargo test
cargo fmt && cargo clippy
```

Fetching the private `klappstuhl_me-shared` dependency requires an SSH key with read access to that repo; `.cargo/config.toml` sets `net.git-fetch-with-cli = true` so SSH auth works reliably.
