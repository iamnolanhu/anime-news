# Deploy: your-site.example

Public deploy on the deploy host (Docker behind the shared Traefik, Let's Encrypt resolver `le`).

| What | Where |
| --- | --- |
| Public URL | https://your-site.example (gallery: `/episodes`) |
| Code on the box | `/root/shot-director/app` (rsynced copy of this repo, no `.git`) |
| Env file | `/root/shot-director/.env` (mode 600): `COMFY_API_KEY`, `SHOT_DIRECTOR_ACCESS_TOKEN`, `SHOT_DIRECTOR_PUBLIC_URL` |
| Token copy on the Mac | `~/.claude/.secrets/services/comfy/shot-director-deploy.env` (mode 600) |
| Container / volume | `shot-director` / `shot-director-media` mounted at `/data/media` (frames, shots, episodes, `episodes.json`) |
| Limits | 1 GB RAM, 1.5 CPU, 512 pids; healthcheck on `http://127.0.0.1:8000/api/presets` |

## Who can do what

- Anyone: the studio page, `/episodes`, `GET /api/*`, `/media/*`.
- Token holders only (`X-Access-Token: <token>` or `?token=<token>`): every POST/DELETE. Without it: `401 {"error":"access token required"}`. The UI asks for it once and keeps it in localStorage.
- Rate limit per token, rolling hour, in-process (resets on restart): `SHOT_DIRECTOR_EPISODES_PER_HOUR` (default 6, every `/api/episode` call counts, including `render:false`) and `SHOT_DIRECTOR_CALLS_PER_HOUR` (default 60, frame generation and shots). Over the limit: `429`.

## Redeploy

```bash
# from the repo root on the Mac
rsync -az --delete --exclude .git --exclude .venv --exclude media --exclude .env --exclude '*.env' \
  --exclude .worktrees --exclude .pytest_cache ./ deploy-host:/root/shot-director/app/
ssh deploy-host 'cd /root/shot-director && docker compose -f app/deploy/compose.yml -p shot-director up -d --build'
```

## Rotate the access token

Generate a new one, write it to both files without printing it, recreate the container:

```bash
NEW=$(openssl rand -hex 16)
ssh deploy-host "sed -i 's/^SHOT_DIRECTOR_ACCESS_TOKEN=.*/SHOT_DIRECTOR_ACCESS_TOKEN=$NEW/' /root/shot-director/.env && cd /root/shot-director && docker compose -f app/deploy/compose.yml -p shot-director up -d --force-recreate"
sed -i '' "s/^SHOT_DIRECTOR_ACCESS_TOKEN=.*/SHOT_DIRECTOR_ACCESS_TOKEN=$NEW/" ~/.claude/.secrets/services/comfy/shot-director-deploy.env
unset NEW
```

Browsers holding the old token get a 401 and are asked for the new one.

## Copy a finished episode into the public gallery

The gallery reads `episodes.json` in the media volume; each entry's `url` is `/media/<file>`.

```bash
scp media/episodes/episode-XXXX.mp4 deploy-host:/tmp/
ssh deploy-host 'docker cp /tmp/episode-XXXX.mp4 shot-director:/data/media/ && rm /tmp/episode-XXXX.mp4'
```

Then merge its ledger entry into `/data/media/episodes.json` in the container (drop the Mac-only `path` / `raw_path` fields). Episodes rendered on the box itself land there automatically.

## DNS first (Traefik gotcha)

Add the A record before the first `compose up`:

```bash
ssh deploy-host 'cd /root/namecheap_scripts && python3 add_subdomain.py --domain your-site.example --ip 203.0.113.10 --dry-run'
# then the same without --dry-run; wait for: dig +short your-site.example @1.1.1.1
```

Traefik orders the certificate when the container starts and does not retry after an NXDOMAIN. If the site serves Traefik's default certificate, run `ssh deploy-host docker restart traefik-traefik-1` once DNS resolves.
