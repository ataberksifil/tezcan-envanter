"""Human-readable Location path helpers. Path is not persisted (DEC-023)."""

from __future__ import annotations

from locations.models import Location


def location_path_label(location: Location) -> str:
    """Return a human-readable ancestor path, leaf last.

    Example: ``Elektrik Ambarı (EA) → R03 → G07``.
    """
    parts: list[str] = []
    current: Location | None = location
    seen: set = set()
    while current is not None and current.pk not in seen:
        seen.add(current.pk)
        if current.name == current.code:
            parts.append(current.code)
        else:
            parts.append(f"{current.name} ({current.code})")
        current = current.parent
    return " → ".join(reversed(parts))
