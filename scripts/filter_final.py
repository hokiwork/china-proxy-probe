#!/usr/bin/env python3
import argparse
import glob
import json
import os

import yaml


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_alive(pattern):
    alive_names = set()
    reports = []
    for path in glob.glob(pattern):
        with open(path, "r", encoding="utf-8") as f:
            report = json.load(f)
        reports.append(report)
        for item in report.get("results", []):
            if item.get("alive") and item.get("name"):
                alive_names.add(item["name"])
    return alive_names, reports


def write_yaml(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", default="data/candidates.yaml")
    parser.add_argument("--alive", default="data/alive/*.json")
    parser.add_argument("--output", default="data/final.yaml")
    parser.add_argument("--min-probes", type=int, default=1)
    args = parser.parse_args()

    config = load_yaml(args.candidates)
    proxies = config.get("proxies") or []
    if not proxies:
        raise SystemExit("No candidate proxies found.")

    counts = {}
    reports = []
    for path in glob.glob(args.alive):
        with open(path, "r", encoding="utf-8") as f:
            report = json.load(f)
        reports.append(report)
        for item in report.get("results", []):
            if item.get("alive") and item.get("name"):
                counts[item["name"]] = counts.get(item["name"], 0) + 1

    keep = [p for p in proxies if counts.get(p.get("name", ""), 0) >= args.min_probes]
    keep_names = [p["name"] for p in keep if p.get("name")]

    final = {
        "mixed-port": config.get("mixed-port", 7890),
        "allow-lan": False,
        "mode": "Rule",
        "log-level": "info",
        "proxies": keep,
        "proxy-groups": [
            {
                "name": "PROXY",
                "type": "url-test",
                "proxies": keep_names or ["DIRECT"],
                "url": "https://www.gstatic.com/generate_204",
                "interval": 300,
            }
        ],
        "rules": ["MATCH,PROXY"],
    }
    write_yaml(args.output, final)

    json_path = os.path.splitext(args.output)[0] + ".json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "total_candidates": len(proxies),
                "total_final": len(keep),
                "probe_reports": len(reports),
                "names": keep_names,
            },
            f,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        f.write("\n")

    print(f"filtered {len(keep)}/{len(proxies)} proxies into {args.output}")


if __name__ == "__main__":
    main()

