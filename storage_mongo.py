import os, time
from typing import Optional, List, Tuple, Dict
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING

_client: AsyncIOMotorClient | None = None
_db = None
_users = None
_history = None
_daily = None

async def init_db():
    global _client, _db, _users, _history, _daily
    uri = os.getenv("MONGO_URI", "")
    dbname = os.getenv("MONGO_DB", "wordseek")
    if not uri:
        raise RuntimeError("MONGO_URI not set")
    _client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=10000)
    _db = _client[dbname]
    _users = _db["users"]
    _history = _db["history"]
    _daily = _db["daily"]
    await _users.create_index([("user_id", ASCENDING)], unique=True)
    await _users.create_index([("total_points", DESCENDING), ("wins", DESCENDING)])
    await _history.create_index([("ts", DESCENDING)])
    await _daily.create_index([("date", ASCENDING)], unique=True)

async def upsert_user(user_id: int, username: str, first_name: str, last_name: str):
    await _users.update_one(
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
    await _history.insert_one({
        "user_id": user_id, "username": username, "word": word,
        "attempts": attempts, "points": points, "ts": ts,
        "chat_id": chat_id, "mode": mode
    })
    await _users.update_one(
        {"user_id": user_id},
        {"$inc": {"total_points": points, "games_played": 1, "wins": (1 if points>0 else 0)}}
    )
    if points > 0:
        from datetime import date, timedelta
        today = date.today().strftime("%Y-%m-%d")
        yday = (date.today()-timedelta(days=1)).strftime("%Y-%m-%d")
        user = await _users.find_one({"user_id": user_id}, {"streak_current":1,"streak_best":1,"last_win_date":1})
        sc = (user or {}).get("streak_current", 0) or 0
        sb = (user or {}).get("streak_best", 0) or 0
        last = (user or {}).get("last_win_date")
        if last == yday: sc += 1
        elif last == today: sc = sc
        else: sc = 1
        sb = max(sb, sc)
        await _users.update_one({"user_id": user_id}, {"$set": {"streak_current": sc, "streak_best": sb, "last_win_date": today}})

async def get_leaderboard(limit: int=10) -> List[Tuple]:
    cur = _users.find({}, {"_id":0, "username":1, "total_points":1, "wins":1, "streak_best":1}).sort(
        [("total_points",-1), ("wins",-1)]
    ).limit(limit)
    out = []
    async for d in cur:
        out.append((d.get("username"), d.get("total_points",0), d.get("wins",0), d.get("streak_best",0)))
    return out

async def get_user_stats(user_id: int) -> Optional[Dict]:
    d = await _users.find_one({"user_id": user_id}, {"_id":0, "total_points":1, "games_played":1, "wins":1, "streak_current":1, "streak_best":1})
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

async def get_history_recent(limit: int=20) -> List[Tuple]:
    cur = _history.find({}, {"_id":0,"username":1,"word":1,"attempts":1,"points":1,"ts":1}).sort("ts",-1).limit(limit)
    out = []
    async for d in cur:
        out.append((d.get("username"), d.get("word"), d.get("attempts"), d.get("points"), d.get("ts")))
    return out

async def set_daily_word(date_str: str, word: str):
    await _daily.update_one({"date": date_str}, {"$set": {"word": word}}, upsert=True)

async def get_daily_word(date_str: str) -> Optional[str]:
    d = await _daily.find_one({"date": date_str}, {"_id":0, "word":1})
    return d["word"] if d else None
      
