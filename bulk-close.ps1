$repos = @("sauravbhattacharya001","agenticchat","agentlens","BioBots","everything","FeedReader","getagentbox","gif-captcha","GraphVisual","Ocaml-sample-code","prompt","sauravcode","Vidly","VoronoiMap","WinSentinel","ai")
$owner = "sauravbhattacharya001"
$totalClosed = 0
$closeErrors = @()

foreach ($repo in $repos) {
    Write-Host "=== Closing remaining PRs in $repo ==="
    $prs = gh pr list -R "$owner/$repo" --state open --limit 200 --json number -q ".[].number" 2>&1
    if ([string]::IsNullOrWhiteSpace($prs)) {
        Write-Host "No open PRs"
        continue
    }
    $prNumbers = $prs -split "`n" | Where-Object { $_ -match '^\d+$' }
    Write-Host "Found $($prNumbers.Count) remaining open PRs"
    foreach ($pr in $prNumbers) {
        $result = gh pr close $pr -R "$owner/$repo" -c "Closing: superseded or conflicting with newer changes already on main/master." 2>&1
        if ($LASTEXITCODE -eq 0) {
            $totalClosed++
        } else {
            $closeErrors += "$owner/$repo#$pr : $result"
            Write-Host "Error closing #$pr : $result"
        }
    }
    Write-Host "Repo $repo done. Total closed so far: $totalClosed"
    Set-Content "C:\Users\onlin\.openclaw\workspace\status.md" "# Status`nClosing conflicted PRs...`nRepo: $repo done`nClosed so far: $totalClosed"
}

# Update status.md
$statusContent = @"
# Bulk Repo Operations - Complete
Date: 2026-03-27

## Results
- Repos with branch protection removed: 15
- PRs merged: 334
- PRs skipped (conflicts): 351
- PRs closed (conflicted): $totalClosed
$(if ($closeErrors.Count -gt 0) { "`n## Close Errors`n" + ($closeErrors | ForEach-Object { "- $_" } | Out-String) })
"@
Set-Content "C:\Users\onlin\.openclaw\workspace\status.md" $statusContent

# Prepend close info to runs.md
$existing = Get-Content "C:\Users\onlin\.openclaw\workspace\runs.md" -Raw
$closeEntry = "- PRs closed (conflicted): $totalClosed`n"
$existing = $existing -replace "(- PRs skipped: 351)", "`$1`n$closeEntry"
Set-Content "C:\Users\onlin\.openclaw\workspace\runs.md" $existing

Write-Host "DONE. Closed: $totalClosed"
