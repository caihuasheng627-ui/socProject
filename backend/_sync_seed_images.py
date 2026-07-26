"""Export / sync Steam image_url into seed DB + JSON sidecar."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "skinvision.db"
SEED = ROOT / "seed" / "skinvision.db"
JSON_OUT = ROOT / "seed" / "skin_image_urls.json"


def cols(c: sqlite3.Connection) -> set[str]:
    return {r[1] for r in c.execute("PRAGMA table_info(skins)")}


def load_imgs(path: Path) -> dict[str, str]:
    c = sqlite3.connect(path)
    try:
        if "image_url" not in cols(c):
            return {}
        return {
            r[0]: r[1]
            for r in c.execute(
                "SELECT market_hash_name, image_url FROM skins "
                "WHERE image_url IS NOT NULL AND length(image_url)>0"
            )
        }
    finally:
        c.close()


def main() -> None:
    imgs = load_imgs(DATA)
    if not imgs:
        raise SystemExit("no image_url in data DB")
    JSON_OUT.write_text(json.dumps(imgs, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {JSON_OUT} ({len(imgs)} urls)")

    dst = sqlite3.connect(SEED)
    if "image_url" not in cols(dst):
        dst.execute("ALTER TABLE skins ADD COLUMN image_url TEXT")
    updated = 0
    for name, url in imgs.items():
        cur = dst.execute(
            "UPDATE skins SET image_url=? WHERE market_hash_name=?",
            (url, name),
        )
        updated += cur.rowcount
    dst.commit()
    n = dst.execute(
        "SELECT SUM(CASE WHEN image_url IS NOT NULL AND length(image_url)>0 "
        "THEN 1 ELSE 0 END) FROM skins"
    ).fetchone()[0]
    dst.close()
    print(f"seed updated={updated} with_url={n}")


if __name__ == "__main__":
    main()
