"""
telegram - headless CLI for the user's own Telegram account (MTProto through Telethon, not a bot).

The session is a Telethon StringSession kept in the macOS Keychain (service $TELEGRAM_CLI_KEYCHAIN_SERVICE,
default "jarvis-center"; account "telegram:session"), together with the app credentials from my.telegram.org
("telegram:api_id", "telegram:api_hash"). Nothing secret is written to disk or printed.

Reads never mark anything as read. Only `send` writes to Telegram.
"""

import argparse
import asyncio
import base64
import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from telethon import TelegramClient, utils
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl import types

KEYCHAIN_SERVICE = os.environ.get("TELEGRAM_CLI_KEYCHAIN_SERVICE") or "jarvis-center"
ACCOUNT_SESSION = "telegram:session"
ACCOUNT_API_ID = "telegram:api_id"
ACCOUNT_API_HASH = "telegram:api_hash"
ACCOUNT_PENDING = "telegram:pending_login"
MEDIA_DIR = Path.home() / "Library" / "Caches" / "telegram-cli"
WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"
SEARCH_PATH = ["/opt/homebrew/bin", str(Path.home() / ".local" / "bin"), "/usr/local/bin"]
SNIPPET = 4000


class CliError(Exception):
    def __init__(self, message, code="error", exit_code=1):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


# --- Keychain (through /usr/bin/security; values go on stdin, never in argv) ---

def keychain_read(account):
    r = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def keychain_write(account, value):
    if not value or any(c in value for c in " \"'\\\n\r\t"):
        raise CliError(f"refusing to store an unexpected value for {account}", "bad_value")
    r = subprocess.run(
        ["security", "-i"],
        input=f'add-generic-password -U -s {KEYCHAIN_SERVICE} -a {account} -l "telegram-cli {account}" -w {value}\n',
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        raise CliError(f"keychain write failed for {account}", "keychain")


def keychain_delete(account):
    subprocess.run(
        ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account],
        capture_output=True, timeout=10,
    )


def api_credentials():
    api_id = keychain_read(ACCOUNT_API_ID)
    api_hash = keychain_read(ACCOUNT_API_HASH)
    if not api_id or not api_hash:
        raise CliError("Telegram app credentials missing: run `telegram auth setup`", "not_configured", 2)
    return int(api_id), api_hash


def make_client(session=None):
    api_id, api_hash = api_credentials()
    return TelegramClient(
        StringSession(session or ""), api_id, api_hash,
        device_model=os.environ.get("TELEGRAM_CLI_DEVICE") or "telegram-cli (Mac)", app_version="telegram-cli 0.1",
        system_version="macOS", receive_updates=False,
    )


async def connected_client():
    session = keychain_read(ACCOUNT_SESSION)
    if not session:
        raise CliError("not logged in: run `telegram auth login`", "not_logged_in", 3)
    client = make_client(session)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise CliError("the Telegram session was revoked or expired: run `telegram auth login`", "unauthorized", 3)
    return client


# --- Serialization ---

def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def parse_time(value):
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.astimezone()


def fold(text):
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).casefold().strip()


def name_of(entity):
    if entity is None:
        return None
    if isinstance(entity, types.User):
        return utils.get_display_name(entity) or entity.username or str(entity.id)
    return getattr(entity, "title", None) or utils.get_display_name(entity) or str(entity.id)


def kind_of(entity):
    if isinstance(entity, types.User):
        return "bot" if entity.bot else "user"
    if isinstance(entity, types.Channel):
        return "group" if entity.megagroup or entity.gigagroup else "channel"
    return "group"


def peer_json(entity):
    if entity is None:
        return None
    return {
        "id": utils.get_peer_id(entity),
        "name": name_of(entity),
        "username": getattr(entity, "username", None),
        "type": kind_of(entity),
    }


