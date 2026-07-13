#!/usr/bin/env python3
import argparse
import base64
import datetime as dt
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

import yaml


PROXY_SCHEMES = ("ss", "ssr", "vmess", "vless", "trojan", "hysteria2", "hy2", "tuic")
PROXY_LINK_RE = re.compile(r"\b(?:" + "|".join(PROXY_SCHEMES) + r")://[^\s<>\"'\\]+", re.I)
HTTP_LINK_RE = re.compile(r"https?://[^\s<>\"'\\]+", re.I)


def fetch_text(url, timeout=30):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 china-proxy-probe/1.0",
            "Accept": "text/html,application/yaml,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
    return data.decode(charset, errors="replace")


def b64decode_any_text(value):
    compact = re.sub(r"\s+", "", value)
    if not compact:
        return None
    padding = "=" * (-len(compact) % 4)
    try:
        decoded = base64.urlsafe_b64decode((compact + padding).encode("ascii"))
    except Exception:
        return None
    return decoded.decode("utf-8", errors="replace")


def b64decode_subscription_text(value):
    text = b64decode_any_text(value)
    if not text:
        return None
    if "://" not in text and "proxies:" not in text:
        return None
    return text


def maybe_subscription_text(text):
    decoded = b64decode_subscription_text(text)
    return decoded or text


def clean_link(link):
    link = html.unescape(link)
    link = urllib.parse.unquote(link)
    return link.rstrip(".,;，。；)]}")


def extract_proxy_links(text):
    text = maybe_subscription_text(html.unescape(text))
    return [clean_link(match.group(0)) for match in PROXY_LINK_RE.finditer(text)]


def extract_http_links(text):
    return [clean_link(match.group(0)) for match in HTTP_LINK_RE.finditer(html.unescape(text))]


def safe_name(value, fallback):
    value = urllib.parse.unquote(value or "").strip()
    value = re.sub(r"[\r\n\t]+", " ", value)
    return value[:120] or fallback


def parse_host_port(netloc, default_port=None):
    if "@" in netloc:
        _, netloc = netloc.rsplit("@", 1)
    if netloc.startswith("["):
        end = netloc.find("]")
        host = netloc[1:end]
        rest = netloc[end + 1 :]
        port = int(rest[1:]) if rest.startswith(":") and rest[1:] else default_port
        return host, port
    if ":" not in netloc:
        return netloc, default_port
    host, port = netloc.rsplit(":", 1)
    return host, int(port) if port else default_port


def parse_ss(link):
    raw = link[5:]
    name = safe_name(urllib.parse.urlsplit(link).fragment, "ss")
    raw = raw.split("#", 1)[0]
    if "@" not in raw:
        decoded = b64decode_any_text(raw)
        if decoded:
            raw = decoded
    userinfo, server_part = raw.rsplit("@", 1)
    if ":" not in userinfo:
        decoded = b64decode_any_text(userinfo)
        if decoded:
            userinfo = decoded
    method, password = urllib.parse.unquote(userinfo).split(":", 1)
    server, port = parse_host_port(server_part)
    return {
        "name": name,
        "type": "ss",
        "server": server,
        "port": port,
        "cipher": method,
        "password": password,
    }


def parse_vmess(link):
    payload = link[8:]
    decoded = b64decode_any_text(payload)
    if not decoded:
        raise ValueError("invalid vmess payload")
    data = json.loads(decoded)
    name = safe_name(data.get("ps"), "vmess")
    proxy = {
        "name": name,
        "type": "vmess",
        "server": data.get("add"),
        "port": int(data.get("port")),
        "uuid": data.get("id"),
        "alterId": int(data.get("aid") or 0),
        "cipher": data.get("scy") or data.get("cipher") or "auto",
        "tls": str(data.get("tls", "")).lower() == "tls",
        "network": data.get("net") or "tcp",
    }
    host = data.get("host")
    path = data.get("path")
    if proxy["network"] == "ws" and (host or path):
        proxy["ws-opts"] = {"headers": {"Host": host or ""}, "path": path or "/"}
    sni = data.get("sni")
    if sni:
        proxy["servername"] = sni
    return proxy


