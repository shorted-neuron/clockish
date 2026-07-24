#!/usr/bin/env python3
"""render_time_samples.py  --  Render ONE config across many synthetic
clock/date moments, to spot layout regressions across the full range of
digit widths, 12h/24h hour formats, and weekday/month name lengths.

Usage:
    clockish-time-samples <config.yaml> [--outdir docs/previews/time-samples]

No default config -- you must pass exactly one. Run it once against a 12h
config (e.g. nixie.yaml, time_format: "%-I:%M") and once against a 24h
config (e.g. nixie24.yaml, time_format: 24hs) to see both side by side.

Reuses clockish.render_preview's render_config() (and all of its hardware
stubbing) unchanged -- this script only overrides render_preview's module-
level _PREVIEW_NOW before each frame and always renders in 'mock' mode
(fixed cpu%/uptime; deterministic clock/date; see render_preview.py's
module docstring for what 'mock' mode fixes).

Output: {outdir}/{config-name}/{HH}-{MM}.png -- one file per sample time.
"""

import argparse
import datetime
import os
import sys

# Importing render_preview installs every hardware stub (RPi.GPIO, spidev,
# pyili9486, ...) as a side effect, then imports clockish.display -- exactly
# what render_config() below needs, and nothing here duplicates that setup.
import clockish.render_preview as _rp

_REPO = _rp._REPO

# ---------------------------------------------------------------------------
# Sample times (24h hour, minute) -- curated to hit:
#   - narrow 12h hour (1-9, no leading zero via %-I)           e.g. 1:17
#   - wide 24h hour AND wide 12h hour (10/11/12)               e.g. 20:00, 23:59
#   - a spread of ordinary middle-of-the-day times
#   - every digit 0-9 at least once across HH (24h), H12 (no-pad 12h), MM
#     (see _assert_digit_coverage() below -- verified at every run, not just
#     when this list is written)
# ---------------------------------------------------------------------------
_SAMPLE_TIMES: list[tuple[int, int]] = [
    (0, 0),    # midnight  -- 12h "12:00 AM" / 24h "00:00:00"
    (1, 17),   # narrow    -- 12h "1:17"
    (2, 38),
    (3, 46),
    (5, 9),
    (6, 6),
    (9, 52),
    (12, 0),   # noon      -- 12h stays "12:00"
    (13, 45),
    (15, 30),
    (20, 0),   # wide      -- 24h "20:00:00" / 12h "8:00 PM"
    (23, 59),  # wide both
]

# ---------------------------------------------------------------------------
# Sample dates -- rotated round-robin across _SAMPLE_TIMES (one date per
# time, cycling if there are more times than dates) so date-format widths
# (short/long weekday & month names) get exercised too, not just a single
# fixed worst-case date every frame.
# ---------------------------------------------------------------------------
_SAMPLE_DATES: list[tuple[int, int, int]] = [
    (2026, 1, 4),    # Sunday, January 4
    (2026, 3, 21),   # Saturday, March 21
    (2026, 5, 12),   # Tuesday, May 12
    (2026, 6, 30),   # Tuesday, June 30
    (2026, 7, 3),    # Friday, July 3
    (2026, 9, 9),    # Wednesday, September 9
    (2026, 11, 25),  # Wednesday, November 25
    (2028, 12, 20),  # Wednesday, December 20 -- longest weekday + longest month
]


def _assert_digit_coverage() -> None:
    """Every digit 0-9 must appear in at least one sample's 24h hour, no-pad
    12h hour, or zero-padded minute. Guards against _SAMPLE_TIMES being
    edited down to a set that silently loses coverage."""
    seen: set[str] = set()
    for h, m in _SAMPLE_TIMES:
        h12 = h % 12 or 12
        seen.update(f"{h:02d}")
        seen.update(str(h12))
        seen.update(f"{m:02d}")
    missing = set("0123456789") - seen
    if missing:
        raise AssertionError(f"_SAMPLE_TIMES no longer covers digits: {sorted(missing)}")


def render_time_samples(config_path: str, outdir: str) -> None:
    """Render config_path once per _SAMPLE_TIMES entry (mock mode, deterministic)
    into {outdir}/{config-name}/{HH}-{MM}.png."""
    _assert_digit_coverage()
    name = os.path.splitext(os.path.basename(config_path))[0]
    out_dir = os.path.join(outdir, name)
    os.makedirs(out_dir, exist_ok=True)

    print(f"Rendering {len(_SAMPLE_TIMES)} time sample(s) for [{name}] -> {out_dir}/")
    for i, (h, m) in enumerate(_SAMPLE_TIMES):
        year, month, day = _SAMPLE_DATES[i % len(_SAMPLE_DATES)]
        # render_config() reads this module-level global directly (same
        # mechanism render_preview.py's own CLI mock mode uses) -- no need
        # to touch clockish.display at all.
        _rp._PREVIEW_NOW = datetime.datetime(year, month, day, h, m, 0)
        label = f"{h:02d}-{m:02d}"
        out_path = os.path.join(out_dir, f"{label}.png")
        try:
            _rp.render_config(config_path, out_path, mock=True)
        except Exception as exc:
            print(f"  ERROR rendering {config_path} @ {label}: {exc}", file=sys.stderr)
            import traceback
            traceback.print_exc()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render ONE clockish config across a curated set of "
                    "clock/date moments (narrow/wide hours, 12h/24h, digit "
                    "coverage, weekday/month name-length spread)."
    )
    parser.add_argument(
        "config", help="Path to a single YAML config file (required -- no default).",
    )
    parser.add_argument(
        "--outdir", default=os.path.join(_REPO, "docs", "previews", "time-samples"),
        help="Base output directory (default: docs/previews/time-samples/). "
             "This config gets its own {outdir}/{config-name}/ subdirectory.",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.config):
        print(f"Config file not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    render_time_samples(args.config, args.outdir)


if __name__ == "__main__":
    main()
