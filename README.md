# Celeste Discord Bot Scaffold

A fully wired Discord bot using `discord.py` 2.x showcasing prefix, slash, hybrid commands, and UI components.

## Features
- Prefix commands (e.g., `!ping`).
- Slash commands with guild-only fast sync option.
- Hybrid commands shared between prefix and slash contexts.
- Persistent button UI with counter/information actions.
- Admin utilities for lockdown/unlockdown and slash sync.

## Setup
1. **Create a virtual environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows use .venv\\Scripts\\activate
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**
   - Copy `.env.example` to `.env` and fill in your bot token.
   - Optionally set `GUILD_ID` for faster slash command updates while developing.

4. **Edit `config.yml` if needed**
   - `prefix`: Command prefix for text commands.
- `admin_role_ids`: Role IDs required for admin commands (empty list by default). Use role IDs so names can change safely.
   - `log_level`: Logging verbosity.
   - `data_dir`: Directory for JSON storage.
   - `dev_guild_id`: Optional guild ID for app command sync; speeds up iteration.

5. **Enable Message Content Intent**
   - In the Discord Developer Portal, enable the *Message Content Intent* for your bot. This is required for prefix commands to work.

6. **Run the bot**
   ```bash
   python main.py
   ```

## Slash command tips
- Make sure you invited the bot with the `applications.commands` scope (alongside the normal `bot` scope) so slash commands can register.
- Set `GUILD_ID` or `dev_guild_id` to your test server's ID for immediate, per-guild syncing. Without it, global sync can take a while to appear in the `/` menu.
- If slash commands still do not appear, reinvite the bot with the correct scopes and run the `/sync` command to refresh.

## Commands
- `!ping` / `/ping`: Show latency and invocation style.
- `!about` / `/about`: Bot information.
- `!counter` / `/counter`: Increment a per-guild counter stored in `data/counters.json`.
- `/panel`: Post a persistent control panel with buttons to confirm, increment the counter, or show config info.
- `!lockdown` / `/lockdown`: Restrict @everyone from sending messages in the current channel.
- `!unlockdown` / `/unlockdown`: Restore default send permissions.
- `!sync` / `/sync [guild_only]`: Sync application commands (guild-only when configured for faster updates).

## Notes
- `.env` is ignored by git; keep your token safe.
- When `dev_guild_id` is set, slash commands sync to that guild for instant availability; otherwise they are synced globally (may take time to propagate).
