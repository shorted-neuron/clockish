# vendor/

This directory contains **unmodified (or minimally modified) copies of
upstream open-source projects** that are incorporated into clockish.

## Why vendor instead of just `pip install`?

1. **The upstream project may not be on PyPI** (e.g. it's a loose collection
   of files from a GitHub repo).
2. **We need a specific commit**, not the latest release.
3. **We apply small local patches** and want diffs against upstream to be clear.

## Rules

| Rule | Reason |
|------|--------|
| Keep each project in its **own subdirectory** named after the project | Isolation |
| Include the upstream `LICENSE` file inside that subdirectory | Legal requirement |
| Record the upstream URL and commit/tag in [`../NOTICE`](../NOTICE) | Attribution |
| Prefer **no local edits** to vendored files | Easy to diff / update upstream |
| If you _must_ edit a vendored file, add a `# CLOCKISH PATCH:` comment | Visibility |

## Updating an upstream project

```bash
# Example: replace third_party/some_lib with a newer upstream commit
cd third_party/
rm -rf some_lib/
git clone --depth 1 --branch v2.3.0 https://github.com/user/some_lib some_lib/
rm -rf some_lib/.git          # don't nest git repos — use submodules if you want history
# Update the commit reference in ../NOTICE
```

## Subdirectories

| Directory | Upstream URL | Fork URL | Branch | Upstream commit | License |
|-----------|-------------|----------|--------|-----------------|---------|
| `dseg/` | https://github.com/keshikan/DSEG | — | — | v0.46 | SIL OFL 1.1 |
| `nixie/` | https://github.com/google/fonts/tree/main/ofl/nixieone | — | — | main | SIL OFL 1.1 |

### `nixie/` — Nixie One display font

**Not committed to this repo** — run the helper script to download:

```bash
bash scripts/download-nixie-font.sh
```

(No apt package exists for Nixie One; the script fetches it directly from the
Google Fonts GitHub repository.)

**Font file:**

| File | Notes |
|------|-------|
| `NixieOne-Regular.ttf` | Only weight/style available |

### `dseg/` — DSEG 7-segment / 14-segment fonts

**Not committed to this repo** — the font files are binary assets.
Run the helper script to download them on first use:

```bash
bash scripts/download-dseg-font.sh
```

Or install system-wide via apt (Debian / Raspberry Pi OS / Ubuntu):

```bash
sudo apt install fonts-dseg
```

After either step the `seven-segment.yaml` config (and any config that
references `DSEG7Classic-Regular.ttf` or similar in its `fonts:` section)
will work automatically.

**Key variants:**

| File | Style |
|------|-------|
| `DSEG7Classic-Regular.ttf` | Authentic: dim "ghost" segments visible (most realistic) |
| `DSEG7Classic-Bold.ttf` | Bold classic |
| `DSEG7Modern-Regular.ttf` | Clean: no ghost segments |
| `DSEG7Modern-Bold.ttf` | Bold modern |
| `DSEG14Classic-Regular.ttf` | 14-segment (alphanumeric) |
