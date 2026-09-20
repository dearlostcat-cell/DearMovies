# Domain-save fix

Based on the current DearMovies GitHub main branch downloaded for this fix.

Replace bot/runtime_config.py with this version and redeploy Railway, or upload the full project archive to your repository. Keep your existing secrets and database; neither is included or changed.

Then send `/domain gokuhd https://gokuhd.com/`. The domain saves after URL and public-address validation, without making a catalogue search. The response explicitly says website access is not tested. Use `/domains` to verify the saved value and `/sites` → Test GokuHD for a separate access check. `/domainrollback gokuhd` retains the previous rollback behavior.

This fixes the save operation. It does not claim to resolve GokuHD or VCloud HTTP 403 responses. A successful save and successful host access are separate outcomes. Staff-only/private-chat checks remain in effect.

No GitHub push or Railway redeployment has been performed by preparing this archive. Rotate the token exposed in the earlier screenshot using BotFather and update Railway separately.
