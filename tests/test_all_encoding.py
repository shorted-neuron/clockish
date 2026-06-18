"""
Scan the clockish project for non-ASCII characters (mojibake, BOM, curly quotes, etc.)
and fix them to plain ASCII.  Reports what it finds before fixing.

Policy:
  - Markdown (.md) files: UTF-8 is allowed.  Only mojibake and BOM are fixed;
    intentional Unicode (box-drawing, arrows, emoji, etc.) is left alone.
  - All other text files: must be plain ASCII after fixing.
"""
import os
import re

PROJECT_ROOT = r'C:\git-bts\clockish'

# File extensions to scan (text files only)
TEXT_EXTS = {'.py', '.yaml', '.yml', '.sh', '.md', '.txt', '.cfg', '.toml', '.ini', '.rst'}

# Markdown extensions -- UTF-8 is fine here; only fix mojibake / BOM, don't enforce ASCII
MD_EXTS = {'.md'}

# Files/dirs to skip
SKIP_DIRS = {'.git', '.venv', '__pycache__', 'third_party', 'node_modules', '.mypy_cache'}

# ASCII replacements for common non-ASCII characters
REPLACEMENTS = [
    ('\u2014', ' -- '),   # em-dash
    ('\u2013', ' - '),    # en-dash
    ('\u2192', '->'),     # right arrow ->
    ('\u2190', '<-'),     # left arrow <-
    ('\u2248', '~'),      # approximately equal ~
    ('\u2265', '>='),     # greater or equal >=
    ('\u2264', '<='),     # less or equal <=
    ('\u2260', '!='),     # not equal !=
    ('\u00d7', 'x'),      # multiplication sign x
    ('\u00f7', '/'),      # division sign /
    ('\u2026', '...'),    # ellipsis ...
    ('\u2018', "'"),      # left single quote '
    ('\u2019', "'"),      # right single quote '
    ('\u201c', '"'),      # left double quote "
    ('\u201d', '"'),      # right double quote "
    ('\u00b0', ' deg'),   # degree sign
    ('\u2022', '-'),      # bullet point -
    ('\u2212', '-'),      # minus sign -
    ('\u00a0', ' '),      # non-breaking space -> regular space
    ('\u00ab', '<<'),     # left angle quote <<
    ('\u00bb', '>>'),     # right angle quote >>
    ('\u2039', '<'),      # single left angle <
    ('\u203a', '>'),      # single right angle >
    ('\u2044', '/'),      # fraction slash /
    ('\u00b1', '+/-'),    # plus-minus +/-
    ('\ufeff', ''),       # BOM -- remove entirely
]


def reverse_mojibake(src):
    """Reverse Windows-1252 mojibake: re-encode cp1252 chars as bytes, decode as UTF-8."""
    fixed = []
    i = 0
    while i < len(src):
        c = src[i]
        if ord(c) > 127:
            restored = None
            for length in (3, 2, 1):
                chunk = src[i:i+length]
                try:
                    b = chunk.encode('cp1252')
                    restored = b.decode('utf-8')
                    break
                except (UnicodeEncodeError, UnicodeDecodeError):
                    continue
            if restored is not None:
                fixed.append(restored)
                i += length
            else:
                fixed.append(c)  # keep as-is; will be caught by remaining check
                i += 1
        else:
            fixed.append(c)
            i += 1
    return ''.join(fixed)


def fix_text(src, allow_unicode=False):
    """Strip BOM, reverse mojibake, and (unless allow_unicode) apply ASCII replacements."""
    src = src.lstrip('\ufeff')
    src = reverse_mojibake(src)
    if not allow_unicode:
        for orig, repl in REPLACEMENTS:
            src = src.replace(orig, repl)
    return src


