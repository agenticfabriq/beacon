# Beacon Workers Deployment

Four independent worker processes use one image:

| Worker | Port | Schedule | Notes |
| --- | --- | --- | --- |
| `beacon-worker-promotion` | 9101 | hourly | Extracts candidate eval items. |
| `beacon-worker-convergence` | 9102 | continuous | Promotes on SUT agreement. |
| `beacon-worker-antigoodhart` | 9103 | every 5 min | Scans registry leakage and skew. |
| `beacon-worker-retention` | 9104 | daily | Summarizes and deletes old raw trace blobs. |

## Docker Compose

```bash
docker compose -f docker-compose.yml \
  -f deploy/workers/docker-compose.workers.yml up -d
```

Check health:

```bash
for port in 9101 9102 9103 9104; do
  curl -s "http://127.0.0.1:${port}/healthz"
  echo
done
```

## Kubernetes

Run each worker as a separate Deployment with `replicas: 1` and a liveness
probe on `/healthz` for that worker's port. Worker writes are idempotent through
Postgres watermarks, but duplicate replicas add unnecessary load.

## Bare Metal

Use the systemd unit files in `deploy/workers/systemd/` once Task 20 lands.