def media_json(msg):
    if msg.media is None:
        return None
    if msg.voice:
        kind = "voice"
    elif msg.video_note:
        kind = "video_note"
    elif msg.audio:
        kind = "audio"
    elif msg.photo:
        kind = "photo"
    elif msg.video:
        kind = "video"
    elif msg.sticker:
        kind = "sticker"
    elif msg.document:
        kind = "document"
    else:
        kind = type(msg.media).__name__
    out = {"type": kind}
    f = msg.file
    if f is not None:
        if f.duration:
            out["duration"] = f.duration
        if f.name:
            out["file_name"] = f.name
        if f.size:
            out["size"] = f.size
        if f.mime_type:
            out["mime_type"] = f.mime_type
    return out


def message_json(msg, chat=None):
    return {
        "id": msg.id,
        "date": iso(msg.date),
        "chat": peer_json(chat or msg.chat),
        "sender": peer_json(msg.sender),
        "out": bool(msg.out),
        "text": (msg.message or "")[:SNIPPET],
        "reply_to": msg.reply_to_msg_id,
        "media": media_json(msg),
        "edited": iso(msg.edit_date),
    }


# --- Resolution ---

async def resolve_chat(client, ref):
    """A chat by numeric id, @username, t.me link or (unique) title among the user's dialogs."""
    ref = (ref or "").strip()
    if not ref:
        raise CliError("missing chat", "bad_args")
    if ref.startswith("@") or "t.me/" in ref:
        return await client.get_entity(ref)
    dialogs = await client.get_dialogs(limit=None, ignore_migrated=True)
    if ref.lstrip("-").isdigit():
        want = int(ref)
        for d in dialogs:
            if d.id == want or d.entity.id == want:
                return d.entity
        return await client.get_entity(want)
    target = fold(ref)
    exact = [d for d in dialogs if fold(d.name) == target]
    if len(exact) == 1:
        return exact[0].entity
    partial = exact or [d for d in dialogs if target in fold(d.name)]
    if len(partial) == 1:
        return partial[0].entity
    if not partial:
        raise CliError(f'no chat matches "{ref}"', "chat_not_found")
    names = ", ".join(f'"{d.name}" ({d.id})' for d in partial[:10])
    raise CliError(f'"{ref}" matches several chats: {names}. Use the id.', "ambiguous_chat")


async def resolve_member(client, chat, ref):
    """A user by @username or id, or by (unique) name among the chat's members."""
    ref = ref.strip()
    if ref.startswith("@"):
        return await client.get_entity(ref)
    if ref.lstrip("-").isdigit():
        return await client.get_entity(int(ref))
    target = fold(ref)
    found = []
    async for u in client.iter_participants(chat, search=ref if len(ref) >= 2 else None, limit=200):
        names = {fold(utils.get_display_name(u)), fold(u.first_name), fold(u.username)}
        if target in names or any(n.startswith(target) for n in names if n):
            found.append(u)
    if len(found) == 1:
        return found[0]
    if not found:
        raise CliError(f'nobody named "{ref}" in {name_of(chat)}', "member_not_found")
    names = ", ".join(f"{utils.get_display_name(u)} (@{u.username or '-'}, {u.id})" for u in found[:10])
    raise CliError(f'"{ref}" matches several members: {names}. Use @username or id.', "ambiguous_member")


def utf16_len(text):
    return len(text.encode("utf-16-le")) // 2


def mention_entities(text, users):
    """Text and entities that notify each user: their name is linked where it appears, else prefixed."""
    entities = []
    prefix = []
    for u in users:
        label = f"@{u.username}" if u.username else utils.get_display_name(u)
        candidates = [c for c in (f"@{u.username}" if u.username else None, utils.get_display_name(u), u.first_name) if c]
        if not any(c in text for c in candidates):
            prefix.append(label)
    if prefix:
        text = " ".join(prefix) + " " + text
    for u in users:
        for c in (f"@{u.username}" if u.username else None, utils.get_display_name(u), u.first_name):
            if c and c in text:
                start = text.index(c)
                entities.append(types.InputMessageEntityMentionName(
                    offset=utf16_len(text[:start]), length=utf16_len(c), user_id=utils.get_input_user(u),
                ))
                break
    return text, entities


# --- Commands ---

