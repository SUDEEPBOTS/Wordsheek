import os, time, asyncio, random
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field

Third-party
from pyrogram import Client, filters
from pyrogram.types import Message
from motor.motor_asyncio import AsyncIOMotorClient
import aiohttp

-----------------------------
Config from environment
-----------------------------
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

MONGO_URI = os.getenv("MONGO_URI", "")
MONGO_DB = os.getenv("MONGO_DB", "wordseek")

DAILY_MODE = os.getenv("DAILY_MODE", "true").lower() == "true"
DICT_STRICT = os.getenv("DICT_STRICT", "true").lower() == "true"
HINT_MODE = os.getenv("HINT_MODE", "position") # "position" | "letterpool"

-----------------------------
Embedded minimal word lists
Replace/extend these arrays as needed
-----------------------------
ANSWERS_EMBED = [
"smile","crane","slice","opera","stone","smear","crown","geese","baste","sooth",
"beard","grasp","blade","rapid","clean","point","align","earth","light","night"
]
GUESSES_EXTRA = [
"slate","stare","share","shout","taste","cater","trace","trial","raise","arise",
"ratio","irate","adieu","audio","house","input","value","zesty","fuzzy","jazzy"
]
ALL_VALID = set(ANSWERS_EMBED) | set(GUESSES_EXTRA)

-----------------------------
Tiles and evaluation
-----------------------------
GREEN, YELLOW, GREY = "🟩", "🟨", "🟥"

def _counts(s: str) -> Dict[str, int]:
c: Dict[str, int] = {}
for ch in s:
c[ch] = c.get(ch, 0) + 1
return c

def evaluate_guess(secret: str, guess: str) -> List[str]:
# Wordle-accurate: pass1 greens, pass2 yellows using inventory
secret, guess = secret.lower(), guess.lower()
tiles = [GREY]*5
inv = _counts(secret)
for i in range(5):
if guess[i] == secret[i]:
tiles[i] = GREEN
inv[guess[i]] -= 1
for i in range(5):
if tiles[i] == GREEN:
continue
ch = guess[i]
if inv.get(ch, 0) > 0:
tiles[i] = YELLOW
inv[ch] -= 1
else:
tiles[i] = GREY
return tiles

def is_alpha5(word: str) -> bool:
return len(word) == 5 and word.isalpha()

def is_win(tiles: List[str]) -> bool:
return all(t == GREEN for t in tiles)

def score_for_attempts(n: int, hint_used: bool) -> int:
base = {1:100,2:60,3:40,4:25,5:15,6:10}.get(n, 0)
return int(base*0.5) if hint_used else base

def render_history(history: List[Tuple[str, List[str]]]) -> str:
return "\n".join(f"{' '.join(tiles)} {guess.upper()}" for guess, tiles in history)

async def fetch_definition(word: str) -> str:
url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
try:
async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
async with s.get(url) as r:
if r.status == 200:
data = await r.json()
if isinstance(data, list) and data:
meanings = data.get("meanings", [])
if meanings:
defs = meanings.get("definitions", [])
if defs:
return defs.get("definition", "Definition not available.")
except Exception:
pass
return "Definition not available."

-----------------------------
Game session
-----------------------------
@dataclass
class Attempt:
guess: str
tiles: List[str]

@dataclass
class Session:
user_id: int
chat_id: int
mode: str # "single" | "group"
secret: str
attempts: int = 0
history: List[Attempt] = field(default_factory=list)
started_at: float = field(default_factory=time.time)
solved: bool = False
hint_used: bool = False
last_guess_ts: float = 0.0

text
def can_guess(self) -> Tuple[bool, str]:
    now = time.time()
    if now - self.last_guess_ts < 1.5:
        return False, "⏱️ Slow down: wait 1.5s between guesses."
    if self.solved:
        return False, "This round is already solved."
    if self.attempts >= 6:
        return False, "No attempts left."
    return True, ""

def apply_guess(self, guess: str) -> Attempt:
    self.last_guess_ts = time.time()
    tiles = evaluate_guess(self.secret, guess)
    self.attempts += 1
    att = Attempt(guess=guess, tiles=tiles)
    self.history.append(att)
    if is_win(tiles):
        self.solved = True
    return att
def today_str_utc() -> str:
# Deterministic daily by UTC date
import datetime
return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

