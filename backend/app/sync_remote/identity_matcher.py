from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Iterable, Literal


MatchStatus = Literal["resolved", "ambiguous", "unmatched"]


@dataclass(frozen=True)
class DirectoryMatch:
    status: MatchStatus
    department: str | None = None
    staff_id: str | None = None


def normalize_display_name(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def match_directory_entries(
    observed_names: Iterable[str],
    directory: Iterable[dict],
) -> dict[str, DirectoryMatch]:
    active: dict[str, list[dict]] = {}
    for member in directory:
        if not member.get("active"):
            continue
        normalized = normalize_display_name(member.get("display_name"))
        if normalized:
            active.setdefault(normalized, []).append(member)

    results: dict[str, DirectoryMatch] = {}
    for observed in observed_names:
        candidates = active.get(normalize_display_name(observed), [])
        if not candidates:
            results[observed] = DirectoryMatch("unmatched")
        elif len(candidates) > 1:
            results[observed] = DirectoryMatch("ambiguous")
        else:
            member = candidates[0]
            departments = tuple(
                normalized
                for value in member.get("departments") or ()
                if (normalized := normalize_display_name(value))
            )
            results[observed] = DirectoryMatch(
                "resolved",
                department=" / ".join(dict.fromkeys(departments)) or None,
                staff_id=str(member.get("staff_id") or "") or None,
            )
    return results