async def cmd_auth_setup(args):
    api_id = os.environ.get("TELEGRAM_API_ID") or input("api_id (my.telegram.org): ").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH") or getpass.getpass("api_hash: ").strip()
    if not api_id.isdigit() or len(api_hash) < 16:
        raise CliError("invalid api_id / api_hash", "bad_args")
    keychain_write(ACCOUNT_API_ID, api_id)
    keychain_write(ACCOUNT_API_HASH, api_hash)
    return {"ok": True, "stored": [ACCOUNT_API_ID, ACCOUNT_API_HASH]}


def two_step_password(args):
    if args.password_env:
        value = os.environ.get(args.password_env)
        if not value:
            raise CliError(f"${args.password_env} is empty", "bad_args")
        return value
    if not sys.stdin.isatty():
        raise CliError(
            "this account has two-step verification: run `telegram auth login --password` in a terminal, "
            "or pass --password-env VAR with the password in that variable",
            "password_needed", 4,
        )
    return getpass.getpass("Two-step verification password: ")


async def finish_login(client, args):
    me = await client.get_me()
    keychain_write(ACCOUNT_SESSION, client.session.save())
    keychain_delete(ACCOUNT_PENDING)
    return {"ok": True, "me": peer_json(me)}


def sent_via(sent):
    """Where Telegram delivered the login code, in words."""
    names = {
        "SentCodeTypeApp": "a message from \"Telegram\" in your Telegram app on another device",
        "SentCodeTypeSms": "SMS",
        "SentCodeTypeCall": "phone call",
        "SentCodeTypeFlashCall": "flash call (the code is in the calling number)",
        "SentCodeTypeMissedCall": "missed call (the code is the last digits of the calling number)",
        "SentCodeTypeEmailCode": "e-mail",
        "SentCodeTypeSetUpEmailRequired": "Telegram requires setting up a login e-mail first (do it in the app)",
        "SentCodeTypeFragmentSms": "Fragment (anonymous number)",
        "SentCodeTypeFirebaseSms": "SMS",
    }
    kind = type(getattr(sent, "type", None)).__name__
    out = {"type": kind, "where": names.get(kind, kind)}
    nxt = getattr(sent, "next_type", None)
    if nxt is not None:
        out["fallback"] = names.get(type(nxt).__name__, type(nxt).__name__)
    if getattr(sent, "timeout", None):
        out["fallback_after_s"] = sent.timeout
    return out


async def cmd_auth_login(args):
    """Interactive in a terminal; without one, in steps: --phone (sends the code), --code, --password."""
    pending = keychain_read(ACCOUNT_PENDING)
    state = json.loads(base64.urlsafe_b64decode(pending)) if pending else None
    if args.phone and not args.code:
        state = None  # a new --phone restarts the login
    client = make_client(state["session"] if state else None)
    await client.connect()
    try:
        if args.password and state:
            await client.sign_in(password=two_step_password(args))
            return await finish_login(client, args)
        if args.code and not state:
            raise CliError("no login in progress: start with `telegram auth login --phone +55...`", "no_pending_login")
        if not state:
            phone = args.phone or (input("Phone (+55...): ").strip() if sys.stdin.isatty() else None)
            if not phone:
                raise CliError("no terminal: run `telegram auth login --phone +55...`, then `--code <code>`", "bad_args")
            sent = await client.send_code_request(phone)
            state = {"phone": phone, "hash": sent.phone_code_hash, "session": client.session.save()}
            keychain_write(ACCOUNT_PENDING, base64.urlsafe_b64encode(json.dumps(state).encode()).decode())
            if not sys.stdin.isatty() and not args.code:
                return {"ok": True, "code_sent": True, "sent_via": sent_via(sent), "next": "telegram auth login --code <code>"}
        code = args.code or input("Code sent by Telegram: ").strip()
        try:
            await client.sign_in(phone=state["phone"], code=code, phone_code_hash=state["hash"])
        except SessionPasswordNeededError:
            keychain_write(ACCOUNT_PENDING, base64.urlsafe_b64encode(json.dumps({**state, "session": client.session.save()}).encode()).decode())
            await client.sign_in(password=two_step_password(args))
        return await finish_login(client, args)
    finally:
        await client.disconnect()


