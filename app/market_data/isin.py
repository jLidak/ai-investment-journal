"""ISIN format and Luhn checksum validation (not proof of instrument identity)."""

import re


def is_valid_isin(isin: str) -> bool:
    if not isinstance(isin, str):
        return False
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin):
        return False
    expanded = "".join(
        char if char.isdigit() else str(ord(char) - ord("A") + 10)
        for char in isin
    )
    total = 0
    for position, char in enumerate(reversed(expanded)):
        digit = int(char)
        if position % 2 == 1:
            digit *= 2
        total += digit // 10 + digit % 10
    return total % 10 == 0
