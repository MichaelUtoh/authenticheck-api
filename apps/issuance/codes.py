"""Shared handling of consumer-entered code strings.

Every place that resolves a code from user input (public verify, consumer reports,
deactivation, USSD) goes through `find_code` so they all treat spaces, dashes and
case the same way.

Lookup order: the input as typed (whitespace trimmed), then the canonical form
(upper-case, no spaces or dashes), then `Code.legacy_code` (the old 22-char
case-sensitive token that a converted code used to have, exact match only).
"""
import re
import secrets

# Consumer code format: 12 characters = 11 random + 1 check character.
# Alphabet drops look-alikes (0 O 1 I L) and U: digits 2-9 + 22 letters = 30 symbols.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 12
_BASE = len(ALPHABET)
_INDEX = {ch: i for i, ch in enumerate(ALPHABET)}

_SEPARATORS = re.compile(r"[\s-]+")


def clean_code(raw: str) -> str:
    """The code as typed, with surrounding whitespace removed."""
    return (raw or "").strip()


def canonical_code(raw: str) -> str:
    """Upper-case with all whitespace and dashes removed (e.g. `7kq2-m9xw 4prt` -> `7KQ2M9XW4PRT`)."""
    return _SEPARATORS.sub("", raw or "").upper()


def candidates(raw: str) -> list[str]:
    """Strings to try, most specific first, without duplicates."""
    seen: list[str] = []
    for value in (clean_code(raw), canonical_code(raw)):
        if value and value not in seen:
            seen.append(value)
    return seen


def find_code(raw: str, queryset=None):
    """Return the matching Code or None. Exact input beats the canonical form."""
    from .models import Code

    qs = Code.unscoped.all() if queryset is None else queryset
    values = candidates(raw)
    for value in values:
        found = qs.filter(code=value).first()
        if found is not None:
            return found
    # Printed pre-conversion codes are case-sensitive: only the exact input counts.
    return qs.filter(legacy_code=values[0]).first() if values else None


def _check_char(payload: str) -> str:
    """Luhn mod-N check character: catches any single wrong character and most swaps."""
    total, factor = 0, 2
    for ch in reversed(payload):
        addend = factor * _INDEX[ch]
        factor = 1 if factor == 2 else 2
        total += addend // _BASE + addend % _BASE
    return ALPHABET[(_BASE - total % _BASE) % _BASE]


def generate_code() -> str:
    """A new 12-character code from a cryptographically secure random source."""
    payload = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH - 1))
    return payload + _check_char(payload)


def is_valid_format(raw: str) -> bool:
    """True if `raw` (any case, with spaces/dashes) is a well-formed 12-char code with a good check char."""
    code = canonical_code(raw)
    if len(code) != CODE_LENGTH or any(ch not in _INDEX for ch in code):
        return False
    return _check_char(code[:-1]) == code[-1]
