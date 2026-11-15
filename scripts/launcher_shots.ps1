# Snapshots of the launcher at several window sizes, for checking the layout (SMOOVPLAY_SHOTS: the window as WPF
# renders it, never a screen capture). Opens and closes the launcher once per case, with its own prefs and settings
# files, so your own are never touched. Uses the GPU only to draw the window.
#
#   powershell -ExecutionPolicy Bypass -File scripts\launcher_shots.ps1 [-Exe installer\dist\SmoovPlay.exe] [-Only settings]
#
# Dev mode needs SMOOVPLAY_PYTHON (a Python with the app's packages), or -Python. Writes out\launcher_shots\<case>\.
param(
    [string]$Exe = "installer\dist\SmoovPlay.exe",
    [string]$Python = $env:SMOOVPLAY_PYTHON,
    [string]$Out = "out\launcher_shots",
    [int]$Seconds = 7,
    [string]$Only = ""  # only the cases whose name starts with this, e.g. "settings"
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$Exe = (Resolve-Path (Join-Path $repo $Exe)).Path
if (-not $Python) { throw "Say which Python: -Python or SMOOVPLAY_PYTHON" }
$Out = Join-Path $repo $Out

$groups = "set-open:Smoothness", "set-open:Picture", "set-open:Display", "set-open:PS5 Remote Play"
$changed = '{"scaler": "vsr", "present": "dxgi", "fullscreen": true, "motion": "half"}'
# name, page, size, prefs (besides guide-seen), settings.json ("" = none: defaults)
$cases = @(
    @("settings-min", "settings", "980x660", @(), ""),
    @("settings-default", "settings", "1036x700", @(), ""),
    @("settings-default-open", "settings", "1036x700", @("set-open:Picture"), $changed),
    @("settings-wide", "settings", "1500x950", @(), ""),
    @("settings-wide-open", "settings", "1500x950", $groups, $changed),
    @("play-min", "play", "980x660", @(), ""),
    @("play-wide", "play", "1500x950", @(), ""),
    @("guide-min", "guide", "980x660", @(), ""),
    @("guide-wide", "guide", "1500x950", @(), ""),
    @("check-min", "check", "980x660", @(), ""),
    @("check-wide", "check", "1500x950", @(), ""),
    @("help-min", "help", "980x660", @(), ""),
    @("help-wide", "help", "1500x950", @(), "")
)

foreach ($v in "FRAMEGEN_VFX_PATH", "FRAMEGEN_TRT_PATH", "NV_RTX_VIDEO_SDK") {  # a shell older than these
    if (-not (Test-Path "env:$v")) {
        $u = [Environment]::GetEnvironmentVariable($v, "User")
        if ($u) { Set-Item "env:$v" $u }
    }
}
$env:SMOOVPLAY_PYTHON = $Python
$env:SMOOVPLAY_APP = $repo
$env:SMOOVPLAY_UPDATE_CHECK = "0"

foreach ($c in $cases) {
    $name, $page, $size, $prefs, $settings = $c
    if ($Only -and -not $name.StartsWith($Only)) { continue }
    $dir = Join-Path $Out $name
    if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
    New-Item -ItemType Directory -Force $dir | Out-Null
    $tmp = Join-Path $dir "_state"
    New-Item -ItemType Directory -Force $tmp | Out-Null
    Set-Content -Encoding ascii (Join-Path $tmp "prefs.txt") (@("guide-seen") + $prefs)
    $sfile = Join-Path $tmp "settings.json"
    if ($settings) { [IO.File]::WriteAllText($sfile, $settings) }
    $env:SMOOVPLAY_SHOTS = $dir
    $env:SMOOVPLAY_SIZE = $size
    $env:SMOOVPLAY_PREFS = Join-Path $tmp "prefs.txt"
    $env:SMOOVPLAY_SETTINGS = $sfile
    $p = Start-Process -FilePath $Exe -ArgumentList "--page", $page -PassThru
    Start-Sleep -Seconds $Seconds
    if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }
    $n = (Get-ChildItem $dir -Filter *.png).Count
    "{0,-24} {1,-9} {2,-9} {3} shots" -f $name, $page, $size, $n
}
"Done: $Out"