def pick_secret(answers: List[str], daily_mode: bool, day_seed: Optional[int]=None) -> str:
if daily_mode:
random.seed(day_seed if day_seed is not None else int(time.time() // 86400))
return random.choice(answers)
return random.choice(answers)

-----------------------------
MongoDB (Motor) setup
-----------------------------
mongo_client: Optional[AsyncIOMotorClient] = None
db = None
users_col = None
history_col = None
daily_col = None

async def init_db():
global mongo_client, db, users_col, history_col, daily_col
if not MONGO_URI:
raise RuntimeError("MONGO_URI not set")
mongo_client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=10000)
db = mongo_client[MONGO_DB]
users_col = db["users"]
history_col = db["history"]
daily_col = db["daily"]
await users_col.create_index([("user_id", 1)], unique=True)
await users_col.create_index([("total_points", -1), ("wins", -1)])
await history_col.create_index([("ts", -1)])
await daily_col.create_index([("date", 1)], unique=True)

async def upsert_user(user_id: int, username: str, first_name: str, last_name: str):
await users_col.update_one(
{"user_id": user_id},
{"$set": {
"username": username,
"first_name": first_name,
"last_name": last_name,
"last_played_date": time.strftime("%Y-%m-%d"),
},
"$setOnInsert": {
"total_points": 0, "games_played": 0, "wins": 0,
"streak_current": 0, "streak_best": 0, "last_win_date": None
}},
upsert=True
)

async def record_result(user_id: int, username: str, word: str, attempts: int, points: int, chat_id: int, mode: str):
ts = int(time.time())
await history_col.insert_one({
"user_id": user_id, "username": username, "word": word,
"attempts": attempts, "points": points, "ts": ts,
"chat_id": chat_id, "mode": mode
})
await users_col.update_one(
{"user_id": user_id},
{"$inc": {"total_points": points, "games_played": 1, "wins": (1 if points>0 else 0)}}
)
if points > 0:
from datetime import date, timedelta
today = date.today().strftime("%Y-%m-%d")
yday = (date.today()-timedelta(days=1)).strftime("%Y-%m-%d")
user = await users_col.find_one({"user_id": user_id}, {"streak_current":1,"streak_best":1,"last_win_date":1})
sc = (user or {}).get("streak_current", 0) or 0
sb = (user or {}).get("streak_best", 0) or 0
last = (user or {}).get("last_win_date")
if last == yday: sc += 1
elif last == today: sc = sc
else: sc = 1
sb = max(sb, sc)
await users_col.update_one({"user_id": user_id}, {"$set": {"streak_current": sc, "streak_best": sb, "last_win_date": today}})

async def get_leaderboard(limit: int=10) -> List[Tuple]:
cur = users_col.find({}, {"_id":0, "username":1, "total_points":1, "wins":1, "streak_best":1}).sort(
[("total_points",-1), ("wins",-1)]
).limit(limit)
out = []
async for d in cur:
out.append((d.get("username"), d.get("total_points",0), d.get("wins",0), d.get("streak_best",0)))
return out

async def get_user_stats(user_id: int) -> Optional[Dict]:
d = await users_col.find_one({"user_id": user_id}, {"_id":0, "total_points":1, "games_played":1, "wins":1, "streak_current":1, "streak_best":1})
if not d: return None
g, w = d.get("games_played",0), d.get("wins",0)
winrate = round((w/g)*100, 2) if g else 0.0
return {
"total_points": d.get("total_points",0),
"games_played": g, "wins": w,
"streak_current": d.get("streak_current",0),
"streak_best": d.get("streak_best",0),
"winrate": winrate
}

async def set_daily_word(date_str: str, word: str):
await daily_col.update_one({"date": date_str}, {"$set": {"word": word}}, upsert=True)

async def get_daily_word(date_str: str) -> Optional[str]:
d = await daily_col.find_one({"date": date_str}, {"_id":0, "word":1})
return d["word"] if d else None

-----------------------------
Bot init and handlers
-----------------------------
app = Client("wordseek-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

single_sessions: Dict[int, Session] = {}
group_sessions: Dict[int, Session] = {}

async def ensure_user(m: Message):
u = m.from_user
await upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "")

async def get_secret(daily: bool) -> str:
if not daily:
return pick_secret(ANSWERS_EMBED, daily_mode=False)
d = today_str_utc()
s = await get_daily_word(d)
if s:
return s
day_seed = int(time.time() // 86400)
secret = pick_secret(ANSWERS_EMBED, daily_mode=True, day_seed=day_seed)
await set_daily_word(d, secret)
return secret

def active_session(m: Message) -> Tuple[str, Optional[Session]]:
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
idx = random.choice(range(5))
await m.reply_text(f"💡 Hint: The {idx+1}ᵗʰ letter is '{sess.secret[idx].upper()}'. Using a hint halves your final points for this round.")
else:
pool = "".join(sorted(set(sess.secret.upper())))
await m.reply_text(f"💡 Hint letters: {pool}. Using a hint halves your final points for this round.")

@app.on_message(filters.command("leaderboard"))
async def leaderboard_cmd(_, m: Message):
rows = await get_leaderboard(10)
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
stats = await get_user_stats(m.from_user.id)
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
if DICT_STRICT and g not in ALL_VALID:
await m.reply_text("❗Not in my dictionary. Try another valid 5-letter English word.")
return
att = sess.apply_guess(g)
hist = render_history([(a.guess, a.tiles) for a in sess.history])
await m.reply_text(f"{hist}\nAttempts used: {sess.attempts}/6")
if sess.solved:
pts = score_for_attempts(sess.attempts, sess.hint_used)
meaning = await fetch_definition(sess.secret)
u = m.from_user
await upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "")
await record_result(u.id, u.username or "", sess.secret, sess.attempts, pts, m.chat.id, mode)
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
await record_result(u.id, u.username or "", sess.secret, sess.attempts, 0, m.chat.id, mode)
if mode == "group": group_sessions.pop(m.chat.id, None)
else: single_sessions.pop(u.id, None)
await m.reply_text(f"😢 Game over — the word was {sess.secret.upper()}. Better luck next time! No points awarded. Use /new to try again.")

@app.on_message(filters.command("guess"))
async def guess_cmd(_, m: Message):
parts = m.text.split(maxsplit=1)
if len(parts) < 2:
await m.reply_text("Usage: /guess <5-letter word>")
return
await handle_guess(m, parts)

@app.on_message(filters.text & ~filters.command(["start","help","new","guess","giveup","leaderboard","score","rules","hint"]))
async def plain_guess(_, m: Message):
t = (m.text or "").strip()
if len(t) == 5 and t.isalpha():
await handle_guess(m, t)

async def main():
await init_db()
print("DB connected. Bot starting...")
await app.start()
# Pyrogram run-loop
await asyncio.Event().wait()

if name == "main":
# Heroku worker entry
asyncio.run(main())
