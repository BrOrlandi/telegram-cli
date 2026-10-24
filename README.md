# telegram-cli

Headless CLI for your **own** Telegram account: a user session over MTProto through [Telethon](https://docs.telethon.dev), not a bot. Read chats, unread messages and search, send messages that notify people with real mentions, download media and transcribe voice messages locally. JSON output makes it easy to script or plug into an agent.

> **macOS only for now.** Secrets go to the macOS Keychain through `/usr/bin/security`, and `transcribe` uses [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper), which needs Apple Silicon.

## Install

```bash
git clone https://github.com/BrOrlandi/telegram-cli && cd telegram-cli
uv tool install --python 3.12 -e .            # installs ~/.local/bin/telegram
uv tool install --python 3.12 mlx-whisper     # only for `transcribe`; also needs ffmpeg (brew install ffmpeg)
```

## Credentials (macOS Keychain)

Nothing secret is written to disk or printed. Everything lives in the Keychain under the service `$TELEGRAM_CLI_KEYCHAIN_SERVICE` (default `jarvis-center`), accounts `telegram:api_id`, `telegram:api_hash` and `telegram:session`.

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

The session shows up in Telegram's device list as "telegram-cli (Mac)" (`TELEGRAM_CLI_DEVICE` changes it).

## Commands

Put `--json` before the command for machine-readable output. A chat is an id, `@username`, `t.me` link or a (unique) title. Reads never mark anything as read.

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