def parse_vless(link):
    parsed = urllib.parse.urlsplit(link)
    uuid = parsed.username
    server, port = parse_host_port(parsed.netloc)
    query = urllib.parse.parse_qs(parsed.query)
    network = query.get("type", ["tcp"])[0]
    security = query.get("security", [""])[0]
    proxy = {
        "name": safe_name(parsed.fragment, "vless"),
        "type": "vless",
        "server": server,
        "port": port,
        "uuid": uuid,
        "network": network,
        "tls": security in ("tls", "reality"),
    }
    if security:
        proxy["client-fingerprint"] = query.get("fp", ["chrome"])[0]
    sni = query.get("sni", query.get("peer", [""]))[0]
    if sni:
        proxy["servername"] = sni
    flow = query.get("flow", [""])[0]
    if flow:
        proxy["flow"] = flow
    path = query.get("path", [""])[0]
    host = query.get("host", [""])[0]
    if network == "ws":
        proxy["ws-opts"] = {"path": path or "/", "headers": {"Host": host or sni or ""}}
    return proxy


def parse_trojan(link):
    parsed = urllib.parse.urlsplit(link)
    server, port = parse_host_port(parsed.netloc, 443)
    query = urllib.parse.parse_qs(parsed.query)
    proxy = {
        "name": safe_name(parsed.fragment, "trojan"),
        "type": "trojan",
        "server": server,
        "port": port,
        "password": urllib.parse.unquote(parsed.username or ""),
        "sni": query.get("sni", query.get("peer", [""]))[0] or server,
    }
    network = query.get("type", [""])[0]
    if network:
        proxy["network"] = network
    if network == "ws":
        proxy["ws-opts"] = {
            "path": query.get("path", ["/"])[0] or "/",
            "headers": {"Host": query.get("host", [proxy["sni"]])[0]},
        }
    return proxy


def parse_hysteria2(link):
    parsed = urllib.parse.urlsplit(link)
    server, port = parse_host_port(parsed.netloc, 443)
    query = urllib.parse.parse_qs(parsed.query)
    return {
        "name": safe_name(parsed.fragment, "hysteria2"),
        "type": "hysteria2",
        "server": server,
        "port": port,
        "password": urllib.parse.unquote(parsed.username or ""),
        "sni": query.get("sni", [""])[0] or server,
        "skip-cert-verify": query.get("insecure", ["0"])[0] in ("1", "true"),
    }


def parse_proxy_link(link):
    scheme = link.split("://", 1)[0].lower()
    if scheme == "ss":
        return parse_ss(link)
    if scheme == "vmess":
        return parse_vmess(link)
    if scheme == "vless":
        return parse_vless(link)
    if scheme == "trojan":
        return parse_trojan(link)
    if scheme in ("hysteria2", "hy2"):
        return parse_hysteria2(link)
    raise ValueError(f"unsupported scheme: {scheme}")


def proxies_from_clash(text):
    data = yaml.safe_load(text) or {}
    proxies = data.get("proxies") or []
    return [item for item in proxies if isinstance(item, dict)]


def proxies_from_text(text):
    text = maybe_subscription_text(text)
    try:
        proxies = proxies_from_clash(text)
        if proxies:
            return proxies
    except Exception:
        pass

    proxies = []
    for link in extract_proxy_links(text):
        try:
            proxy = parse_proxy_link(link)
        except Exception as exc:
            print(f"skip unsupported link: {exc}")
            continue
        proxies.append(proxy)
    return proxies


def telegram_url(source):
    if source.get("url"):
        return source["url"]
    channel = str(source["channel"]).strip().lstrip("@")
    return f"https://t.me/s/{channel}"


def should_follow_url(url):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if host in ("t.me", "telegram.me", "telegram.org"):
        return False
    if path.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov", ".zip", ".apk", ".exe")):
        return False
    return parsed.scheme in ("http", "https")


