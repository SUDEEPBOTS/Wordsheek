from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
import random, time

GREEN, YELLOW, GREY = "🟩", "🟨", "🟥"

def _counts(s: str) -> Dict[str, int]:
    c: Dict[str, int] = {}
    for ch in s: c[ch] = c.get(ch, 0) + 1
    return c

def evaluate_guess(secret: str, guess: str) -> List[str]:
    secret, guess = secret.lower(), guess.lower()
    tiles = [GREY]*5
    inv = _counts(secret)
    for i in range(5):
        if guess[i] == secret[i]:
            tiles[i] = GREEN
            inv[guess[i]] -= 1
    for i in range(5):
        if tiles[i] == GREEN: continue
        ch = guess[i]
        if inv.get(ch, 0) > 0:
            tiles[i] = YELLOW
            inv[ch] -= 1
        else:
            tiles[i] = GREY
    return tiles

def is_win(tiles: List[str]) -> bool:
    return all(t == GREEN for t in tiles)

def score_for_attempts(n: int, hint_used: bool) -> int:
    base = {1:100,2:60,3:40,4:25,5:15,6:10}.get(n, 0)
    return int(base*0.5) if hint_used else base

@dataclass
class Attempt:
    guess: str
    tiles: List[str]

@dataclass
class Session:
    user_id: int
    chat_id: int
    mode: str  # "single"|"group"
    secret: str
    attempts: int = 0
    history: List[Attempt] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    solved: bool = False
    hint_used: bool = False
    last_guess_ts: float = 0.0

    def can_guess(self) -> Tuple[bool, str]:
        now = time.time()
        if now - self.last_guess_ts < 1.5:
            return False, "⏱️ Slow down: wait 1.5s between guesses."
        if self.solved: return False, "This round is already solved."
        if self.attempts >= 6: return False, "No attempts left."
        return True, ""

    def apply_guess(self, guess: str) -> Attempt:
        self.last_guess_ts = time.time()
        tiles = evaluate_guess(self.secret, guess)
        self.attempts += 1
        att = Attempt(guess=guess, tiles=tiles)
        self.history.append(att)
        if is_win(tiles): self.solved = True
        return att

def pick_secret(answers: List[str], daily_mode: bool, day_seed: Optional[int]=None) -> str:
    random.seed(day_seed if daily_mode else None)
    return random.choice(answers)
  
