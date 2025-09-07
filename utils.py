
# utils.py
import aiohttp
from datetime import datetime, timezone
from typing import List, Tuple

GREEN = "🟩"
YELLOW = "🟨"
GREY = "🟥"

def load_wordlist(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as f:
        return [w.strip().lower() for w in f if w.strip()]

def is_alpha5(word: str) -> bool:
    return len(word) == 5 and word.isalpha()

def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def render_history(history: List[Tuple[str, list]]) -> str:
    return "\n".join(f"{' '.join(tiles)}  {guess.upper()}" for guess, tiles in history)

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
  
