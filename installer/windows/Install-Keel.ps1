# Installs Keel for the current user. No administrator rights.
# The zip's Install-Keel.bat starts this. Python, PyTorch and TensorRT are downloaded
# from their own sites (about 5 GB). chiaki-ng is not in this package.

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$InfoPath = Join-Path $Here "package-info.json"
$Payload = Join-Path $Here "payload"
if (-not (Test-Path $InfoPath)) { throw "package-info.json is missing. Unzip the whole package and run Install-Keel.bat from that folder." }
if (-not (Test-Path (Join-Path $Payload "keel.py"))) { throw "The app files are missing from payload\." }

$info = Get-Content -Raw -Path $InfoPath | ConvertFrom-Json
$Dest = Join-Path $env:LOCALAPPDATA "Programs\Keel"
$PyDir = Join-Path $Dest "runtime\python"
$Py = Join-Path $PyDir "python.exe"
$Log = Join-Path $Dest "logs\install.log"

function Say($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Write-Host $line
    if (Test-Path (Split-Path $Log)) { Add-Content -Path $Log -Value $line }
}

New-Item -ItemType Directory -Force -Path (Join-Path $Dest "logs") | Out-Null
Say "Keel $($info.version) -> $Dest"
Say "This downloads about 5 GB (Python, PyTorch, TensorRT). It can take a while."

if (-not [Environment]::Is64BitOperatingSystem) { throw "Keel needs 64-bit Windows 10 or 11." }

Say "Copying the app"
$App = Join-Path $Dest "app"
if (Test-Path $App) { Remove-Item -Recurse -Force $App }
New-Item -ItemType Directory -Force -Path $App | Out-Null
Copy-Item -Path (Join-Path $Payload "*") -Destination $App -Recurse -Force
Copy-Item -Path $InfoPath -Destination (Join-Path $Dest "package-info.json") -Force
foreach ($name in @("LICENSE", "installer\THIRD_PARTY_NOTICES.txt")) {
    $src = Join-Path $Payload $name
    if (Test-Path $src) { Copy-Item $src -Destination (Join-Path $Dest (Split-Path $name -Leaf)) -Force }
}

if (-not (Test-Path $Py)) {
    Say "Downloading Python $($info.python_version)"
    $zip = Join-Path $Dest "runtime\python-embed.zip"
    New-Item -ItemType Directory -Force -Path (Split-Path $zip) | Out-Null
    Invoke-WebRequest -Uri $info.python_url -OutFile $zip
    $sha = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
    if ($sha -ne $info.python_sha256) {
        Remove-Item $zip -Force
        throw "The Python download is not the expected file (SHA-256 $sha)."
    }
    New-Item -ItemType Directory -Force -Path $PyDir | Out-Null
    Expand-Archive -Path $zip -DestinationPath $PyDir -Force
    Remove-Item $zip -Force
    $pth = Get-ChildItem $PyDir -Filter "python3*._pth" | Select-Object -First 1
    if (-not $pth) { throw "The Python package has no ._pth file." }
    $zipName = [IO.Path]::GetFileNameWithoutExtension($pth.Name) + ".zip"
    @(
        $zipName
        "."
        "Lib\site-packages"
        "..\..\app"
        "import site"
    ) -join "`r`n" | Set-Content -Path $pth.FullName -Encoding ascii
    Say "Python ready"
} else {
    Say "Python already installed"
}

Say "Installing pip"
& $Py -m pip install "setuptools>=70,<82" "wheel>=0.40" --no-warn-script-location
if ($LASTEXITCODE -ne 0) {
    $getpip = Join-Path $Dest "runtime\get-pip.py"
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getpip
    & $Py $getpip "setuptools>=70,<82" "wheel>=0.40" --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { throw "Installing pip failed." }
}

Say "Downloading PyTorch, TensorRT and the other packages (about 5 GB)"
$req = Join-Path $App "installer\requirements-runtime.txt"
& $Py -m pip install --no-build-isolation --upgrade -r $req `
    --extra-index-url $info.torch_index --extra-index-url $info.nvidia_index
if ($LASTEXITCODE -ne 0) { throw "Installing the packages failed. The log is $Log" }

$cmd = Join-Path $Dest "Keel.cmd"
@"
@echo off
setlocal
cd /d "%~dp0app"
if exist "%~dp0keel-local.cmd" call "%~dp0keel-local.cmd"
"%~dp0runtime\python\python.exe" -X utf8 "%~dp0app\keel.py" %*
"@ | Set-Content -Path $cmd -Encoding ascii

$local = Join-Path $Dest "keel-local.cmd"
if (-not (Test-Path $local)) {
    @"
@echo off
rem Put the full path of chiaki.exe here if Keel does not find it.
rem set KEEL_CHIAKI=C:\path\to\chiaki.exe
"@ | Set-Content -Path $local -Encoding ascii
}

$vc = Join-Path $env:SystemRoot "System32\vcruntime140_1.dll"
if (-not (Test-Path $vc)) {
    Say "The Visual C++ runtime is missing. Downloading Microsoft's installer."
    $redist = Join-Path $Dest "runtime\vc_redist.x64.exe"
    Invoke-WebRequest -Uri "https://aka.ms/vs/17/release/vc_redist.x64.exe" -OutFile $redist
    $p = Start-Process -FilePath $redist -ArgumentList "/install","/passive","/norestart" -Wait -PassThru
    Say "Visual C++ installer exit code $($p.ExitCode)"
}

$wsh = New-Object -ComObject WScript.Shell
foreach ($pair in @(
    @{ Name = "Keel.lnk"; Folder = [Environment]::GetFolderPath("Desktop") },
    @{ Name = "Keel.lnk"; Folder = (Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs") }
)) {
    New-Item -ItemType Directory -Force -Path $pair.Folder | Out-Null
    $sc = $wsh.CreateShortcut((Join-Path $pair.Folder $pair.Name))
    $sc.TargetPath = $cmd
    $sc.WorkingDirectory = $Dest
    $sc.Description = "Keel"
    $sc.Save()
}

Say "Installed. Start Keel from the desktop shortcut."
Say "chiaki-ng with the frame tap is not in this package. Set KEEL_CHIAKI in $local to chiaki.exe before you play."
Say "The first Play also downloads the RIFE weights and can spend a minute or two building the TensorRT engine."
Write-Host ""
Write-Host "Keel is at $Dest"
Write-Host "Press Enter to close."
[void](Read-Host)
