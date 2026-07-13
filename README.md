# China Proxy Probe

GitHub-centered proxy availability pipeline with a home-router probe.

The goal is to separate two jobs:

- GitHub Actions stores and filters candidate nodes.
- Your iStoreOS/OpenWrt router tests candidates from a real mainland home network.

This avoids treating GitHub Actions or an overseas VPS as proof that a node works from mainland China.

## Layout

```text
.
├── .github/workflows/
│   ├── collect.yml        # Pull candidate Clash YAML and export probe JSON
│   └── filter.yml         # Filter final subscription from probe results
├── config/
│   └── probe.example.json # Router probe config template
├── data/
│   ├── candidates.yaml    # Candidate Clash config
│   ├── candidates.json    # Simplified JSON consumed by router probe
│   ├── final.yaml         # Filtered Clash config for clients
│   └── alive/             # Probe result files
├── probe/
│   ├── probe.py           # Run mihomo locally and test candidates
│   ├── push_github.py     # Upload alive JSON to this GitHub repo
│   └── probe.sh           # OpenWrt cron entrypoint
└── scripts/
    ├── export_candidates.py
    └── filter_final.py
```

## GitHub Setup

Create a repository:

```text
hokiwork/china-proxy-probe
```

Add repository variable:

```text
CANDIDATES_URL=https://example.com/candidates.yaml
```

`CANDIDATES_URL` should point to a Clash YAML containing a top-level `proxies` array. It can come from aggregator, Gist, or any other generator.

For multiple public sources, add repository variable `SOURCES_JSON` instead:

```json
{
  "sources": [
    {
      "type": "clash",
      "url": "https://example.com/subscription.yaml"
    },
    {
      "type": "text",
      "url": "https://example.com/free-nodes.txt"
    },
    {
      "type": "telegram",
      "channel": "public_channel_name",
      "follow_subscription_links": true
    }
  ]
}
```

Supported source types:

- `clash`: Clash YAML with top-level `proxies`.
- `text`: plain text, base64 subscription text, or a page containing proxy links.
- `telegram`: public Telegram channel page, using `https://t.me/s/<channel>`. When `follow_subscription_links` is true, the collector also tries ordinary HTTP links in recent public messages and keeps only links that parse into proxies.

Do not commit private subscription URLs to a public repository. Put private or semi-private sources in GitHub repository variables or secrets.

Enable Actions. Run `Collect candidates` once. It will create:

```text
data/candidates.yaml
data/candidates.json
```

## Router Setup

Copy the `probe/` directory and `config/probe.example.json` to your router, for example:

```sh
mkdir -p /mnt/sata/china-proxy-probe
```

Recommended router path:

```text
/mnt/sata/china-proxy-probe
```

Do not put large logs on `/overlay`.

Create config:

```sh
cp config/probe.example.json config/probe.json
vi config/probe.json
```

Set:

- `github.owner`: `hokiwork`
- `github.repo`: `china-proxy-probe`
- `github.token`: fine-grained GitHub token with Contents read/write permission for this repo
- `probe.id`: a stable name such as `home-telecom`
- `mihomo.bin`: path to your router's mihomo/clash binary

Run once:

```sh
sh /mnt/sata/china-proxy-probe/probe/probe.sh
```

If it works, add cron:

```sh
crontab -e
```

```cron
*/30 * * * * /bin/sh /mnt/sata/china-proxy-probe/probe/probe.sh >/tmp/china-proxy-probe.log 2>&1
```

## Important Notes

- The probe starts a separate local mihomo process. It does not modify your OpenClash/PassWall/Nikki runtime.
- Probe traffic must go directly through WAN. Do not route the probe process through an existing proxy chain.
- The mihomo controller started by the probe binds to `127.0.0.1` only.
- Keep the GitHub token on the router only. Never commit it.

## Pipeline

```text
Collect candidates workflow
  -> data/candidates.yaml
  -> data/candidates.json

Router probe
  -> data/alive/<probe-id>.json

Filter workflow
  -> data/final.yaml
```