async def cmd_auth_status(args):
    try:
        client = await connected_client()
    except CliError as e:
        return {"ok": False, "code": e.code, "message": str(e)}
    try:
        return {"ok": True, "me": peer_json(await client.get_me())}
    finally:
        await client.disconnect()


async def cmd_auth_logout(args):
    session = keychain_read(ACCOUNT_SESSION)
    if session:
        client = make_client(session)
        await client.connect()
        try:
            if await client.is_user_authorized():
                await client.log_out()
        finally:
            await client.disconnect()
    keychain_delete(ACCOUNT_SESSION)
    return {"ok": True}


async def with_client(fn, args):
    client = await connected_client()
    try:
        return await fn(client, args)
    finally:
        await client.disconnect()


async def cmd_me(client, args):
    me = await client.get_me()
    return {**peer_json(me), "phone_last4": (me.phone or "")[-4:] or None, "premium": bool(me.premium)}


async def cmd_dialogs(client, args):
    out = []
    async for d in client.iter_dialogs(limit=args.limit, archived=args.archived, ignore_migrated=True):
        if args.unread and not (d.unread_count or d.unread_mentions_count):
            continue
        out.append({
            **peer_json(d.entity),
            "unread": d.unread_count,
            "unread_mentions": d.unread_mentions_count,
            "muted": bool(d.dialog.notify_settings and d.dialog.notify_settings.mute_until),
            "last_message": message_json(d.message, d.entity) if d.message else None,
        })
    return {"dialogs": out}


async def cmd_unread(client, args):
    """Chats with unread messages and the unread messages themselves (newest last). Marks nothing as read."""
    out = []
    async for d in client.iter_dialogs(limit=args.dialogs, ignore_migrated=True):
        if not d.unread_count and not d.unread_mentions_count:
            continue
        if args.skip_muted and d.dialog.notify_settings and d.dialog.notify_settings.mute_until and not d.unread_mentions_count:
            continue
        read_max = d.dialog.read_inbox_max_id or 0
        msgs = []
        if args.messages:
            async for m in client.iter_messages(d.entity, limit=min(d.unread_count or args.messages, args.messages), min_id=read_max):
                msgs.append(message_json(m, d.entity))
            msgs.reverse()
        out.append({**peer_json(d.entity), "unread": d.unread_count, "unread_mentions": d.unread_mentions_count, "messages": msgs})
    return {"chats": out}


async def cmd_history(client, args):
    chat = await resolve_chat(client, args.chat)
    since, until = parse_time(args.since), parse_time(args.until)
    sender = await resolve_member(client, chat, args.sender) if args.sender else None
    msgs = []
    async for m in client.iter_messages(chat, limit=args.limit, offset_date=until, from_user=sender):
        if since and m.date < since:
            break
        msgs.append(message_json(m, chat))
    msgs.reverse()
    return {"chat": peer_json(chat), "messages": msgs}


async def cmd_search(client, args):
    chat = await resolve_chat(client, args.chat) if args.chat else None
    msgs = [message_json(m) async for m in client.iter_messages(chat, search=args.query, limit=args.limit)]
    return {"query": args.query, "chat": peer_json(chat), "messages": msgs}


async def cmd_members(client, args):
    chat = await resolve_chat(client, args.chat)
    users = [peer_json(u) async for u in client.iter_participants(chat, search=args.search or "", limit=args.limit)]
    return {"chat": peer_json(chat), "members": users}


async def cmd_send(client, args):
    chat = await resolve_chat(client, args.chat)
    users = [await resolve_member(client, chat, m) for m in (args.mention or [])]
    text, entities = mention_entities(args.text, users)
    preview = {"chat": peer_json(chat), "text": text, "mentions": [peer_json(u) for u in users], "reply_to": args.reply_to}
    if args.dry_run:
        return {"ok": True, "dry_run": True, **preview}
    msg = await client.send_message(chat, text, formatting_entities=entities or None, reply_to=args.reply_to, link_preview=False)
    return {"ok": True, **preview, "message": message_json(msg, chat)}


