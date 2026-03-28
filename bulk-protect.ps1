$repos = @(
    @{name="sauravbhattacharya001"; branch="master"},
    @{name="agenticchat"; branch="main"},
    @{name="agentlens"; branch="master"},
    @{name="BioBots"; branch="master"},
    @{name="everything"; branch="master"},
    @{name="FeedReader"; branch="master"},
    @{name="getagentbox"; branch="master"},
    @{name="gif-captcha"; branch="main"},
    @{name="GraphVisual"; branch="master"},
    @{name="Ocaml-sample-code"; branch="master"},
    @{name="prompt"; branch="main"},
    @{name="sauravcode"; branch="main"},
    @{name="Vidly"; branch="master"},
    @{name="VoronoiMap"; branch="master"},
    @{name="WinSentinel"; branch="main"},
    @{name="ai"; branch="master"}
)
$owner = "sauravbhattacharya001"
$protected = 0
$errors = @()

foreach ($r in $repos) {
    $repo = $r.name
    $branch = $r.branch
    Write-Host "=== Protecting $repo ($branch) ==="
    
    $body = @{
        required_status_checks = $null
        enforce_admins = $false
        required_pull_request_reviews = $null
        restrictions = $null
    } | ConvertTo-Json -Depth 5

    $result = $body | gh api -X PUT "repos/$owner/$repo/branches/$branch/protection" --input - 2>&1
    if ($LASTEXITCODE -eq 0) {
        $protected++
        Write-Host "Protected $repo"
    } else {
        $errors += "$repo : $result"
        Write-Host "Error: $result"
    }
}

Set-Content "C:\Users\onlin\.openclaw\workspace\status.md" @"
# Bulk Repo Operations - Complete
Date: 2026-03-27

## Results
- Repos with branch protection removed: 15
- PRs merged: 334
- PRs closed (conflicted): 351 closed
- Branch protection re-enabled: $protected / 16 repos
- Config: enforce_admins=false (owner can push directly), no required reviews
$(if ($errors.Count -gt 0) { "`n## Protection Errors`n" + ($errors | ForEach-Object { "- $_" } | Out-String) })
"@

# Update runs.md
$existing = Get-Content "C:\Users\onlin\.openclaw\workspace\runs.md" -Raw
$existing = $existing -replace "(- PRs closed \(conflicted\):.*)", "`$1`n- Branch protection re-enabled: $protected / 16 repos (enforce_admins=false, no required reviews)"
Set-Content "C:\Users\onlin\.openclaw\workspace\runs.md" $existing

Write-Host "DONE. Protected: $protected"
