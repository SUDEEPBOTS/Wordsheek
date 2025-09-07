import os, time, asyncio
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import Message
from typing import Dict, Tuple

from game_logic import Session, pick_secret, score_for_attempts
from utils import load_wordlist, is_alpha5, render_history, today_str, fetch_definition
import storage_mongo as storage  # MongoDB Atlas backend

load_dotenv()
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

DAILY_MODE = os.getenv("DAILY_MODE", "true").lower() == "true"
DICT_STRICT = os.getenv("DICT_STRICT", "true").lower() == "true"
HINT_MODE = os.getenv("HINT_MODE", "position")  # "position" | "letterpool"

ANSWERS = load_wordlist("words/answers.txt")
GUESSES = set(load_wordlist("words/guesses.txt"))
ALL_VALID = set(ANSWERS) | set(GUESSES)

single_sessions: Dict[int, Session] = {}
group_sessions: Dict[int, Session] = {}

app = Client("wordseek-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

async def ensure_user(m: Message):
    u = m.from_user
    await storage.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "")

async def get_secret(daily: bool) -> str:
    if not daily:
        return pick_secret(ANSWERS, daily_mode=False)
    d = today_str()
    exist = await storage.get_daily_word(d)
    if exist: return exist
    day_seed = int(time.time() // 86400)
    secret = pick_secret(ANSWERS, daily_mode=True, day_seed=day_seed)
    await storage.set_daily_word(d, secret)
    return secret

def active_session(m: Message) -> Tuple[str, Session | None]:
    is_group = m.chat.type in ("group","supergroup")
    return ("group", group_sessions.get(m.chat.id)) if is_group else ("single", single_sessions.get(m.from_user.id))

def help_text() -> str:
    return ("Welcome to WordSeek!\n"
            "/new — start a new round.\n"
            "/guess <word> — or just send the 5-letter word.\n"
            "/hint — one hint (halves points).\n"
            "/giveup — end round and reveal word.\n"
            "/leaderboard — top players.\n"
            "/score — your stats.\n"
            "/rules — rules.\n"
            "Scoring: fewer attempts → more points. Definition shown after wins.\n")

@app.on_message(filters.command(["start","help"]))
async def start_cmd(_, m: Message):
    await ensure_user(m)
    await m.reply_text(help_text())

@app.on_message(filters.command("rules"))
async def rules_cmd(_, m: Message):
    await m.reply_text(
        "Rules:\n"
        "- Guess the 5-letter word in 6 attempts.\n"
        "- 🟩 correct spot, 🟨 in word wrong spot, 🟥 not in word.\n"
        "- Duplicates handled with inventory (greens then yellows).\n"
        "- Using /hint halves your final points.\n"
        "- Daily mode may share one secret per day.\n"
    )

@app.on_message(filters.command("new"))
async def new_cmd(_, m: Message):
    await ensure_user(m)
    is_group = m.chat.type in ("group","supergroup")
    secret = await get_secret(DAILY_MODE)
    if is_group:
        sess = group_sessions.get(m.chat.id)
        if sess and not sess.solved and sess.attempts < 6:
            await m.reply_text("A round is already active in this group. Use /giveup to end or continue guessing.")
            return
        group_sessions[m.chat.id] = Session(user_id=0, chat_id=m.chat.id, mode="group", secret=secret)
        await m.reply_text("🎲 Group WordSeek started! Everyone can guess. 6 attempts total.")
    else:
        single_sessions[m.from_user.id] = Session(user_id=m.from_user.id, chat_id=m.chat.id, mode="single", secret=secret)
        await m.reply_text("🎲 New WordSeek started! Guess the 5-letter word. You have 6 attempts. Send /guess <word> or just send the word.")

@app.on_message(filters.command("giveup"))
async def giveup_cmd(_, m: Message):
    mode, sess = active_session(m)
    if not sess:
        await m.reply_text("No active round. Use /new to start.")
        return
    word = sess.secret.upper()
    if mode == "group": group_sessions.pop(m.chat.id, None)
    else: single_sessions.pop(m.from_user.id, None)
    await m.reply_text(f"😢 Game over — the word was {word}. Better luck next time! No points awarded. Use /new to try again.")

@app.on_message(filters.command("hint"))
async def hint_cmd(_, m: Message):
    mode, sess = active_session(m)
    if not sess:
        await m.reply_text("No active round. Use /new to start.")
        return
    if sess.solved or sess.attempts >= 6:
        await m.reply_text("Round already ended. Use /new to start again.")
        return
    sess.hint_used = True
    if HINT_MODE == "position":
        import random
        idx = random.choice(range(5))
        await m.reply_text(f"💡 Hint: The {idx+1}ᵗʰ letter is '{sess.secret[idx].upper()}'. Using a hint halves your final points for this round.")
    else:
        pool = "".join(sorted(set(sess.secret.upper())))
        await m.reply_text(f"💡 Hint letters: {pool}. Using a hint halves your final points for this round.")

@app.on_message(filters.command("leaderboard"))
async def leaderboard_cmd(_, m: Message):
    rows = await storage.get_leaderboard(10)
    if not rows:
        await m.reply_text("Leaderboard is empty. Play some rounds!")
        return
    lines = ["🏆 Top Players"]
    for i, (username, pts, wins, best) in enumerate(rows, 1):
        name = f"@{username}" if username else f"Player {i}"
        lines.append(f"{i}. {name} — {pts} pts — {wins} wins — best streak {best}")
    await m.reply_text("\n".join(lines))

@app.on_message(filters.command("score"))
async def score_cmd(_, m: Message):
    stats = await storage.get_user_stats(m.from_user.id)
    if not stats:
        await m.reply_text("No stats yet. Play a round with /new.")
        return
    await m.reply_text(
        f"Your stats: Points: {stats['total_points']}, Wins: {stats['wins']}, "
        f"Games: {stats['games_played']}, Winrate: {stats['winrate']}%, "
        f"Best streak: {stats['streak_best']} (current {stats['streak_current']})."
    )

async def handle_guess(m: Message, raw: str):
    await ensure_user(m)
    mode, sess = active_session(m)
    if not sess:
        await m.reply_text("No active round. Use /new to start.")
        return
    ok, msg = sess.can_guess()
    if not ok:
        await m.reply_text(msg)
        return
    g = raw.strip().lower()
    if not is_alpha5(g):
        await m.reply_text("❗Invalid guess — use a 5-letter English word. Try again.")
        return
    if DICT_STRICT and g not in (ALL_VALID):
        await m.reply_text("❗Not in my dictionary. Try another valid 5-letter English word.")
        return
    att = sess.apply_guess(g)
    hist = render_history([(a.guess, a.tiles) for a in sess.history])
    await m.reply_text(f"{hist}\nAttempts used: {sess.attempts}/6")
    if sess.solved:
        pts = score_for_attempts(sess.attempts, sess.hint_used)
        meaning = await fetch_definition(sess.secret)
        u = m.from_user
        await storage.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "")
        await storage.record_result(u.id, u.username or "", sess.secret, sess.attempts, pts, m.chat.id, mode)
        if mode == "group":
            group_sessions.pop(m.chat.id, None)
            await m.reply_text(
                f"🎉 Correct! {u.first_name} solved it. The word was {sess.secret.upper()}.\n"
                f"You solved it in {sess.attempts} attempts and earned {pts} points.\n"
                f"Meaning: {meaning}\nUse /new to play again."
            )
        else:
            single_sessions.pop(u.id, None)
            await m.reply_text(
                f"🎉 Correct! The word was {sess.secret.upper()}.\n"
                f"You solved it in {sess.attempts} attempts and earned {pts} points.\n"
                f"Meaning: {meaning}\nUse /new to play again."
            )
    elif sess.attempts >= 6:
        u = m.from_user
        await storage.record_result(u.id, u.username or "", sess.secret, sess.attempts, 0, m.chat.id, mode)
        if mode == "group": group_sessions.pop(m.chat.id, None)
        else: single_sessions.pop(u.id, None)
        await m.reply_text(f"😢 Game over — the word was {sess.secret.upper()}. Better luck next time! No points awarded. Use /new to try again.")

@app.on_message(filters.command("guess"))
async def guess_cmd(_, m: Message):
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        await m.reply_text("Usage: /guess <5-letter word>")
        return
    await handle_guess(m, parts[26])

@app.on_message(filters.text & ~filters.command(["start","help","new","guess","giveup","leaderboard","score","rules","hint"]))
async def plain_guess(_, m: Message):
    t = (m.text or "").strip()
    if len(t) == 5 and t.isalpha():
        await handle_guess(m, t)

if __name__ == "__main__":
    asyncio.run(storage.init_db())
    app.run()
  
