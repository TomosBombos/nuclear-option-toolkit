#!/usr/bin/env python3
"""Read/write Nuclear Option's DedicatedServerConfig.json — ports, name, players, password.

Schema (the subset the installer owns): Port/QueryPort are {IsOverride, Value}; ServerName,
MaxPlayers, Password are scalars; ModdedServer is the STRING "true"/"false". We MERGE into
any existing file (preserving unknown keys like MissionRotation / VoteKick) and back it up
first, so re-running is idempotent and never clobbers a hand-tuned config.
"""
import json
import os
import time

CONFIG_NAME = "DedicatedServerConfig.json"

# The custom co-op rotation the toolkit ships (matches the missions in missions/ + the live
# server). Mission files install into MissionDirectory; this rotation references them by name.
_MISSION_NAMES = [
    "Escalation Co-op as BDF - Afternoon", "Escalation Co-op as BDF - Clear Skies",
    "Escalation Co-op as BDF - Dawn", "Escalation Co-op as BDF - Dusk",
    "Escalation Co-op as BDF - Night", "Escalation Co-op as BDF - Overcast",
    "Escalation Co-op as PALA - Afternoon", "Escalation Co-op as PALA - Clear Skies",
    "Escalation Co-op as PALA - Dawn", "Escalation Co-op as PALA - Dusk",
    "Escalation Co-op as PALA - Overcast", "Escalation Co-op as PALA - Thunderstorm",
    "Terminal Control Co-op as BDF - Dawn", "Terminal Control Co-op as BDF - Day",
    "Terminal Control Co-op as BDF - Dusk", "Terminal Control Co-op as PALA - Dawn",
    "Terminal Control Co-op as PALA - Day", "Terminal Control Co-op as PALA - Dusk",
    "Escalation",
]
_ROTATION = [{"Key": {"Group": "User", "Name": n}, "MaxTime": 7200.0} for n in _MISSION_NAMES]

# Defaults used only when creating a fresh config (no existing file to merge into).
DEFAULTS = {
    "MissionDirectory": "NuclearOption-Missions",
    "ModdedServer": "true",
    "Hidden": False,
    "ServerName": "My Nuclear Option Server",
    "Password": "",
    "MaxPlayers": 16,
    "RotationType": 2,
    "MissionRotation": _ROTATION,
}


def validate_ports(game_port, query_port):
    """Return '' if ok, else a human error string."""
    try:
        g, q = int(game_port), int(query_port)
    except (TypeError, ValueError):
        return "ports must be numbers"
    if not (1024 <= g <= 65535 and 1024 <= q <= 65535):
        return "ports must be between 1024 and 65535"
    if g == q:
        return "game port and query port must differ"
    return ""


def build_config(existing, game_port, query_port, server_name="", max_players=0,
                 password="", modded=True):
    cfg = dict(DEFAULTS)
    if isinstance(existing, dict):
        cfg.update(existing)                       # preserve everything they already had
    cfg["Port"] = {"IsOverride": True, "Value": int(game_port)}
    cfg["QueryPort"] = {"IsOverride": True, "Value": int(query_port)}
    if server_name:
        cfg["ServerName"] = server_name
    if max_players:
        cfg["MaxPlayers"] = int(max_players)
    if password:
        cfg["Password"] = password
    cfg["ModdedServer"] = "true" if modded else "false"
    return cfg


def write_config(dest_dir, game_port, query_port, server_name="", max_players=0,
                 password="", modded=True, ts=None):
    """Write DedicatedServerConfig.json into dest_dir, merging + backing-up any existing one.
    Returns (path, backup_or_None). Raises ValueError on invalid ports."""
    err = validate_ports(game_port, query_port)
    if err:
        raise ValueError(err)
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, CONFIG_NAME)
    existing, backup = None, None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                existing = json.load(f)
        except (OSError, ValueError):
            existing = None
        backup = path + ".bak-" + (ts or time.strftime("%Y%m%d-%H%M%S"))
        try:
            with open(path, "rb") as a, open(backup, "wb") as b:
                b.write(a.read())
        except OSError:
            backup = None
    cfg = build_config(existing, game_port, query_port, server_name, max_players, password, modded)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)
    return path, backup
