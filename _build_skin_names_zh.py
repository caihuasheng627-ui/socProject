# -*- coding: utf-8 -*-
"""Build zh-CN display-name map for skins currently in the DB.

Source: ByMykel/CSGO-API zh-CN skins_not_grouped.json
(official CS Simplified Chinese localization — same names used on BUFF/Steam CN).
"""
from __future__ import annotations

import json
import sqlite3
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "backend" / "data" / "skinvision.db"
OUT = ROOT / "js" / "skin-names-zh.js"
SRC = (
    "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/"
    "public/api/zh-CN/skins_not_grouped.json"
)
STEAM = (
    "https://raw.githubusercontent.com/EricZhu-42/SteamTradingSite-ID-Mapper/"
    "main/steam/730.json"
)


def fetch_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "CSVest-i18n/1.0"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.load(resp)


def main() -> None:
    conn = sqlite3.connect(DB)
    our = [r[0] for r in conn.execute("SELECT market_hash_name FROM skins")]
    conn.close()
    our_set = set(our)
    print(f"DB skins: {len(our)}")

    mapping: dict[str, str] = {}

    # Primary: game localization (ByMykel)
    print("Fetching ByMykel zh-CN…")
    items = fetch_json(SRC)
    for item in items:
        mhn = item.get("market_hash_name")
        name = item.get("name")
        if mhn in our_set and isinstance(name, str) and name.strip():
            mapping[mhn] = name.strip()
    print(f"ByMykel hits: {len(mapping)}")

    # Fallback: Steam trading-site mapper cn_name
    missing = our_set - set(mapping)
    if missing:
        print(f"Fetching EricZhu steam/730.json for {len(missing)} missing…")
        steam = fetch_json(STEAM)
        # steam format: { market_hash_name: { cn_name, en_name, ... } }
        if isinstance(steam, dict):
            for mhn, meta in steam.items():
                if mhn not in missing:
                    continue
                cn = None
                if isinstance(meta, dict):
                    cn = meta.get("cn_name") or meta.get("name_cn")
                if isinstance(cn, str) and cn.strip():
                    mapping[mhn] = cn.strip()
        print(f"After Steam fallback: {len(mapping)}")

    # Derive StatTrak™ / Souvenir names from the non-prefixed counterpart.
    for mhn in list(our_set - set(mapping)):
        base = None
        prefix_zh = None
        if mhn.startswith("StatTrak™ "):
            base = mhn[len("StatTrak™ ") :]
            prefix_zh = "StatTrak™ "
        elif mhn.startswith("Souvenir "):
            base = mhn[len("Souvenir ") :]
            prefix_zh = "纪念品 "
        if base and base in mapping:
            zh = mapping[base]
            # Avoid double-prefix if source already localized with StatTrak
            if zh.startswith("StatTrak™ ") or zh.startswith("纪念品"):
                mapping[mhn] = zh
            else:
                mapping[mhn] = prefix_zh + zh

    still = sorted(our_set - set(mapping))
    print(f"Still missing: {len(still)}")
    miss_path = ROOT / "_skin_names_missing.txt"
    miss_path.write_text("\n".join(still), encoding="utf-8")

    checks = [
        "AK-47 | Fire Serpent (Minimal Wear)",
        "AWP | Dragon Lore (Minimal Wear)",
        "M4A4 | Howl (Factory New)",
        "StatTrak™ AK-47 | Crossfade (Field-Tested)",
    ]
    (ROOT / "_skin_names_check.txt").write_text(
        "\n".join(f"{k} => {mapping.get(k, 'MISSING')}" for k in checks),
        encoding="utf-8",
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: mapping[k] for k in sorted(mapping)}
    js = (
        "// Auto-generated Chinese display names (CS schinese / BUFF-style).\n"
        "// Do not hand-edit; regenerate with _build_skin_names_zh.py\n"
        "window.SKIN_NAMES_ZH = "
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + ";\n"
    )
    OUT.write_text(js, encoding="utf-8")
    print(f"Wrote {OUT.name} bytes={OUT.stat().st_size} names={len(payload)}")


if __name__ == "__main__":
    main()
