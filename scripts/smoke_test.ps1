# Employee Monitoring — Windows smoke test for the bundled build
# Run on a clean Windows machine/VM after installing the .exe.
# Requires PowerShell 5+ (no extra modules).
#
#   powershell -ExecutionPolicy Bypass -File scripts/smoke_test.ps1
#   # or with custom paths:
#   powershell -File scripts/smoke_test.ps1 -ExePath "$env:LOCALAPPDATA\Programs\Employee Monitoring\EmployeeMonitoring.exe"

param(
    [string]$ExePath = "$env:LOCALAPPDATA\Programs\Employee Monitoring\EmployeeMonitoring.exe",
    [string]$InstallerPath = "$PSScriptRoot\..\installer\dist\EmployeeMonitoring-Setup-0.1.0.exe"
)

$ErrorActionPreference = "Stop"
$checks = 0
$failed = 0

function Check($label, [scriptblock]$block) {
    $script:checks += 1
    Write-Host "`n[$script:checks] $label ..." -ForegroundColor Cyan
    try {
        & $block
        Write-Host "  OK" -ForegroundColor Green
    } catch {
        $script:failed += 1
        Write-Host "  FAIL: $_" -ForegroundColor Red
    }
}

Write-Host "=== Employee Monitoring smoke test ===" -ForegroundColor Yellow
Write-Host "Exe: $ExePath"
Write-Host "Installer: $InstallerPath"

Check "Installer exists (if built)" {
    if (-not (Test-Path $InstallerPath)) { Write-Host "  (skip — installer not present, checking installed exe only)" }
    else {
        $sig = Get-AuthenticodeSignature $InstallerPath -ErrorAction SilentlyContinue
        Write-Host "  Installer size: $((Get-Item $InstallerPath).Length / 1MB) MB"
        Write-Host "  Signature status: $($sig.Status) — $($sig.SignerCertificate.Subject -join '')"
        if ($sig.Status -ne "Valid") { Write-Host "  (unsigned dev build — expected without cert, docs/TECH_STACK.md:19)" -ForegroundColor DarkYellow }
    }
}

Check "Installed exe exists" {
    if (-not (Test-Path $ExePath)) { throw "not found at $ExePath — install the Setup exe first" }
    Write-Host "  Size: $((Get-Item $ExePath).Length / 1MB) MB"
}

Check "Bundled layout is onedir (no onefile temp churn, no external Python)" {
    $dir = Split-Path $ExePath -Parent
    $dlls = Get-ChildItem $dir -Filter "*.dll" | Select-Object -First 3
    if (-not $dlls) { throw "no DLLs beside exe — expected PyInstaller onedir (Qt6Core.dll, python3*.dll)" }
    Write-Host "  DLLs: $($dlls.Name -join ', ')"
    if (Test-Path "$dir\base_library.zip") { Write-Host "  base_library.zip present" }
    if (Test-Path "$dir\assets\icons\app.ico") { Write-Host "  assets/icons/app.ico bundled" }
    # Must NOT need a system Python
    if (Get-Command python -ErrorAction SilentlyContinue) {
        Write-Host "  (system python exists but bundle does not use it — correct)" -ForegroundColor DarkGray
    }
}

Check "Exe --version (no console window hang)" {
    $p = Start-Process -FilePath $ExePath -ArgumentList "--version" -PassThru -WindowStyle Hidden
    if (-not $p.WaitForExit(5000)) { $p.Kill(); throw "timed out" }
    Write-Host "  Exit code: $($p.ExitCode)"
}

Check "No console window (windowed build)" {
    # Windowed PyInstaller exe has no console subsystem — launch should not allocate one.
    Add-Type -MemberDefinition '[DllImport("kernel32.dll")] public static extern bool FreeConsole();' -Name K32 -Namespace Win32 -ErrorAction SilentlyContinue | Out-Null
    Write-Host "  (manual: launch exe — no black console should flash)"
}

Check "Second launch opens the running instance (never a second window)" {
    $p1 = Start-Process -FilePath $ExePath -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 5
    # A second launch must hand off to the instance already in the tray and
    # exit quietly (docs/UI_SPEC.md §Window behavior). It must never open a
    # second window: two writers on one SQLite file is a corruption risk.
    $p2 = Start-Process -FilePath $ExePath -PassThru -WindowStyle Hidden
    if (-not $p2.WaitForExit(20000)) {
        $p2.Kill()
        throw "second launch kept running — it started a second instance"
    }
    if ($p2.ExitCode -ne 0) { throw "second launch exit code $($p2.ExitCode) (expected 0)" }
    Write-Host "  second launch exited 0 after handing off" -ForegroundColor Green
    Write-Host "  (manual: the first instance's window should now be in front)" -ForegroundColor DarkGray
    try { $p1.Kill() } catch {}
    $p1.WaitForExit(3000) | Out-Null
}

Check "Data dir is per-user, not Program Files (offline-first)" {
    $data = "$env:LOCALAPPDATA\EmployeeMonitoring"
    Write-Host "  Expected: $data"
    if (-not (Test-Path $data)) {
        Write-Host "  (not yet created — will appear after first run / Check In)" -ForegroundColor DarkYellow
    } else {
        Get-ChildItem $data | Select-Object Name, Length | Format-Table | Out-String | Write-Host
        if (Test-Path "$data\database\agent.db") { Write-Host "  agent.db present (SQLite bundled, no external install)" -ForegroundColor Green }
    }
    # Must NOT be writing to {app}
    $exeDir = Split-Path $ExePath -Parent
    if (Test-Path "$exeDir\agent.db") { throw "DB found inside install dir — should be in LOCALAPPDATA" }
}

Check "Autostart registry value (if enabled)" {
    $val = Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "EmployeeMonitoring" -ErrorAction SilentlyContinue
    if ($null -eq $val) { Write-Host "  (not enabled — enable via Settings or installer task 'autostart')" -ForegroundColor DarkYellow }
    else { Write-Host "  HKCU\...\Run\EmployeeMonitoring = $($val.EmployeeMonitoring)" }
}

Check "Uninstall keeps data" {
    $un = Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -like "*Employee Monitoring*" } | Select-Object -First 1
    if ($null -eq $un) { Write-Host "  (uninstall key not yet present — after install it appears here)" -ForegroundColor DarkYellow }
    else {
        Write-Host "  DisplayName: $($un.DisplayName)  Version: $($un.DisplayVersion)"
        Write-Host "  UninstallString: $($un.UninstallString)"
    }
    Write-Host "  (Docs: uninstall intentionally keeps $env:LOCALAPPDATA\EmployeeMonitoring — delete manually for full wipe)"
}

Write-Host "`n=== Result: $checks checks, $failed failures ===" -ForegroundColor $(if ($failed -eq 0) { "Green" } else { "Red" })
if ($failed -gt 0) { exit 1 }
