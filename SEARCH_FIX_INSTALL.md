# Fresh search tests and GokuHD configuration — v2.2.4

## Changes and evidence

The supplied GokuHD homepage submits its search form to /?s=QUERY. The previous configuration called /search.php?q=QUERY. This update uses the supplied form route with HTML parsing. Both the homepage article cards and the earlier saved results-grid layout are supported. This does not prove that the real search results retain either layout; malformed results still fail explicitly.

Saved GokuHD overrides using the old /search.php configuration receive the new search layout while retaining owner domains and other independent settings. Other custom search APIs are preserved. The site Test button bypasses the search cache. Normal searches still use caching. Fresh requests can report 403 instead of hiding it behind a cache hit.

91 automated tests passed, including both saved GokuHD layouts, the cache-versus-live-test distinction, and saved configuration migration. Live search URLs could not be opened by the preparation web tool. No real server access or HTTP 403 resolution is claimed. RogMovies' search API was not changed based solely on its homepage form.

## Install

The small lost-movies-hotfix-v2.2.4.zip updates v2.2.3. The full lost-movies-recovery-v2.2.4.zip includes the earlier fixes. Preserve .env, database and custom changes. Neither archive contains a Telegram token.

Start in your existing Git checkout. Save existing changes, then create a new branch:

```powershell
git status --short
git switch -c search-v2.2.4
if ($LASTEXITCODE -ne 0) { throw 'Could not create update branch' }
$zipPath = 'C:\Users\dearl\Documents\Codex\2026-09-11\lets-talk-about-what-is-possible\outputs\lost-movies-hotfix-v2.2.4.zip'
Expand-Archive -LiteralPath $zipPath -DestinationPath (Get-Location).Path -Force
git diff
```

Review and stage only the files in CHANGED_FILES.txt, commit and push this branch, then merge its pull request. On the existing server after merging:

```sh
git pull --ff-only
docker compose up -d --build
```

Use --profile https for bundled Caddy, or restart the existing Python service if not using Docker. No GitHub push, Docker build or deployment was performed here.

## Confirm on your server

Send these commands separately in the owner's Telegram chat:

```
/version
/domains
/sites
```

Confirm version 2.2.4, then use Test for GokuHD and RogMovies. In this release tests always make fresh requests. Send back each result and job ID. A fresh successful search clears that site's cooldown; failures remain failures. Normal searches may still show CACHE_HIT.

The supplied RogMovies homepage has canonical domain https://rogmovies.onl/. If /domains still shows the old new2.rogmovies.click domain, use the existing domain command and test again:

```
/domain rogmovies https://rogmovies.onl
```

Do not clear caches, approve unrelated download hosts or reset download-provider cooldowns to fix a website-search 403. If the corrected GokuHD request is still blocked, the returned server response must be investigated separately; retrying the same request is not a fix.
