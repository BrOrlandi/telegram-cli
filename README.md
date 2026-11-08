# telegram-cli

Headless CLI for your **own** Telegram account: a user session over MTProto through [Telethon](https://docs.telethon.dev), not a bot. Read chats, unread messages and search, send messages that notify people with real mentions, download media and transcribe voice messages locally. JSON output makes it easy to script or plug into an agent.

> **macOS and Linux.** On macOS secrets go to the Keychain through `/usr/bin/security`; on Linux to a file only you can read (see below). `transcribe` uses [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper), which needs Apple Silicon, so it only works on a Mac.

## Install

```bash
git clone https://github.com/BrOrlandi/telegram-cli && cd telegram-cli
uv tool install --python 3.12 -e .            # installs ~/.local/bin/telegram
uv tool install --python 3.12 mlx-whisper     # only for `transcribe`; also needs ffmpeg (brew install ffmpeg)
```

On Linux without `uv`, a plain venv works: `python3 -m venv .venv && .venv/bin/pip install -e .` (the command is `.venv/bin/telegram`).

## Credentials

Nothing secret is printed or passed in a command line. The app credentials and the session are stored as `telegram:api_id`, `telegram:api_hash` and `telegram:session`:

- **macOS:** in the Keychain, under the service `$TELEGRAM_CLI_KEYCHAIN_SERVICE` (default `telegram-cli`).
- **Linux** (or any system when `$TELEGRAM_CLI_SECRETS_FILE` is set): in a JSON file, default `~/.config/telegram-cli/secrets.json`, written with mode `0600` in a `0700` directory. Anyone who can read that file can use your account, so keep it on a machine you control.

```bash
telegram auth setup    # api_id / api_hash from https://my.telegram.org (or TELEGRAM_API_ID / TELEGRAM_API_HASH in the env)
telegram auth login    # interactive: phone, code, 2FA password; stores a StringSession
telegram auth status
telegram auth logout   # ends the session on Telegram and removes it from the Keychain
```

Without an interactive terminal (an agent, a script), log in in steps:

```bash
telegram auth login --phone +5511999999999   # sends the code; the output says where (usually the "Telegram" chat in your app)
telegram auth login --code 12345
telegram auth login --password               # only with two-step verification; asks in a terminal (or --password-env VAR)
```

The session shows up in Telegram's device list as "telegram-cli (Mac)" or "telegram-cli (Linux)" (`TELEGRAM_CLI_DEVICE` changes it).

## Commands

Put `--json` before the command for machine-readable output. A chat is an id, `@username`, `t.me` link or a (unique) title; `me` is always your Saved Messages. Reads never mark anything as read.

```bash
telegram me
telegram dialogs [--unread] [--limit 50]
telegram unread [--messages 20] [--skip-muted]
telegram history "Team Chat" [--limit 30] [--since 2026-10-20] [--from Alice]
telegram search "deploy" [--chat "Team Chat"]
telegram members "Team Chat" [--search ali]
telegram send "Team Chat" "the meeting moved to 3pm" --mention Alice [--reply-to 123] [--dry-run]
telegram download "Team Chat" 456 [--out ~/Downloads]
telegram transcribe "Team Chat" 456 [--language en]
```

- `--mention` resolves a member (@username, id or name) and adds a mention entity, so the person is notified even without a username.
- `--dry-run` resolves the chat and mentions and shows what would be sent, without sending.
- `transcribe` downloads the voice/audio/video to a temporary directory, runs `mlx_whisper` locally (`mlx-community/whisper-large-v3-turbo`, Portuguese by default) and deletes the file. The first run downloads the model.

Exit codes: 2 = app credentials missing, 3 = not logged in or session revoked, 4 = two-step password needed, 1 = other errors (JSON on stderr with `--json`).

## Notes

Automating a user account is allowed by Telegram's API terms, but it is your account: avoid bulk sending and respect `FloodWait` errors, or Telegram may limit it.

## License

MIT
