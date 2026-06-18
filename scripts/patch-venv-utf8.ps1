# scripts/patch-venv-utf8.ps1  --  Inject PYTHONUTF8=1 into a venv's activation scripts.
#
# Usage:
#   .\scripts\patch-venv-utf8.ps1              # patches .venv in current directory
#   .\scripts\patch-venv-utf8.ps1 -VenvDir C:\path\to\venv
#
# Why this exists
# ---------------
# On Windows, Python's open() defaults to cp1252 (Windows-1252) rather than
# UTF-8.  PYTHONUTF8=1 (PEP 540, Python >= 3.7) forces UTF-8 everywhere.
# Python 3.15 will make this the default; this script bridges the gap.
#
# Run once after creating a venv:
#   python -m venv .venv
#   .\scripts\patch-venv-utf8.ps1
#
# Idempotent -- running twice is safe.
# Re-run if the venv is recreated.

param(
    [string]$VenvDir = '.venv'
)

$MARKER = '# PYTHONUTF8=1 -- injected by scripts/patch-venv-utf8.ps1'
$patched = 0

function Patch-Script {
    param([string]$Path, [string]$Append)
    if (-not (Test-Path $Path)) { return }
    $content = Get-Content $Path -Raw -Encoding UTF8
    if ($content -match 'PYTHONUTF8') {
        Write-Host "  already patched: $Path"
        return
    }
    # Append with a trailing newline; preserve existing line endings
    Add-Content -Path $Path -Value "`n$MARKER`n$Append" -Encoding UTF8
    Write-Host "  patched: $Path"
    $script:patched++
}

# ---------------------------------------------------------------------------
# activate  (bash / zsh -- present in Windows venvs too, used by WSL / Git Bash)
# ---------------------------------------------------------------------------
Patch-Script "$VenvDir\Scripts\activate"    "export PYTHONUTF8=1"

# ---------------------------------------------------------------------------
# activate.bat  (Windows CMD)
# ---------------------------------------------------------------------------
$batPath = "$VenvDir\Scripts\activate.bat"
if ((Test-Path $batPath) -and -not ((Get-Content $batPath -Raw) -match 'PYTHONUTF8')) {
    $original = Get-Content $batPath -Raw -Encoding UTF8
    $insert = "rem $MARKER`r`nset PYTHONUTF8=1`r`n"
    Set-Content $batPath -Value ($insert + $original) -Encoding UTF8 -NoNewline
    Write-Host "  patched: $batPath"
    $patched++
}

# ---------------------------------------------------------------------------
# activate.ps1  (PowerShell)
# ---------------------------------------------------------------------------
Patch-Script "$VenvDir\Scripts\activate.ps1" "`$env:PYTHONUTF8 = '1'"

# ---------------------------------------------------------------------------
# activate.fish  (fish shell)
# ---------------------------------------------------------------------------
Patch-Script "$VenvDir\Scripts\activate.fish" "set -gx PYTHONUTF8 1"

# ---------------------------------------------------------------------------
# activate.nu  (Nushell)
# ---------------------------------------------------------------------------
Patch-Script "$VenvDir\Scripts\activate.nu"  "`$env.PYTHONUTF8 = '1'"

Write-Host ""
if ($patched -gt 0) {
    Write-Host "Patched $patched activation script(s) in '$VenvDir' with PYTHONUTF8=1."
} else {
    Write-Host "All activation scripts in '$VenvDir' already patched (nothing to do)."
}

