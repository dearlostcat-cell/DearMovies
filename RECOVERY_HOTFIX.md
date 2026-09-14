# Recovery navigation hotfix — v2.2.1

This update addresses the v2.2.0 recovery traversal bug seen in the supplied HubDrive log. It retains the existing five sites and Kuroiru.

## Changes

- Known-provider navigation links no longer qualify solely because their domain is registered. Same-provider download/resume/continue buttons still qualify; links to a different registered provider still qualify.
- Recovery ignores login/account routes and navigation labels. Configured candidates are also filtered for authentication destinations.
- Google sign-in redirects no longer generate download-host approval proposals. The owner cannot approve an old accounts.google.com proposal through /hostapprove.
- A failed page is not requested repeatedly through sibling branches of the same provider attempt. A new top-level provider attempt may retry shared destinations.
- Each top-level provider receives its configured hop allowance; a 64-hop job ceiling remains. A failed HubDrive traversal therefore does not automatically exhaust HubCloud's allowance.
- Actual login, verification and HTTP 403 failures remain failures. Hostname recognition is not proof that a file exists or that the server can access it.

## Apply to your Git checkout

The small hotfix ZIP contains only changed files relative to v2.2.0. The full release ZIP also contains the source, configuration and welcome video. Keep your existing .env and database.

In PowerShell, start inside the correct Git checkout. First review `git status --short`; save any existing local changes. If your recovery-v2.2.0 pull request has already merged, switch to main and pull it before creating the new branch. Otherwise create the new branch from your existing recovery-v2.2.0 branch.

```powershell
git switch -c recovery-v2.2.1
if ($LASTEXITCODE -ne 0) { throw 'Could not create hotfix branch' }
# Replace this path with the downloaded hotfix ZIP location if necessary.
$hotfixZip = 'C:\Users\dearl\Documents\Codex\2026-09-11\lets-talk-about-what-is-possible\outputs\lost-movies-hotfix-v2.2.1.zip'
Expand-Archive -LiteralPath $hotfixZip -DestinationPath (Get-Location).Path -Force
git diff --stat
git diff
```

After reviewing the changes, commit and push the new branch, then create a pull request into main:

```powershell
git add -- bot/__init__.py bot/recovery.py bot/resolver.py bot/runtime_config.py tests/test_recovery.py compose.yaml .github/workflows/image.yml README.md DOCKER_IMAGE.md RECOVERY_GUIDE.md RECOVERY_HOTFIX.md
git diff --cached --quiet
if ($LASTEXITCODE -eq 1) {
    git commit -m 'Fix recovery navigation loops and authentication proposals'
    if ($LASTEXITCODE -ne 0) { throw 'Commit failed' }
} elseif ($LASTEXITCODE -ne 0) { throw 'Could not inspect staged changes' }
git push -u origin recovery-v2.2.1
if ($LASTEXITCODE -ne 0) { throw 'Push failed' }
```

## Deploy and retry

After merging, on the server in its existing checkout:

```sh
git pull --ff-only
docker compose up -d --build
```

Include `--profile https` if using the bundled Caddy deployment. For a Python-only install, restart its existing service. Do not start a second process polling the same bot token.

In the owner's private Telegram chat, dismiss the stale Google sign-in proposal:

```
/hostreject e28833549a19
```

If the providers are cooling down after the old failures, after deploying the fix clear those cooldowns once:

```
/providerreset hubdrive
/providerreset hubcloud
```

Retry once. If it still reports BLOCKED, supply the new log and the relevant host page HTML. Do not approve Google Accounts as a download provider. This update does not fabricate a HubCloud URL if no matching file link is present.

## Validation

83 automated tests passed, including five new regression tests. Tests use controlled responses; the live failing URL was redacted, so this is not a claim that the specific file resolves successfully. No Telegram messages were sent. Docker was not built here, and no GitHub or server changes were performed by this packaging operation.
