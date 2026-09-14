# Lost Movies — server edition 2.2.1

The supplied welcome video is now included. See DOCKER_IMAGE.md for building or downloading a Docker image through GitHub Actions. This supersedes references below to media being excluded.

Telegram bot and password-protected control webpage for a continuously running Linux server with Docker Engine and Docker Compose v2. GitHub stores the source; a server runs the app. GitHub Pages and hosting that sleeps between requests cannot run this polling edition reliably.

Retains 4KHDHub, HDHub4u, RogMovies, GokuHD, VegaMovies and Kuroiru (`/anime`). Plain text and `/search` search the five movie sources. MoviesMod and MoviesLeech remain removed. Source availability depends on the external hosts; changing servers does not guarantee a fix for HTTP 403.

## Version provenance

Based on the locally tested 2.0 release, including FastDL-to-VCloud fallback and web controls. Later changes reported by Replit Agent were not supplied as source files and are NOT included. Preserve your Replit export and production database before migrating. A new bundled database starts with fresh settings; no production state is bundled.

## Start on a server

Install Docker Engine with Compose v2 using https://docs.docker.com/engine/install/ for your operating system. Extract this project or clone your GitHub repository, then:

```sh
cp .env.example .env
chmod 600 .env
```

Edit `.env`: provide BOT_TOKEN, your numeric Telegram OWNER_IDS (comma-separated for multiple owners), WEB_CONTROL_PASSWORD of at least 16 characters, and a random hexadecimal POSTGRES_PASSWORD (`openssl rand -hex 32`). Never commit `.env`. Rotate the bot token previously shared in chat through BotFather before deploying. Optional welcome video: `assets/dear.mp4`.

```sh
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 bot
```

Stop your previous bot instance before starting this one: only ONE worker may poll a token. Do not scale the bot service above one replica. PostgreSQL is private to the Docker network; named volumes persist data across container replacement. The app waits for the database health check before starting.

The page is bound to the server's loopback interface. To use it without a domain, run this on your own computer and keep the connection open:

```sh
ssh -L 8080:127.0.0.1:8080 YOUR_USER@YOUR_SERVER
```

Open http://localhost:8080 and sign in. On/off controls affect Telegram polling and active jobs; the page stays running. Off survives restart through the database. Telegram may deliver queued messages when you switch back on. Website sessions expire after eight hours or app restart. Missing/short control passwords disable login.

## Public HTTPS webpage

Set DOMAIN in `.env` to a hostname you control. Point its DNS records at the server and allow inbound TCP 80 and 443. Then:

```sh
docker compose --profile https up -d --build
```

Open `https://YOUR_DOMAIN`. Caddy obtains and renews the certificate. If you already run a reverse proxy, leave the https profile off and proxy your domain to `127.0.0.1:8080`, preserving the Host header. Public login requires HTTPS because its session cookie is Secure. Do not expose port 8080 directly as a public HTTP login.

## Updates and restart

Back up the database before updating. Pull the new code, then rebuild:

```sh
git pull --ff-only
docker compose --profile https up -d --build
```

Omit `--profile https` when not using Caddy. Changed environment values need `up -d` to recreate containers; `restart` alone does not apply them. Do not casually change POSTGRES_PASSWORD after the database is initialized: changing `.env` does not change the stored database password.

`docker compose stop bot` stops the bot process and its webpage. Use the webpage's Off control to keep the webpage available. Restart policies restart crashed containers, but an unhealthy health check alone does not restart a running container. Telegram status is separate from web health; inspect the control page for a stopped Telegram worker.

## Backup and restore (Linux shell)

```sh
mkdir -p backups
chmod 700 backups
docker compose exec -T db pg_dump -U lostmovies -d lostmovies -Fc > backups/lostmovies.dump
```

Verify the command succeeded and copy backups off the server. Store `.env` securely too. To restore a known-good backup into a running database, stop writes first (this replaces the existing bot tables):

```sh
docker compose stop bot
docker compose exec -T db pg_restore -U lostmovies -d lostmovies --clean --if-exists --no-owner < backups/lostmovies.dump
docker compose start bot
```

Do not run `docker compose down -v`: it removes persistent volumes. Migrating the existing Replit database requires a separate export/restore; this package does not access that database.

## GitHub

Create an empty repository under your account. From this extracted folder:

```sh
git init
git add .
git diff --cached --stat
git commit -m "Add portable Telegram bot deployment"
git branch -M main
git remote add origin YOUR_GITHUB_REPOSITORY_URL
git push -u origin main
```

Check the staged files before committing. Secrets, databases and media are excluded. The included GitHub Actions workflow runs unit tests and builds the standard Docker image; it does not deploy or use a Telegram token. Repository publication is not performed by creating this archive. No redistribution license is assigned for third-party code or media; review ownership and licensing before making a public release.

## Verification and optional browser

Run `python -m pytest -q` after installing requirements.txt and requirements-dev.txt. Test the deployed control page and a Telegram request yourself. Unit tests use fixtures and mocks and do not certify live provider access. Docker is unavailable in the preparation environment, so the image and PostgreSQL stack must be built and smoke-tested on your server or in GitHub Actions.

Set BROWSER_ENABLED=true and rebuild to include Chromium if your configured providers require it. Browser support is not a promise of access to a blocking host. It uses more memory and increases image size.

References: https://docs.docker.com/compose/how-tos/startup-order/ and https://caddyserver.com/docs/quick-starts/reverse-proxy

## Automatic recovery update

See RECOVERY_GUIDE.md for the changes, owner commands and upgrade limits.
