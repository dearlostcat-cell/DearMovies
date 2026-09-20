# Site diagnostics — 2.3.1

2.3.1 adds the verified RogMovies `rogmovies.onl` mirror to bundled and saved configurations. API requests now permit configured catalogue mirrors, just as HTML requests already do. Other unconfigured destinations remain blocked.

Failed parser tests now retain a sanitized HTML structure for 24 hours. Use **Download parser report** on the failure message or `/inspect JOB_ID`. Scripts, input fields, most attributes and URL paths are removed. The report preserves selectors and visible text to help repair layouts that cannot be fetched from the development machine. Review the JSON before sharing it.

In a private owner/admin chat:

- `/sites` starts fresh searches for `dear` on all enabled sites, including Kuroiru's API.
- `/sites Mayday` uses a different query.
- `/sites status` displays status without starting requests.
- Each test reports its job ID and provides **View logs** and **Retry test** buttons.
- Successful searches with results offer **Test details (first result)**. This fetches that source's first result again and reports variants, screenshots and watch links.
- `/log JOB_ID` also retrieves test logs. Long logs are sent as a text file; payloads are no longer cut off at 220 characters.

At most two diagnostics run concurrently. Repeated tests for the same site/stage are suppressed while running and for 60 seconds after starting. Tests skip disabled sites and stop when the bot shuts down. Test records expire after 24 hours; event logs follow the existing retention policy. Owner/admin access is required.

HTTP request logs retain host, response status, byte count and truncation flag, not response bodies or signed URLs. Browser transport uses existing browser diagnostics and may not supply HTTP response events. A search success means the catalogue search worked, not that a provider can download a file. Detail tests parse options without downloading media or resolving every provider.

## Fixes

- Kuroiru tests now use the same API as `/anime`, with search health recording.
- GokuHD uses `/search.php?q=...&page=...`, matching the supplied search page. Saved configurations with the old empty API route migrate while keeping owner domains and other settings.
- HDHub4u permits the exact `new6.hdhub4u.cl` mirror, including for saved configuration overrides.
- Recovery ignores navigation, advertising, sidebar and related-link containers instead of treating their download labels as download routes.
- Domain changes retain the earlier fix: validated domains can be saved even if a subsequent access test returns 403.

## Upgrade

1. Back up the database and current project. Replace application files with this archive, keeping your `.env`, hosting secrets, database and mounted data volume.
2. For GitHub deployment, commit the updated files to your repository and redeploy the service. For Docker Compose, run `docker compose up -d --build` from the project folder. Run only one polling instance per bot token.
3. Verify `/version` reports **2.3.1**, then run `/sites`. Use its log buttons to check results from your actual server.

This archive has not been pushed or deployed automatically. It includes the existing Docker/server deployment files; it is not a prebuilt Docker image.

## Remaining external issues

Host-side HTTP 403 responses are not bypassed by these changes. Supplied browser HTML can verify a parser but cannot prove the hosting server is permitted to fetch the site. The exact 4KHDHub/HDHub4u Mayday detail failures need those pages' HTML for a specific parser repair. Unknown Greenmount destinations remain subject to owner review; no arbitrary hosts were automatically trusted.
