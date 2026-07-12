#!/usr/bin/env python3
import argparse
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


def http_json(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_mihomo_config(proxies, mixed_port, controller):
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

    candidates = http_json(github_raw_url(gh_cfg), timeout=60)
    proxies = candidates.get("proxies", [])
    if not proxies:
        raise SystemExit("No proxies found in candidates JSON.")

    controller = mihomo_cfg.get("controller", "127.0.0.1:19090")
    runtime_config = build_mihomo_config(
        proxies=proxies,
        mixed_port=mihomo_cfg.get("mixed_port", 19091),
        controller=controller,
    )
    runtime_config_path = os.path.join(workdir, "mihomo-probe.yaml")
    write_json(runtime_config_path, runtime_config)

    mihomo_bin = find_bin(mihomo_cfg.get("bin", ""))
    proc = subprocess.Popen(
        [mihomo_bin, "-d", workdir, "-f", runtime_config_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        if not wait_controller(controller, mihomo_cfg.get("startup_wait_seconds", 3) + 5):
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


if __name__ == "__main__":
    main()

