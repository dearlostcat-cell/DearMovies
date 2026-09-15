# Catalogue fixes — v2.2.3

88 automated tests passed. This release retains the existing sites and v2.2.2 mirror fixes.

## Fixed

- The supplied HDHub4u India's Got Latent Season 2 episode index now returns 11 entries: episodes 1–7, three bonus episodes and a bonus clip. WATCH links are excluded. Unspecified quality remains Unknown. Special entries stay separate from regular episode numbers.
- Article parser/configuration failures are counted as failures but no longer increment the outage streak or start a whole-site cooldown. HTTP 403, timeouts and service unavailability still can cause cooldown.
- Search API failures now log their reason and message. Detail failures log stage, title and message. Site test messages include the site name and job ID, and retain an error log.

The supplied RogMovies detail page already parsed into six options using the existing parser. No new RogMovies parser was necessary. Its canonical domain is rogmovies.onl; this is evidence from the attachment, not a successful live server test. No replacement GokuHD domain was supplied.

## Apply

The small lost-movies-hotfix-v2.2.3.zip contains changes relative to v2.2.2. Apply it only to a checkout already containing v2.2.2. Otherwise use/review the full source release. Preserve .env, database and custom changes. Neither archive contains credentials.

In your correct Git checkout, save existing changes first, create a branch, extract the small ZIP over that checkout, and review the diff. Example PowerShell:

```powershell
git status --short
# Continue only after saving existing changes and confirming this is the correct checkout.
git switch -c catalog-v2.2.3
if ($LASTEXITCODE -ne 0) { throw 'Could not create update branch' }
$zipPath = 'C:\Users\dearl\Documents\Codex\2026-09-11\lets-talk-about-what-is-possible\outputs\lost-movies-hotfix-v2.2.3.zip'
Expand-Archive -LiteralPath $zipPath -DestinationPath (Get-Location).Path -Force
git diff
```

After review, stage only the changed files listed in CHANGED_FILES.txt, commit and push your branch, then merge its pull request. On your existing server checkout, pull the merged update and rebuild:

```sh
git pull --ff-only
docker compose up -d --build
```

Use --profile https if using bundled Caddy. For Python hosting restart the existing service instead. Docker was not built here. No GitHub push or deployment was performed.

## Telegram diagnosis and retry

In the owner's private chat, send each command separately:

```
/version
/domains
/log site gokuhd
/log site rogmovies
```

If /domains still lists the old new2.rogmovies.click domain, you can apply the canonical domain shown in your supplied page:

```
/domain rogmovies https://rogmovies.onl
/sites
```

Use the RogMovies Test button, then GokuHD Test. Tests run directly even while normal searches skip sites in cooldown. A successful live search clears that site's cooldown. A cached success is not a fresh network test. Old cooldowns otherwise expire normally; /providerreset resets download providers, not website search health.

If HTTP 403 persists, changing parser rules or approving download hosts will not fix that response. We need the failed site's current reachable domain or the site owner's access arrangements. No wildcard Workers/R2 hosts, bonuscaf.com or tinyurl.com have been approved automatically.

The parser tests use supplied HTML; later redirect/download steps are not established by successfully listing episode entries. A new unsupported episode destination still needs its actual page inspected.
