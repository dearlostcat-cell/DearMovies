# Mirror recovery update — v2.2.2

Includes the v2.2.1 navigation/loop corrections and the new HubDrive missing-file detection and pixel.hubcloud.ist host correction. Retains the five sites, Kuroiru, welcome video and existing deployment settings.

## What changed

- Recognizes HubDrive's supplied HTTP-200 "File not found !" heading as LINK_EXPIRED. The existing mirror fallback then tries the next available link for the selected file.
- Verifies with the supplied movie HTML that HubDrive /file/16254736976 and HubCloud /drive/q2dzkp2psnhfzff remain in the same Arrival variant. No file IDs or replacement URLs are hardcoded into the resolver.
- Adds exactly pixel.hubcloud.ist to generator and HubCloud allowed destinations. Saved provider overrides receive the same addition on load. Other unknown hosts remain unapproved.
- The route may go through Gamerxyt or directly to a configured file host. It does not require Gamerxyt to appear.
- v2.2.1 protections remain: ignore navigation/authentication routes, bound work, avoid repeated failed pages within a provider attempt, and preserve budget for alternate providers.

86 automated tests passed. The supplied movie and missing-file HTML are used in regression tests (scripts/styles/iframes removed). Downstream successful responses are simulated, including paths with and without Gamerxyt. This does not establish that the live file is available or reachable from your server. Real login/403 blocks remain failures.

## GitHub update from PowerShell

Use your existing Git checkout containing v2.2.0 or v2.2.1. If the prior pull request has merged, first switch to main and pull. Save existing local changes before applying. This hotfix is cumulative relative to v2.2.0; review any newer custom edits before replacing files.

```powershell
$repoFolder = Read-Host 'Paste the full path of your DearMovies Git checkout'
Set-Location -LiteralPath $repoFolder
git status --short
if ($LASTEXITCODE -ne 0) { throw 'This is not a Git checkout' }
if (git status --porcelain) { throw 'Save existing local changes before applying the update' }
git switch -c recovery-v2.2.2
if ($LASTEXITCODE -ne 0) { throw 'Could not create the update branch' }
$zipPath = 'C:\Users\dearl\Documents\Codex\2026-09-11\lets-talk-about-what-is-possible\outputs\lost-movies-hotfix-v2.2.2.zip'
Expand-Archive -LiteralPath $zipPath -DestinationPath $repoFolder -Force
git diff --stat
git diff
```

After reviewing, in that same folder:

```powershell
git add -- bot compose.yaml config/providers/generator.yaml config/providers/hubcloud.yaml tests .github/workflows/image.yml README.md DOCKER_IMAGE.md RECOVERY_GUIDE.md RECOVERY_HOTFIX.md
git diff --cached --quiet
if ($LASTEXITCODE -eq 1) {
    git commit -m 'Fix missing-file mirror fallback and HubCloud download host'
    if ($LASTEXITCODE -ne 0) { throw 'Commit failed' }
} elseif ($LASTEXITCODE -ne 0) { throw 'Could not inspect staged changes' }
git push -u origin recovery-v2.2.2
if ($LASTEXITCODE -ne 0) { throw 'Push failed' }
```

Create and merge the pull request to main on GitHub. This preparation has not pushed or deployed anything automatically.

## Server update

In the existing server checkout after merging:

```sh
git pull --ff-only
docker compose up -d --build
```

Include `--profile https` if using bundled Caddy. For Python-only hosting restart the existing service instead. Keep .env and database volumes. Do not start a second Telegram polling process with the same token.

If old failures left cooldowns, clear them once in the owner's private Telegram chat after deployment:

```
/providerreset hubdrive
/providerreset hubcloud
```

Search again, select the version and retry. There is no need to approve pixel.hubcloud.ist manually. If both actual mirrors are unavailable, the bot must still report failure; it cannot recreate a deleted file. The Docker image was not built in the preparation environment.