async def fetch_message(client, chat_ref, msg_id):
    chat = await resolve_chat(client, chat_ref)
    msg = await client.get_messages(chat, ids=msg_id)
    if msg is None:
        raise CliError(f"message {msg_id} not found in {name_of(chat)}", "message_not_found")
    if msg.media is None:
        raise CliError(f"message {msg_id} has no media", "no_media")
    return chat, msg


async def cmd_download(client, args):
    chat, msg = await fetch_message(client, args.chat, args.message_id)
    out = Path(args.out).expanduser() if args.out else MEDIA_DIR
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = await msg.download_media(file=str(out) + "/")
    os.chmod(path, 0o600)
    return {"path": path, "message": message_json(msg, chat)}


def find_tool(name):
    return shutil.which(name, path=os.pathsep.join([*SEARCH_PATH, os.environ.get("PATH", "")]))


def transcribe_file(path, model, language):
    whisper = find_tool("mlx_whisper")
    if not whisper:
        raise CliError("mlx_whisper not found: uv tool install --python 3.12 mlx-whisper", "no_transcriber")
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        raise CliError("ffmpeg not found: brew install ffmpeg", "no_ffmpeg")
    env = {**os.environ, "PATH": os.pathsep.join([str(Path(ffmpeg).parent), *SEARCH_PATH, "/usr/bin", "/bin"])}
    with tempfile.TemporaryDirectory(prefix="telegram-cli-") as tmp:
        cmd = [whisper, path, "--model", model, "--output-dir", tmp, "--output-format", "txt", "--verbose", "False"]
        if language:
            cmd += ["--language", language]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=1800)
        if r.returncode != 0:
            raise CliError(f"transcription failed: {(r.stderr or r.stdout).strip()[-300:]}", "transcribe_failed")
        txt = next(Path(tmp).glob("*.txt"), None)
        return txt.read_text().strip() if txt else ""


async def cmd_transcribe(client, args):
    chat, msg = await fetch_message(client, args.chat, args.message_id)
    if not (msg.voice or msg.audio or msg.video_note or msg.video):
        raise CliError(f"message {args.message_id} is not audio or video", "not_audio")
    with tempfile.TemporaryDirectory(prefix="telegram-cli-") as tmp:
        path = await msg.download_media(file=tmp + "/")
        text = await asyncio.to_thread(transcribe_file, path, args.model, args.language)
    return {"message": message_json(msg, chat), "model": args.model, "language": args.language, "text": text}


# --- Output and entry point ---

def human(result):
    if "messages" in result and isinstance(result["messages"], list):
        lines = []
        for m in result["messages"]:
            who = "eu" if m["out"] else (m["sender"] or {}).get("name") or "?"
            media = f" [{m['media']['type']}]" if m.get("media") else ""
            when = datetime.fromisoformat(m["date"].replace("Z", "+00:00")).astimezone().strftime("%d/%m %H:%M")
            lines.append(f"{when}  #{m['id']}  {who}: {m['text']}{media}")
        return "\n".join(lines) or "(no messages)"
    if "dialogs" in result:
        return "\n".join(f"{d['id']:>16}  {d['type']:<7} {d['unread']:>4} unread  {d['name']}" for d in result["dialogs"]) or "(none)"
    if "chats" in result:
        out = []
        for c in result["chats"]:
            out.append(f"== {c['name']} ({c['id']}): {c['unread']} unread")
            out += [f"   #{m['id']} {(m['sender'] or {}).get('name') or '?'}: {m['text']}" for m in c["messages"]]
        return "\n".join(out) or "(nothing unread)"
    if "members" in result:
        return "\n".join(f"{u['id']:>12}  @{u['username'] or '-':<20} {u['name']}" for u in result["members"])
    if "text" in result and "model" in result:
        return result["text"]
    return json.dumps(result, ensure_ascii=False, indent=2)


