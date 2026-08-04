"""Random identity and password generation.

Reusing one name/password across every account is a trivially detectable
pattern, so each account gets its own realistic-looking identity.
"""
from __future__ import annotations

import re
import secrets
import string
from dataclasses import dataclass

FIRST_NAMES = [
    "Lucas", "Martin", "Diego", "Pablo", "Nicolas", "Javier", "Andres", "Sergio",
    "Bruno", "Ivan", "Marco", "Adrian", "Hugo", "Alex", "Daniel", "Emilio",
    "Sofia", "Valeria", "Camila", "Lucia", "Elena", "Paula", "Nadia", "Irene",
    "Thomas", "Oliver", "Felix", "Anton", "Viktor", "Milan", "Rafael", "Simon",
]

LAST_NAMES = [
    "Garcia", "Rossi", "Silva", "Moreno", "Duarte", "Navarro", "Costa", "Vidal",
    "Ferrer", "Bianchi", "Lopes", "Marino", "Sorel", "Kovac", "Novak", "Weber",
    "Fischer", "Muller", "Schmidt", "Bauer", "Larsen", "Nilsen", "Vega", "Rico",
    "Salas", "Ortega", "Pardo", "Reyes", "Cano", "Bravo", "Solis", "Arce",
]

SPECIALS = "!@#$%^&*?."


@dataclass
class Identity:
    first_name: str
    last_name: str
    password: str


def password_problem(password: str):
    """Return a human-readable problem string, or None if the password is fine.

    ExitLag requires >= 8 chars with lower, upper, number and special.
    """
    if len(password) < 8:
        return "must be at least 8 characters long"
    if not re.search(r"[a-z]", password):
        return "must contain a lowercase letter"
    if not re.search(r"[A-Z]", password):
        return "must contain an uppercase letter"
    if not re.search(r"[0-9]", password):
        return "must contain a number"
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        return "must contain a special character"
    return None


def random_password(length: int = 16) -> str:
    """Generate a random password that always satisfies ExitLag's rules."""
    if length < 10:
        length = 10
    pools = [string.ascii_lowercase, string.ascii_uppercase, string.digits, SPECIALS]
    chars = [secrets.choice(pool) for pool in pools]
    everything = "".join(pools)
    chars += [secrets.choice(everything) for _ in range(length - len(chars))]
    # Shuffle without bias.
    for idx in range(len(chars) - 1, 0, -1):
        swap = secrets.randbelow(idx + 1)
        chars[idx], chars[swap] = chars[swap], chars[idx]
    candidate = "".join(chars)
    return candidate if password_problem(candidate) is None else random_password(length)


def make_identity(cfg) -> Identity:
    """Build an Identity from the signup config, randomizing what is not fixed."""
    if cfg.randomize_identity or not cfg.first_name:
        first = secrets.choice(FIRST_NAMES)
    else:
        first = cfg.first_name
    if cfg.randomize_identity or not cfg.last_name:
        last = secrets.choice(LAST_NAMES)
    else:
        last = cfg.last_name
    password = cfg.password or random_password()
    return Identity(first_name=first, last_name=last, password=password)
