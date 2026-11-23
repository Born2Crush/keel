# How Keel's frames reach the screen: PresentMon records short fake-PS5 sessions at 90 fps (windowed,
# fullscreen, windowed with VSync; then the same with Window presentation: Low delay, the DXGI flip model) and
# writes one CSV per session, plus Keel's own CSVs.
#
# PresentMon (https://github.com/GameTechDev/PresentMon, the console app) needs admin rights, so run this from
# an admin PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\presentmon_check.ps1
# Options: -PresentMon <exe>  -Clip <video>  -Seconds <per session>  -Hz <60|90|120>  -Modes windowed,dxgi-windowed
#          -DryRun runs the sessions without PresentMon (no admin needed), to check everything else works.
# Results: out\presentmon\<mode>.csv (PresentMon) and out\presentmon\<mode>-ticks.csv (Keel's tick log).
# Summarise with: .venv\Scripts\python scripts\presentmon_summary.py out\presentmon
param(
    [string]$PresentMon = "D:\dev\tools\PresentMon\PresentMon-2.6.0-x64.exe",
    [string]$Clip = "testdata\skate_1080p60.mp4",
    [int]$Seconds = 25,
    [int]$Hz = 90,
    [string[]]$Modes = @("windowed", "fullscreen", "windowed-vsync", "dxgi-windowed", "dxgi-fullscreen",
                         "dxgi-windowed-vsync"),
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$py = Join-Path $repo ".venv\Scripts\python.exe"
$out = Join-Path $repo "out\presentmon"
New-Item -ItemType Directory -Force $out | Out-Null
if (-not $DryRun -and -not (Test-Path $PresentMon)) { throw "PresentMon not found: $PresentMon" }
# Settings for this test only, so your saved settings aren't used or changed.
$settings = Join-Path $out "settings.json"
Set-Content -Path $settings -Value "{}" -Encoding ascii

$all = @(
    @{ Name = "windowed"; Args = @("--present", "gl", "--no-fullscreen", "--no-vsync") },
    @{ Name = "fullscreen"; Args = @("--present", "gl", "--fullscreen", "--no-vsync") },
    @{ Name = "windowed-vsync"; Args = @("--present", "gl", "--no-fullscreen", "--vsync") },
    @{ Name = "dxgi-windowed"; Args = @("--present", "dxgi", "--no-fullscreen", "--no-vsync") },
    @{ Name = "dxgi-fullscreen"; Args = @("--present", "dxgi", "--fullscreen", "--no-vsync") },
    @{ Name = "dxgi-windowed-vsync"; Args = @("--present", "dxgi", "--no-fullscreen", "--vsync") }
)
$ErrorActionPreference = "Continue"  # Python's warnings on stderr mustn't stop the run
foreach ($m in ($all | Where-Object { $Modes -contains $_.Name })) {
    $name = $m.Name
    Write-Host "`n=== $name ($Seconds s at $Hz fps) ==="
    $pmCsv = Join-Path $out "$name.csv"
    Remove-Item -ErrorAction SilentlyContinue $pmCsv
    $pm = $null
    if (-not $DryRun) {
        $pm = Start-Process -FilePath $PresentMon -PassThru -WindowStyle Hidden -ArgumentList @(
            "--process_name", "python.exe", "--output_file", "`"$pmCsv`"", "--no_console_stats",
            "--stop_existing_session", "--session_name", "KeelCheck")
        Start-Sleep -Seconds 2
    }
    $tap = "Local\sp-presentmon"
    $fake = Start-Process -FilePath $py -PassThru -WindowStyle Hidden -ArgumentList @(
        "scripts\frametap_fake.py", $Clip, "--seconds", $Seconds, "--delay", "8", "--tap", $tap)
    & $py run.py --chiaki --chiaki-tap $tap --display --hz $Hz --settings $settings --no-minimize-chiaki `
        --chiaki-log (Join-Path $out "$name-ticks.csv") @($m.Args)
    if (-not $fake.HasExited) { Stop-Process -Id $fake.Id -Force }
    if ($DryRun) { Write-Host "Dry run: Keel's tick log is $(Join-Path $out "$name-ticks.csv")"; continue }
    & $PresentMon --terminate_existing_session --session_name KeelCheck | Out-Null
    Start-Sleep -Seconds 1
    if (-not $pm.HasExited) { Stop-Process -Id $pm.Id -Force }
    if (Test-Path $pmCsv) { Write-Host "PresentMon: $pmCsv" } else { Write-Host "PresentMon wrote nothing for $name" }
}
Write-Host "`nDone. Results are in $out"
