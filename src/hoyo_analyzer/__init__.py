"""Independent desktop release versions and wire protocol compatibility."""

MASTER_VERSION = "V2.3.0"
SLAVE_VERSION = "V2.6.0"
PROTOCOL_VERSION = 1
# Backward-compatible import for external tooling; applications use role versions.
__version__ = MASTER_VERSION


def client_version(role: str) -> str:
    if role == "master":
        return MASTER_VERSION
    if role == "slave":
        return SLAVE_VERSION
    raise ValueError(f"Unknown client role: {role}")


def slave_protocol(device: dict) -> int | None:
    explicit = device.get("protocol_version")
    if explicit is not None:
        return explicit if isinstance(explicit, int) and not isinstance(explicit, bool) else None
    # Released V1.x and V2.0 slaves predate explicit protocol reporting.
    import re
    match = re.fullmatch(r"[Vv]?(1\.\d+\.\d+|2\.0\.0)", str(device.get("app_version") or ""))
    return 1 if match else None