def scan_and_fix(dry_run=False):
    issues = []

    for dirpath, dirnames, filenames in os.walk(PROJECT_ROOT):
        # Prune skip dirs in-place
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in TEXT_EXTS:
                continue

            is_markdown = ext in MD_EXTS
            fpath = os.path.join(dirpath, fname)
            relpath = os.path.relpath(fpath, PROJECT_ROOT)

            try:
                with open(fpath, encoding='utf-8') as f:
                    src = f.read()
            except UnicodeDecodeError:
                print(f"  SKIP (not UTF-8): {relpath}")
                continue

            fixed = fix_text(src, allow_unicode=is_markdown)

            # For Markdown, only flag BOM or mojibake that was actually corrected.
            # Remaining non-ASCII in .md files is intentional -- don't warn about it.
            if is_markdown:
                remaining = []
            else:
                remaining = [(m.start(), repr(m.group())) for m in re.finditer(r'[^\x00-\x7F]', fixed)]

            if src != fixed or remaining:
                changed = src != fixed
                file_issues = []
                if '\ufeff' in src:
                    file_issues.append('BOM')
                mojibake = re.findall(r'[^\x00-\x7F]', src)
                if mojibake:
                    file_issues.append(f'{len(mojibake)} non-ASCII chars')
                if remaining:
                    file_issues.append(f'{len(remaining)} unfixed non-ASCII remain after fix')
                issues.append((relpath, file_issues, remaining))

                print(f"  {'[DRY] ' if dry_run else ''}{'FIX' if changed else 'WARN'}: {relpath}")
                for iss in file_issues:
                    print(f"    - {iss}")
                if remaining:
                    for pos, ch in remaining[:5]:
                        line = fixed[:pos].count('\n') + 1
                        print(f"    ! unfixed line {line}: {ch}")

                if not dry_run and changed:
                    with open(fpath, 'w', encoding='utf-8') as f:
                        f.write(fixed)

    return issues


def verify_clean():
    """Return a list of (relpath, label, matches) for files that still have issues."""
    dirty = []
    for dirpath, dirnames, filenames in os.walk(PROJECT_ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in TEXT_EXTS:
                continue
            is_markdown = ext in MD_EXTS
            fpath = os.path.join(dirpath, fname)
            relpath = os.path.relpath(fpath, PROJECT_ROOT)
            try:
                with open(fpath, encoding='utf-8') as f:
                    src = f.read()
            except UnicodeDecodeError:
                continue
            if is_markdown:
                remaining = list(re.finditer(r'\ufeff', src))
                label = "BOM"
            else:
                remaining = list(re.finditer(r'[^\x00-\x7F]', src))
                label = "non-ASCII"
            if remaining:
                dirty.append((relpath, label, remaining, src))
    return dirty


# ---------------------------------------------------------------------------
# pytest entry point  --  run automatically with the rest of the test suite
# ---------------------------------------------------------------------------

def test_all_files_ascii_or_utf8_markdown():
    """All non-Markdown text files must be pure ASCII.
    Markdown files must be BOM-free (intentional Unicode is allowed).

    If this test fails, run:  python tests/test_all_encoding.py
    That script will auto-fix what it can (mojibake, known Unicode -> ASCII
    replacements) and report anything that needs manual attention.
    """
    # Auto-fix pass first (idempotent -- safe to run in CI too)
    scan_and_fix(dry_run=False)
    # Then verify
    dirty = verify_clean()
    if not dirty:
        return
    lines = []
    for relpath, label, matches, src in dirty:
        lines.append(f"\n  {relpath}  ({len(matches)} {label} char(s)):")
        for m in matches[:5]:
            line_no = src[:m.start()].count('\n') + 1
            lines.append(f"    line {line_no}: {repr(m.group())}  U+{ord(m.group()):04X}")
        if len(matches) > 5:
            lines.append(f"    ... and {len(matches) - 5} more")
    raise AssertionError(
        "Non-ASCII / BOM characters found in project files "
        "(auto-fix could not resolve all of them):\n" + "\n".join(lines)
    )


# ---------------------------------------------------------------------------
# Standalone script entry point  --  python tests/test_all_encoding.py
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print("Scanning project for non-ASCII / mojibake / BOM issues...")
    print("=" * 60)
    issues = scan_and_fix(dry_run=False)
    print("=" * 60)
    if not issues:
        print("No issues found. Project is clean.")
    else:
        print(f"Processed {len(issues)} file(s) with issues.")

    print("\nVerification pass...")
    dirty = verify_clean()
    if dirty:
        for relpath, label, remaining, src in dirty:
            print(f"  STILL HAS {label}: {relpath} ({len(remaining)} chars)")
            for m in remaining[:3]:
                line = src[:m.start()].count('\n') + 1
                print(f"    line {line}: {repr(m.group())} U+{ord(m.group()):04X}")
        import sys
        sys.exit(1)
    else:
        print("All scanned files are clean.")
