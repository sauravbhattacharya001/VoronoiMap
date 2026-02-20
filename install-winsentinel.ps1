$ErrorActionPreference = "Continue"

# Create directories
New-Item -Path "C:\Program Files\WinSentinel\App" -ItemType Directory -Force | Out-Null
New-Item -Path "C:\Program Files\WinSentinel\Cli" -ItemType Directory -Force | Out-Null
New-Item -Path "C:\Program Files\WinSentinel\Agent" -ItemType Directory -Force | Out-Null

# Copy files
Copy-Item -Path "C:\Users\onlin\.openclaw\workspace\WinSentinel\publish\app\*" -Destination "C:\Program Files\WinSentinel\App\" -Recurse -Force
Copy-Item -Path "C:\Users\onlin\.openclaw\workspace\WinSentinel\publish\cli\*" -Destination "C:\Program Files\WinSentinel\Cli\" -Recurse -Force
Copy-Item -Path "C:\Users\onlin\.openclaw\workspace\WinSentinel\publish\agent\*" -Destination "C:\Program Files\WinSentinel\Agent\" -Recurse -Force

# Add CLI to PATH
$currentPath = [Environment]::GetEnvironmentVariable("Path", "Machine")
if ($currentPath -notlike "*WinSentinel\Cli*") {
    [Environment]::SetEnvironmentVariable("Path", "$currentPath;C:\Program Files\WinSentinel\Cli", "Machine")
}

# Stop existing service if present
sc.exe stop WinSentinel 2>$null
sc.exe delete WinSentinel 2>$null
Start-Sleep -Seconds 2

# Install Windows Service
sc.exe create WinSentinel binPath="C:\Program Files\WinSentinel\Agent\WinSentinel.Agent.exe" start=auto DisplayName="WinSentinel Security Agent"
sc.exe description WinSentinel "WinSentinel Always-On Security Agent - Real-time Windows security monitoring and threat detection"
sc.exe start WinSentinel

# Create desktop shortcut
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("C:\Users\onlin\Desktop\WinSentinel.lnk")
$Shortcut.TargetPath = "C:\Program Files\WinSentinel\App\WinSentinel.App.exe"
$Shortcut.WorkingDirectory = "C:\Program Files\WinSentinel\App"
$Shortcut.Description = "WinSentinel Security Agent"
$Shortcut.Save()

# Query service
$svcQuery = sc.exe query WinSentinel 2>&1
$result = @{
    FilesInstalled = (Test-Path "C:\Program Files\WinSentinel\App\WinSentinel.App.exe") -and (Test-Path "C:\Program Files\WinSentinel\Cli\winsentinel.exe") -and (Test-Path "C:\Program Files\WinSentinel\Agent\WinSentinel.Agent.exe")
    ServiceQuery = $svcQuery -join "`n"
    ShortcutCreated = Test-Path "C:\Users\onlin\Desktop\WinSentinel.lnk"
}
$result | ConvertTo-Json | Out-File "C:\Users\onlin\.openclaw\workspace\install-result.txt"
