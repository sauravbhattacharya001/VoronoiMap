$repos = @("sauravbhattacharya001","agenticchat","agentlens","BioBots","everything","FeedReader","getagentbox","gif-captcha","GraphVisual","Ocaml-sample-code","prompt","sauravcode","Vidly","VoronoiMap","WinSentinel","ai")
$owner = "sauravbhattacharya001"
$protectionRemoved = 0
$totalMerged = 0
$totalSkipped = 0
$skippedDetails = @()
$errors = @()

foreach ($repo in $repos) {
    Write-Host "=== Processing $repo ==="
    
    # Get default branch
    $branch = gh repo view "$owner/$repo" --json defaultBranchRef -q ".defaultBranchRef.name" 2>&1
    Write-Host "Default branch: $branch"
    
    # Remove branch protection
    $delResult = gh api -X DELETE "repos/$owner/$repo/branches/$branch/protection" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Branch protection removed for $repo"
        $protectionRemoved++
    } else {
        Write-Host "No protection or error: $delResult"
    }
    
    # Get open PRs
    $prs = gh pr list -R "$owner/$repo" --state open --limit 200 --json number -q ".[].number" 2>&1
    if ([string]::IsNullOrWhiteSpace($prs)) {
        Write-Host "No open PRs"
        # Update status
        Set-Content "C:\Users\onlin\.openclaw\workspace\status.md" "# Status`nProcessing: $repo done`nProtection removed: $protectionRemoved`nMerged so far: $totalMerged`nSkipped so far: $totalSkipped"
        continue
    }
    
    $prNumbers = $prs -split "`n" | Where-Object { $_ -match '^\d+$' }
    Write-Host "Found $($prNumbers.Count) open PRs"
    
    foreach ($pr in $prNumbers) {
        $mergeResult = gh pr merge $pr -R "$owner/$repo" --merge 2>&1
        if ($LASTEXITCODE -eq 0) {
            $totalMerged++
        } else {
            $totalSkipped++
            $reason = "$mergeResult" -replace "`n"," "
            $skippedDetails += "$owner/$repo#$pr : $reason"
            Write-Host "Skipped PR #$pr : $reason"
        }
    }
    
    Write-Host "Repo $repo done. Merged total: $totalMerged, Skipped total: $totalSkipped"
    Set-Content "C:\Users\onlin\.openclaw\workspace\status.md" "# Status`nProcessing: $repo done`nProtection removed: $protectionRemoved`nMerged so far: $totalMerged`nSkipped so far: $totalSkipped"
}

# Write final status
$summary = @"
# Bulk Repo Operations - Complete
Date: 2026-03-27

## Results
- Repos with branch protection removed: $protectionRemoved
- PRs merged: $totalMerged
- PRs skipped: $totalSkipped

## Skipped PRs
$($skippedDetails | ForEach-Object { "- $_" } | Out-String)

## Errors
$($errors | ForEach-Object { "- $_" } | Out-String)
"@

Set-Content "C:\Users\onlin\.openclaw\workspace\status.md" $summary

# Append to runs.md
$runsEntry = @"

## 2026-03-27 - Bulk Branch Protection + PR Merge
- Branch protection removed: $protectionRemoved repos
- PRs merged: $totalMerged
- PRs skipped: $totalSkipped
$(if ($skippedDetails.Count -gt 0) { "- Skip reasons:`n" + ($skippedDetails | ForEach-Object { "  - $_" } | Out-String) })
"@

if (Test-Path "C:\Users\onlin\.openclaw\workspace\runs.md") {
    $existing = Get-Content "C:\Users\onlin\.openclaw\workspace\runs.md" -Raw
    Set-Content "C:\Users\onlin\.openclaw\workspace\runs.md" ($runsEntry + "`n" + $existing)
} else {
    Set-Content "C:\Users\onlin\.openclaw\workspace\runs.md" ("# Runs`n" + $runsEntry)
}

Write-Host "DONE. Merged: $totalMerged, Skipped: $totalSkipped, Protection removed: $protectionRemoved"
