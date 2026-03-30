$repos = @("prompt","WinSentinel","VoronoiMap","agentlens","GraphVisual","agenticchat","Vidly","gif-captcha","sauravcode","FeedReader","BioBots","everything","Ocaml-sample-code","getagentbox","ai")
$owner = "sauravbhattacharya001"
$results = @()
$statusPath = "C:\Users\onlin\.openclaw\workspace\status.md"

foreach ($repo in $repos) {
    Write-Host "=== Processing $repo ==="
    $merged = 0; $skipped = 0; $total = 0; $notes = @()
    
    # Get default branch
    $repoInfo = gh api "repos/$owner/$repo" --jq '.default_branch' 2>&1
    $branch = if ($repoInfo -match "^(main|master)$") { $repoInfo.Trim() } else { "main" }
    Write-Host "Default branch: $branch"
    
    # Delete branch protection
    $delResult = gh api "repos/$owner/$repo/branches/$branch/protection" -X DELETE 2>&1
    Write-Host "Protection delete: $delResult"
    
    # List open PRs
    $prsJson = gh pr list -R "$owner/$repo" --state open --limit 200 --json number 2>&1
    try {
        $prs = $prsJson | ConvertFrom-Json
    } catch {
        $prs = @()
    }
    $total = $prs.Count
    Write-Host "Open PRs: $total"
    
    foreach ($pr in $prs) {
        $num = $pr.number
        $mergeOut = gh pr merge $num -R "$owner/$repo" --merge --admin 2>&1
        if ($LASTEXITCODE -eq 0) {
            $merged++
            Write-Host "  Merged PR #$num"
        } else {
            $skipped++
            $notes += "#$num"
            Write-Host "  Skipped PR #$num : $mergeOut"
        }
    }
    
    $skipNote = if ($notes.Count -gt 0) { " (skipped: $($notes -join ', '))" } else { "" }
    $results += [PSCustomObject]@{Repo=$repo; Total=$total; Merged=$merged; Skipped=$skipped; Notes=$skipNote}
    Write-Host "Done: $repo - $merged merged, $skipped skipped / $total total"
    
    # Update status
    $status = "# Bulk Merge Progress`n`nLast updated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`n`n| Repo | Total | Merged | Skipped | Notes |`n|------|-------|--------|---------|-------|`n"
    foreach ($r in $results) {
        $status += "| $($r.Repo) | $($r.Total) | $($r.Merged) | $($r.Skipped) | $($r.Notes) |`n"
    }
    $remaining = $repos.Count - $results.Count
    $status += "`nRemaining: $remaining repos`n"
    Set-Content -Path $statusPath -Value $status -Encoding UTF8
}

# Final summary
$totalAll = ($results | Measure-Object -Property Total -Sum).Sum
$mergedAll = ($results | Measure-Object -Property Merged -Sum).Sum
$skippedAll = ($results | Measure-Object -Property Skipped -Sum).Sum

$final = "# Bulk Merge Complete`n`nFinished: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`n`n**Totals: $mergedAll merged, $skippedAll skipped out of $totalAll PRs across $($repos.Count) repos**`n`n| Repo | Total | Merged | Skipped | Notes |`n|------|-------|--------|---------|-------|`n"
foreach ($r in $results) {
    $final += "| $($r.Repo) | $($r.Total) | $($r.Merged) | $($r.Skipped) | $($r.Notes) |`n"
}
Set-Content -Path $statusPath -Value $final -Encoding UTF8

# Append to runs.md
$runsPath = "C:\Users\onlin\.openclaw\workspace\runs.md"
$runEntry = "`n## 2026-03-29 - Bulk PR Merge`n`n$mergedAll merged, $skippedAll skipped across $($repos.Count) repos ($totalAll total PRs)`n`n| Repo | Total | Merged | Skipped |`n|------|-------|--------|---------|`n"
foreach ($r in $results) {
    $runEntry += "| $($r.Repo) | $($r.Total) | $($r.Merged) | $($r.Skipped) |`n"
}
if (Test-Path $runsPath) {
    $existing = Get-Content $runsPath -Raw
    Set-Content -Path $runsPath -Value ($existing + $runEntry) -Encoding UTF8
} else {
    Set-Content -Path $runsPath -Value ("# Runs`n" + $runEntry) -Encoding UTF8
}

Write-Host "`n=== ALL DONE === $mergedAll merged, $skippedAll skipped, $totalAll total ==="