def collect_source(source):
    kind = source.get("type", "text")
    print(f"collect {kind}: {source.get('url') or source.get('channel')}")
    follow_timeout = int(source.get("follow_timeout", 10))

    if kind == "telegram":
        text = fetch_text(telegram_url(source), timeout=int(source.get("timeout", 30)))
        proxies = proxies_from_text(text)
        if source.get("follow_subscription_links", True):
            attempted = 0
            for url in extract_http_links(text):
                if attempted >= int(source.get("max_follow", 8)):
                    break
                if not should_follow_url(url):
                    continue
                attempted += 1
                try:
                    proxies.extend(proxies_from_text(fetch_text(url, timeout=follow_timeout)))
                except Exception as exc:
                    print(f"skip followed url {url}: {exc}")
        return proxies

    text = fetch_text(source["url"], timeout=int(source.get("timeout", 30)))
    if kind == "clash":
        return proxies_from_clash(text)
    proxies = proxies_from_text(text)
    if source.get("follow_subscription_links", False):
        attempted = 0
        for url in extract_http_links(text):
            if attempted >= int(source.get("max_follow", 6)):
                break
            if not should_follow_url(url):
                continue
            attempted += 1
            try:
                found = proxies_from_text(fetch_text(url, timeout=follow_timeout))
            except Exception as exc:
                print(f"skip followed url {url}: {exc}")
                continue
            if found:
                proxies.extend(found)
    return proxies


def normalize_proxy(proxy, index):
    proxy = dict(proxy)
    name = safe_name(proxy.get("name"), f"proxy-{index}")
    proxy["name"] = name
    if isinstance(proxy.get("port"), str) and proxy["port"].isdigit():
        proxy["port"] = int(proxy["port"])
    return proxy


def proxy_key(proxy):
    return json.dumps(
        {
            "type": proxy.get("type"),
            "server": proxy.get("server"),
            "port": proxy.get("port"),
            "uuid": proxy.get("uuid"),
            "password": proxy.get("password"),
            "cipher": proxy.get("cipher"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def load_config(path):
    env_config = os.environ.get("SOURCES_JSON", "").strip()
    if env_config:
        return json.loads(env_config)
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    candidate_url = os.environ.get("CANDIDATES_URL", "").strip()
    if candidate_url:
        return {"sources": [{"type": "clash", "url": candidate_url}]}
    raise SystemExit("No sources configured. Set SOURCES_JSON, CANDIDATES_URL, or create config/sources.json.")


def write_outputs(proxies, output_yaml, output_json):
    os.makedirs(os.path.dirname(output_yaml), exist_ok=True)
    config = {
        "mixed-port": 7890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "warning",
        "proxies": proxies,
        "proxy-groups": [{"name": "PROBE", "type": "select", "proxies": [p["name"] for p in proxies]}],
        "rules": ["MATCH,PROBE"],
    }
    with open(output_yaml, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(
            {
                "schema": 1,
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "source": "collect_sources",
                "total": len(proxies),
                "proxies": proxies,
            },
            f,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        f.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/sources.json")
    parser.add_argument("--output-yaml", default="data/candidates.yaml")
    parser.add_argument("--output-json", default="data/candidates.json")
    args = parser.parse_args()

    config = load_config(args.config)
    all_proxies = []
    for source in config.get("sources", []):
        try:
            all_proxies.extend(collect_source(source))
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
            print(f"source failed: {exc}")

    seen = set()
    cleaned = []
    for proxy in all_proxies:
        if not isinstance(proxy, dict) or not proxy.get("type") or not proxy.get("server") or not proxy.get("port"):
            continue
        proxy = normalize_proxy(proxy, len(cleaned) + 1)
        key = proxy_key(proxy)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(proxy)

    write_outputs(cleaned, args.output_yaml, args.output_json)
    print(f"collected {len(cleaned)} unique proxies")


if __name__ == "__main__":
    main()
