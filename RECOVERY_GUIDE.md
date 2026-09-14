# Automatic recovery — 2.2.0

No AI service or API key required. This is the five-site server edition with Kuroiru and the welcome video, based on the local 2.1.1 source. Your separate clean v3 starter is unchanged. Later Replit-only edits were not supplied and are not included.

## What recovers automatically

- Recognizes the configured hubdrive.sbs destination and follows a real HubCloud link found on the page. Paths and query parameters are retained; domains are not blindly swapped.
- Accepts redirects to explicitly registered provider hosts and selects the parser belonging to the landed provider.
- Tries existing selectors first, then bounded fallback extraction: actionable download/resume links, data-href/data-url buttons, meta refresh and known literal URL assignments. Only configured destinations are followed. Page JavaScript is never evaluated by this extractor.
- Reads a larger HTML response when the initial bounded range probe was truncated.
- Tries supported alternate routes after failures, preserving file verification. Failed sibling routes no longer incorrectly mark a later route as a loop. A shared hop budget still limits work and fragment-only loops are rejected.
- Existing timeout retries remain bounded. Cooldown logs now report remaining seconds. Parser/configuration failures do not trigger provider-wide cooldown; repeated transient failures or blocks still can.
- Failed extraction logs PAGE_DIAGNOSTIC with host, HTTP status, byte count and element counts. It does not store raw page HTML, signed links, cookies or credentials.

## Owner review in Telegram

Unfamiliar actionable link hosts and unknown redirect hosts are queued as proposals. They are not automatically visited or trusted. Inspect the domain independently before approval, including whether its page format matches the selected provider.

```
/hostproposals
/hostapprove PROPOSAL_ID hubdrive
/hostreject PROPOSAL_ID
/providerreset hubdrive
```

These commands require the owner in a private chat. /hostapprove checks public DNS and saves the explicitly chosen provider mapping in the database. The next request still has to prove it can resolve a file. It does not automatically migrate paths or rewrite an old provider's HTML selectors. A completely new host format requires a new provider definition or parser. Existing jobs finish with their original configuration; new jobs use the approved configuration. Proposals expire after seven days. A rejected proposal can be suggested again if a later request encounters it.

Use /providerreset only after applying a fix, then retry once. It clears the stored cooldown without pretending the host is healthy. Persistent 403 responses, logins, verification challenges and opaque JavaScript flows are not repaired automatically.

## Upgrade

1. Back up the running project and database. If your server has Replit edits newer than the local 2.1.1 release, review RECOVERY_CHANGES.patch and CHANGED_FILES.txt and merge rather than replacing blindly. The patch is relative to the local server edition, not to unknown server code.
2. Keep existing .env and database volumes. The project name remains lost-movies so the normal Compose upgrade retains the same volumes. Do not deploy alongside another process polling the same token.
3. Rebuild using `docker compose up -d --build` (include `--profile https` if using Caddy). Python-only installations must restart their service after replacing code.
4. If old database provider overrides mask hubdrive.sbs, use the existing `/providerdomain hubdrive hubdrive.tips hubdrive.sbs` after independently verifying the host. That existing command explicitly remaps the old hostname; it should only be used when they share the same path scheme. Alternatively review a generated host proposal and approve it for hubdrive to add the hostname without rewriting old URLs.
5. Clear pre-existing cooldowns once with `/providerreset hubdrive` and `/providerreset hubcloud`, then try one representative link.
6. Verify status, search, quality selection and the final file from your actual server. A successful fixture test is not proof that a third-party host will permit the server's IP.

## Verification

78 automated tests passed, including new tests for registered redirects, changed buttons, host review permissions, private-address rejection, truncated HTML, fragment loops and fallback through a previously failed page. Network/provider tests use controlled responses; Telegram messages were not sent during testing.

Docker is not installed in the preparation environment. The Docker build and real host availability remain to be verified on your server or through the included GitHub Actions workflow. No repository was published and no server configuration was changed.
