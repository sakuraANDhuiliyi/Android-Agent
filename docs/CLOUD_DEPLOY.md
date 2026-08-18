# Cloud deployment (optional)

Single-machine SQLite remains the default. Multi-instance is opt-in and **fails closed** if required URLs are missing.

## Modes

| `deployment_mode` | Durable state | Tickets / rate limits | Extra requirements |
|---|---|---|---|
| `sqlite` (default) | SQLite files under `AGENT_DATA_DIR` | Same SQLite (`ws_tickets`, `rate_windows`, `outbox`) | None |
| `postgres` | `database_url` (PostgreSQL) | Shared SQL or the migration dump | `database_url` |
| `hybrid` | `database_url` | Redis (`redis_url`) | both URLs |

`artifact_backend: object` also requires `object_store_url` (`s3://`, `file://`, or `memory://` for tests).

Environment overrides: `AGENT_DEPLOYMENT_MODE`, `AGENT_DATABASE_URL`, `AGENT_REDIS_URL`, `AGENT_ARTIFACT_BACKEND`, `AGENT_OBJECT_STORE_URL`.

## TLS and reverse proxy

Terminate TLS at the proxy. The Python process can stay on loopback.

Example Caddy:

```caddy
agent.example.com {
  reverse_proxy 127.0.0.1:8000
}
```

Example nginx:

```nginx
server {
  listen 443 ssl;
  server_name agent.example.com;
  ssl_certificate     /etc/ssl/certs/agent.crt;
  ssl_certificate_key /etc/ssl/private/agent.key;
  location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header Authorization $http_authorization;
    client_max_body_size 32m;
  }
}
```

Set `cors_allowed_origins` to the exact HTTPS origins. Do not use `*`. Android clients talk HTTPS directly and ignore CORS.

## Persistent volumes

Mount a single volume for:

- `AGENT_DATA_DIR` (default `./data`) — `users.db`, `agent.db`, WAL files
- `workspaces/`
- `builds/` (APK + build logs when `artifact_backend=local`)

Two API processes and two workers may share that volume in `sqlite` mode. WebSocket tickets are one-time rows in SQLite (`BEGIN IMMEDIATE`), so two API instances cannot both consume the same ticket.

## Backup and restore

```bash
# SQLite backup
python3 scripts/migrate_db.py --backup --data-dir /var/lib/android-agent

# Snapshot workspaces and builds together with the DB files
rsync -a /var/lib/android-agent/ /backup/android-agent/

# SQLite → PostgreSQL dry-run (counts + id hashes)
python3 scripts/migrate_sqlite_to_postgres.py --data-dir /var/lib/android-agent

# Write Postgres INSERT SQL + re-hash after dump
python3 scripts/migrate_sqlite_to_postgres.py --data-dir /var/lib/android-agent --apply --backup

# Rollback SQLite files from the migration backup
python3 scripts/migrate_sqlite_to_postgres.py --data-dir /var/lib/android-agent --rollback
```

Verify `row_total`, per-table counts, and `aggregate_hash` in `pg-migration/manifest.json` before switching `deployment_mode`.

## Process layout

1. Reverse proxy (TLS)
2. Two API processes (`python3 -m agent serve`) behind the proxy
3. Two workers (same binary; they claim tasks with lease + `claim_token` fencing)
4. Optional Redis for `hybrid` tickets/rate limits
5. Optional object storage for APK/logs

Slow tasks keep the lease via heartbeat using **SQLite server time** (`strftime('%s','now')`). A second worker cannot steal a live lease; a stale `claim_token` cannot heartbeat after takeover.

## Health

`GET /api/health` should be the proxy health check. Keep `registration_enabled: false` unless you also set `registration_token`.
