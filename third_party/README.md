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
| _(none — pyili9486 is a pip dependency, not vendored)_ | | | | | |

