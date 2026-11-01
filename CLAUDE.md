# telegram-cli

Public repo (MIT). Never commit personal data, chat names, ids or secrets: examples stay generic ("Team Chat", "Alice").

## Release & Changelog

Structured convention: `.claude/release.json` (read by the `release` skill).

- Version lives only in `pyproject.toml` (`project.version`); the CLI reads it at runtime (`telegram --version`, the Telegram `app_version`).
- Changelog: `CHANGELOG.md`, Keep a Changelog, English, public.
- Tag `vX.Y.Z` (annotated) and a GitHub release whose body is the changelog entry. No binary assets: users install from source with `uv tool install`.
- Bump from conventional commits (breaking → major, `feat` → minor, otherwise patch), confirmed with the user. While on 0.x, a breaking change may bump minor.
