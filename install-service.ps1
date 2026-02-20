$ErrorActionPreference = "Continue"
$installBase = "C:\Users\onlin\AppData\Local\WinSentinel"

# Also copy to Program Files for the service
New-Item -Path "C:\Program Files\WinSentinel\Agent" -ItemType Directory -Force | Out-Null
Copy-Item -Path "$installBase\Agent\*" -Destination "C:\Program Files\WinSentinel\Agent\" -Recurse -Force

# Stop existing service if present
sc.exe stop WinSentinel 2>$null
sc.exe delete WinSentinel 2>$null
Start-Sleep -Seconds 2

# Install Windows Service
sc.exe create WinSentinel binPath="$installBase\Agent\WinSentinel.Agent.exe" start=auto DisplayName="WinSentinel Security Agent"
sc.exe description WinSentinel "WinSentinel Always-On Security Agent - Real-time Windows security monitoring and threat detection"
sc.exe start WinSentinel

# Add CLI to machine PATH too
$currentPath = [Environment]::GetEnvironmentVariable("Path", "Machine")
if ($currentPath -notlike "*WinSentinel\Cli*") {
    [Environment]::SetEnvironmentVariable("Path", "$currentPath;$installBase\Cli", "Machine")
}

$svcResult = sc.exe query WinSentinel 2>&1
$svcResult | Out-File "C:\Users\onlin\.openclaw\workspace\service-result.txt"
