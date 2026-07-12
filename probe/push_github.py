#!/usr/bin/env python3
import argparse
import base64
import json
import os
import urllib.error
import urllib.request


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def request_json(method, url, token, payload=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "china-proxy-probe",
    }
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--file", required=True)
    args = parser.parse_args()

    cfg = load_json(args.config)["github"]
    token = cfg.get("token") or os.environ.get("GITHUB_TOKEN", "")
    if not token or token == "ghp_replace_me":
        raise SystemExit("Missing github.token or GITHUB_TOKEN.")

    owner = cfg["owner"]
    repo = cfg["repo"]
    branch = cfg.get("branch", "main")
    path = cfg.get("alive_path", "data/alive/home.json")
    base = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"

    sha = None
    try:
        current = request_json("GET", f"{base}?ref={branch}", token)
        sha = current.get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise

    with open(args.file, "rb") as f:
        content = base64.b64encode(f.read()).decode("ascii")

    payload = {
        "message": f"Update probe result {path}",
        "content": content,
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    request_json("PUT", base, token, payload)
    print(f"uploaded {path}")


if __name__ == "__main__":
    main()

