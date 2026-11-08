# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-11-08

### Added

- Linux support. Outside macOS (or whenever `TELEGRAM_CLI_SECRETS_FILE` is set) the credentials and the session go to a JSON file with mode `0600`, default `~/.config/telegram-cli/secrets.json`, instead of the Keychain. Downloads default to `~/.cache/telegram-cli`, and the device shows up as "telegram-cli (Linux)".

## [0.1.1] - 2026-11-01

### Fixed

- `me` now always means your Saved Messages. It also accepts `self` and `saved`. Before, a chat titled "Me" (such as a bot) could be picked instead.

### Added

- `telegram --version`. The version shown in Telegram's device list now follows the package version.

## [0.1.0] - 2026-10-24

First public release.

### Added

- Login with your own account (phone, code, two-step password), in one step or in steps for non-interactive use. App credentials and the session live in the macOS Keychain.
- `me`, `dialogs`, `unread`, `history`, `search` and `members`, with `--json` output. Reads never mark anything as read.
- `send` with real mentions (`--mention`), `--reply-to` and `--dry-run`.
- `download` and `transcribe`, which transcribes voice messages locally with mlx-whisper.

[Unreleased]: https://github.com/BrOrlandi/telegram-cli/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/BrOrlandi/telegram-cli/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/BrOrlandi/telegram-cli/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/BrOrlandi/telegram-cli/releases/tag/v0.1.0