def parser():
    p = argparse.ArgumentParser(prog="telegram", description="Your own Telegram account from the terminal (not a bot).")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd", required=True)

    auth = sub.add_parser("auth", help="credentials and session (Keychain)").add_subparsers(dest="auth_cmd", required=True)
    auth.add_parser("setup", help="store api_id/api_hash from my.telegram.org")
    login = auth.add_parser("login", help="sign in with phone + code (+ 2FA password)")
    login.add_argument("--phone", help="start: sends the code to the Telegram app")
    login.add_argument("--code", help="the code received (continues a login started with --phone)")
    login.add_argument("--password", action="store_true", help="finish a login that needs the 2FA password (asks in a terminal)")
    login.add_argument("--password-env", metavar="VAR", help="read the 2FA password from this environment variable")
    auth.add_parser("status", help="is the session valid?")
    auth.add_parser("logout", help="end the session on Telegram and remove it from the Keychain")

    sub.add_parser("me", help="the signed-in account")

    d = sub.add_parser("dialogs", help="chats, newest first")
    d.add_argument("--limit", type=int, default=50)
    d.add_argument("--unread", action="store_true", help="only chats with unread messages")
    d.add_argument("--archived", action="store_true")

    u = sub.add_parser("unread", help="unread messages per chat (marks nothing as read)")
    u.add_argument("--dialogs", type=int, default=200, help="how many recent chats to scan")
    u.add_argument("--messages", type=int, default=20, help="unread messages per chat (0 = counts only)")
    u.add_argument("--skip-muted", action="store_true", help="ignore muted chats unless you were mentioned")

    h = sub.add_parser("history", help="messages of one chat (oldest first)")
    h.add_argument("chat", help="id, @username, t.me link or title")
    h.add_argument("--limit", type=int, default=30)
    h.add_argument("--since", help="ISO date/time")
    h.add_argument("--until", help="ISO date/time")
    h.add_argument("--from", dest="sender", help="only messages from this member")

    s = sub.add_parser("search", help="search messages (all chats or one)")
    s.add_argument("query")
    s.add_argument("--chat")
    s.add_argument("--limit", type=int, default=30)

    m = sub.add_parser("members", help="members of a group")
    m.add_argument("chat")
    m.add_argument("--search")
    m.add_argument("--limit", type=int, default=200)

    snd = sub.add_parser("send", help="send a text message")
    snd.add_argument("chat")
    snd.add_argument("text")
    snd.add_argument("--mention", action="append", help="member to notify (@username, id or name); repeatable")
    snd.add_argument("--reply-to", type=int)
    snd.add_argument("--dry-run", action="store_true", help="resolve everything and show what would be sent")

    dl = sub.add_parser("download", help="download the media of a message")
    dl.add_argument("chat")
    dl.add_argument("message_id", type=int)
    dl.add_argument("--out", help=f"directory (default {MEDIA_DIR})")

    t = sub.add_parser("transcribe", help="transcribe a voice/audio/video message locally (mlx-whisper)")
    t.add_argument("chat")
    t.add_argument("message_id", type=int)
    t.add_argument("--model", default=WHISPER_MODEL)
    t.add_argument("--language", default="pt", help='language code, or "" to detect')
    return p


COMMANDS = {
    "me": cmd_me, "dialogs": cmd_dialogs, "unread": cmd_unread, "history": cmd_history, "search": cmd_search,
    "members": cmd_members, "send": cmd_send, "download": cmd_download, "transcribe": cmd_transcribe,
}
AUTH = {"setup": cmd_auth_setup, "login": cmd_auth_login, "status": cmd_auth_status, "logout": cmd_auth_logout}


async def run(args):
    if args.cmd == "auth":
        return await AUTH[args.auth_cmd](args)
    return await with_client(COMMANDS[args.cmd], args)


def main():
    args = parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except CliError as e:
        err = {"error": {"code": e.code, "message": str(e)}}
        print(json.dumps(err, ensure_ascii=False) if args.json else f"telegram: {e}", file=sys.stderr)
        sys.exit(e.exit_code)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:  # Telethon/RPC errors: report the type and message, never the session
        err = {"error": {"code": type(e).__name__, "message": str(e)[:300]}}
        print(json.dumps(err, ensure_ascii=False) if args.json else f"telegram: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else human(result))


if __name__ == "__main__":
    main()
