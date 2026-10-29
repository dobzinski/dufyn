"""Macro scan selection only: never accepts host/IP selectors."""
SCAN_ORDER = ("assets", "targets", "ports", "exclusive", "certificates", "dns", "ptr")


def normalize_scans(value):
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("--scans must contain macro scan names")
    selected = []
    for entry in value:
        if not isinstance(entry, str):
            raise ValueError("Scan names must be strings")
        selected.extend(part.strip().lower() for part in entry.split(","))
    if selected == ["all"]:
        return list(SCAN_ORDER), False
    if any(name not in SCAN_ORDER for name in selected):
        raise ValueError("Valid scans: all, " + ", ".join(SCAN_ORDER))
    if len(selected) != len(set(selected)):
        raise ValueError("Duplicate scan names")
    return [name for name in SCAN_ORDER if name in selected], True
