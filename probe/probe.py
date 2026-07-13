#!/usr/bin/env python3
import argparse
import base64
import concurrent.futures
import datetime as dt
import json
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def find_bin(configured):
    if configured and os.path.exists(configured):
        return configured
    for name in ("mihomo", "clash"):
        path = shutil.which(name)
        if path:
            return path
    raise SystemExit("Cannot find mihomo/clash binary. Set mihomo.bin in config.")


def github_raw_url(gh):
    owner = gh["owner"]
    repo = gh["repo"]
    branch = gh.get("branch", "main")
    path = gh.get("candidates_path", "data/candidates.json")
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"


def github_contents_url(gh):
    owner = gh["owner"]
    repo = gh["repo"]
    path = gh.get("candidates_path", "data/candidates.json")
    return f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"


def github_headers(gh):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "china-proxy-probe",
    }
    token = gh.get("token") or os.environ.get("GITHUB_TOKEN", "")
    if token and token != "ghp_replace_me":
        headers["Authorization"] = f"Bearer {token}"
    return headers


def http_json(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_candidates(gh, timeout=30):
    branch = gh.get("branch", "main")
    api_url = github_contents_url(gh) + "?" + urllib.parse.urlencode({"ref": branch})
    try:
        payload = http_json(api_url, headers=github_headers(gh), timeout=timeout)
        encoded = payload.get("content", "")
        if payload.get("encoding") == "base64" and encoded:
            content = base64.b64decode(encoded).decode("utf-8")
            return json.loads(content)
    except Exception as e:
        print(f"warning: failed to fetch candidates through GitHub API: {e}")

    print("warning: falling back to raw.githubusercontent.com")
    return http_json(github_raw_url(gh), timeout=timeout)


def build_mihomo_config(proxies, mixed_port, controller):
    proxies = unique_proxy_names(proxies)
    names = [p.get("name") for p in proxies if p.get("name")]
    return {
        "mixed-port": int(mixed_port),
        "allow-lan": False,
        "mode": "Rule",
        "log-level": "silent",
        "external-controller": controller,
        "proxies": proxies,
        "proxy-groups": [
            {
                "name": "probe",
                "type": "select",
                "proxies": names or ["DIRECT"],
            }
        ],
        "rules": ["MATCH,probe"],
    }


def unique_proxy_names(proxies):
    used = set()
    counts = {}
    renamed = []
    for proxy in proxies:
        proxy = dict(proxy)
        base = str(proxy.get("name") or "proxy").strip() or "proxy"
        counts[base] = counts.get(base, 0) + 1
        name = base if counts[base] == 1 else f"{base} #{counts[base]}"
        while name in used:
            counts[base] += 1
            name = f"{base} #{counts[base]}"
        proxy["name"] = name
        used.add(name)
        renamed.append(proxy)
    return renamed


def wait_controller(controller, timeout_seconds):
    deadline = time.time() + timeout_seconds
    url = f"http://{controller}/version"
    while time.time() < deadline:
        try:
            http_json(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.25)
    return False


def tail_file(path, max_lines=80):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as exc:
        return f"(cannot read {path}: {exc})"
    return "".join(lines[-max_lines:]).rstrip() or "(empty log)"


def test_proxy(controller, proxy, timeout_ms, test_url):
    name = proxy.get("name", "")
    encoded = urllib.parse.quote(name, safe="")
    qs = urllib.parse.urlencode({"timeout": int(timeout_ms), "url": test_url})
    url = f"http://{controller}/proxies/{encoded}/delay?{qs}"

    started = time.time()
    result = {
        "name": name,
        "type": proxy.get("type", ""),
        "server": proxy.get("server", ""),
        "port": proxy.get("port", ""),
        "alive": False,
    }
    try:
        data = http_json(url, timeout=max(3, int(timeout_ms / 1000) + 2))
        delay = data.get("delay")
        result.update(
            {
                "alive": isinstance(delay, int) and delay >= 0,
                "delay": delay,
                "elapsed_ms": int((time.time() - started) * 1000),
            }
        )
    except Exception as e:
        result.update(
            {
                "error": str(e),
                "elapsed_ms": int((time.time() - started) * 1000),
            }
        )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    cfg = load_json(args.config)
    probe_cfg = cfg["probe"]
    mihomo_cfg = cfg["mihomo"]
    gh_cfg = cfg["github"]

    workdir = probe_cfg.get("workdir", "/tmp/china-proxy-probe")
    os.makedirs(workdir, exist_ok=True)

    candidates = fetch_candidates(gh_cfg, timeout=60)
    proxies = unique_proxy_names(candidates.get("proxies", []))
    if not proxies:
        raise SystemExit("No proxies found in candidates JSON.")

    controller = mihomo_cfg.get("controller", "127.0.0.1:19090")
    runtime_config = build_mihomo_config(
        proxies=proxies,
        mixed_port=mihomo_cfg.get("mixed_port", 19091),
        controller=controller,
    )
    runtime_config_path = os.path.join(workdir, "mihomo-probe.yaml")
    runtime_log_path = os.path.join(workdir, "mihomo.log")
    write_json(runtime_config_path, runtime_config)

    mihomo_bin = find_bin(mihomo_cfg.get("bin", ""))
    log_file = open(runtime_log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [mihomo_bin, "-d", workdir, "-f", runtime_config_path],
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    try:
        if not wait_controller(controller, mihomo_cfg.get("startup_wait_seconds", 3) + 5):
            if proc.poll() is not None:
                print(f"mihomo exited early with code {proc.returncode}.")
            else:
                print("mihomo process is still running, but controller is not responding.")
            print(f"runtime config: {runtime_config_path}")
            print(f"mihomo log: {runtime_log_path}")
            print("----- mihomo log tail -----")
            print(tail_file(runtime_log_path))
            print("----- end mihomo log -----")
            raise SystemExit("mihomo controller did not become ready.")

        timeout_ms = int(probe_cfg.get("timeout_ms", 5000))
        test_url = probe_cfg.get("test_url", "https://www.gstatic.com/generate_204")
        max_workers = max(1, int(probe_cfg.get("max_workers", 8)))

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [
                pool.submit(test_proxy, controller, p, timeout_ms, test_url)
                for p in proxies
                if p.get("name")
            ]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        alive = [r for r in results if r.get("alive")]
        report = {
            "schema": 1,
            "probe_id": probe_cfg.get("id", "home"),
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "test_url": test_url,
            "timeout_ms": timeout_ms,
            "total": len(results),
            "alive": len(alive),
            "results": sorted(results, key=lambda x: (not x.get("alive"), x.get("delay") or 999999, x["name"])),
        }
        write_json(args.output, report)
        print(f"probe complete: {len(alive)}/{len(results)} alive")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()


if __name__ == "__main__":
    main()
