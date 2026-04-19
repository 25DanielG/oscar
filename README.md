# oscar

Monitors the OSCAR (Georgia Tech) for open seats and registers you the instant one appears. Polls Banner every ~20 seconds. When a seat or waitlist spot opens, it fires a Pushover notification and attempts registration automatically. If you're major-restricted, it keeps retrying silently every poll until the restriction lifts. Auth runs on your laptop once a week to bypass Duo. The monitor is meant to be run on a VPS 24/7.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp config.example.yaml config.yaml
cp .env.example .env
```

Edit `config.yaml` with the term and CRNs. Fill in pushover keys in `.env` and an optional VPS.

Run auth on your laptop:
```bash
oscar auth refresh --headed
```

## Commands

```
oscar monitor              start the polling loop
oscar status               session health + watched CRNs
oscar history <CRN>        seat availability history and sparkline for a CRN
oscar check-crn <CRN>      live seat count for a CRN
oscar register-now <CRN>   attempt registration immediately
oscar dry-run <CRN>        simulate registration without submitting
oscar add <CRN>            add CRN to config.yaml
oscar remove <CRN>         remove CRN from config.yaml
oscar auth refresh         headless session refresh
oscar auth refresh --headed  manual re-auth (use when session expired)
oscar auth status          show cookie expiry times
oscar logs                 tail the structured log
```

## Deploy with Docker

```bash
# on repo in VPS
docker compose -f deploy/docker-compose.yml --project-directory . up -d
docker compose -f deploy/docker-compose.yml --project-directory . logs -f
```

```bash
# to close docker container
docker compose -f deploy/docker-compose.yml --project-directory . down
```

Need a `session.json` and `.env` in the VPS repo root before starting.

## Weekly Auth Refresh

Run this locally to refresh the session and upload cookies to the VPS:

```bash
./scripts/refresh_auth.sh
```

Requires `VPS_HOST` set in `.env`. Tries headless first, falls back to headed browser if session expired.

## Config

```yaml
term: "202608" # YYYYMM = Fall 2026

crns:
  - crn: "12345"
    label: "CS 3511 - Algorithms Honors"
  - crn: "67890"
    label: "CS 4400 - Databases"
    retry_on_restriction: true # keep retrying silently if major-restricted
poll:
  base_interval: 20 # poll interval, dont go under 10
  jitter: 10 # random polling variance
dry_run: false # true = full pipeline but no actual class registration (only notifications)
```
