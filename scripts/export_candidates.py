#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os

import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/candidates.yaml")
    parser.add_argument("--output", default="data/candidates.json")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    proxies = config.get("proxies") or []
    if not isinstance(proxies, list):
        raise SystemExit("Expected top-level 'proxies' to be a list.")

    seen = set()
    cleaned = []
    for proxy in proxies:
        if not isinstance(proxy, dict):
            continue
        name = str(proxy.get("name", "")).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        cleaned.append(proxy)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(
            {
                "schema": 1,
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "source": args.input,
                "total": len(cleaned),
                "proxies": cleaned,
            },
            f,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        f.write("\n")

    print(f"exported {len(cleaned)} proxies to {args.output}")


if __name__ == "__main__":
    main()

