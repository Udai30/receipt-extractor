# One-time setup for the v2 receipt extractor (written by Claude, 2026-10-22).
# Run:  powershell -ExecutionPolicy Bypass -File "D:\My Hub\projects\01-receipt-extractor\setup_v2.ps1"
# Safe to re-run: it never overwrites .claude, .env or .git if they already exist.
# Output is saved to setup_log.txt in this folder (no secret values are printed).

$ErrorActionPreference = 'Continue'
$old = 'D:\Project\01-receipt-extractor'
$new = 'D:\My Hub\projects\01-receipt-extractor'
Start-Transcript -Path (Join-Path $new 'setup_log.txt') -Force | Out-Null
Set-Location $new

function Step($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }

Step '1. Copy .claude and .env from the v1 folder'
foreach ($item in '.claude', '.env') {
    if (Test-Path (Join-Path $new $item)) { Write-Host "$item already here - skipped" }
    elseif (Test-Path (Join-Path $old $item)) { Copy-Item (Join-Path $old $item) $new -Recurse; Write-Host "$item copied" }
    else { Write-Host "$item not found in $old" -ForegroundColor Yellow }
}

Step '2. Bring over git history (.git)'
if (Test-Path (Join-Path $new '.git')) { Write-Host '.git already here - skipped' }
else { Copy-Item (Join-Path $old '.git') $new -Recurse -Force; Write-Host '.git copied' }
git branch --show-current
git remote -v
Write-Host '--- git status (short) ---'
git status --short

Step '3. uv sync'
uv sync
Write-Host "uv sync exit code: $LASTEXITCODE"

Step '4. API keys present? (names only, values never printed)'
$envText = Get-Content (Join-Path $new '.env') -ErrorAction SilentlyContinue
foreach ($k in 'GROQ_API_KEY', 'GEMINI_API_KEY', 'HF_TOKEN') {
    $line = $envText | Where-Object { $_ -match "^\s*$k\s*=" } | Select-Object -First 1
    $set = $line -and (($line -split '=', 2)[1].Trim().Trim('"').Trim("'")).Length -gt 0
    Write-Host ("{0}: {1}" -f $k, $(if ($set) { 'set' } else { 'MISSING' }))
}
Write-Host '--- llm ping groq-oss-20b ---'
uv run receipt_extractor llm ping --model groq-oss-20b
Write-Host "llm ping exit code: $LASTEXITCODE"

Step '5. Test suite (DB tests skip if Docker is not running)'
uv run pytest -q -ra
Write-Host "pytest exit code: $LASTEXITCODE"

Step 'Done'
Write-Host 'Next: open Claude Code in this folder, paste the Preamble + the M0.R prompt from IMPLEMENTATION_PROMPTS.md.'
Stop-Transcript | Out-Null
Read-Host 'Press Enter to close'
