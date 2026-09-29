[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$SkipInstall,
    [string]$OutputDirectory = "dist"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$DistRoot = [IO.Path]::GetFullPath((Join-Path $ProjectRoot $OutputDirectory))
if (-not $DistRoot.StartsWith($ProjectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Build output must be a subdirectory of the project."
}
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if ($Python) {
    $PythonExe = (Resolve-Path -LiteralPath $Python).Path
} elseif (Test-Path -LiteralPath $VenvPython) {
    $PythonExe = $VenvPython
} else {
    throw "Project virtual environment was not found. Create .venv or pass -Python."
}

if (-not $SkipInstall) {
    & $PythonExe -m pip install "numpy>=1.26,<3" "opencv-python>=4.9,<5" "pygame>=2.6,<3" "PySide6>=6.7,<7" "windows-capture>=2.0.1,<3" "pyinstaller>=6,<7"
    if ($LASTEXITCODE -ne 0) { throw "Failed to install build dependencies." }
}

# PySide6 ships the current Microsoft C++ runtime. Prefer it while PyInstaller
# scans Qt and OpenCV native extensions.
$PySideDirectory = (& $PythonExe -c "import pathlib, PySide6; print(pathlib.Path(PySide6.__file__).resolve().parent)").Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $PySideDirectory)) {
    throw "Unable to locate the PySide6 runtime directory."
}
$PythonDirectory = Split-Path -Parent $PythonExe
$env:Path = "$PySideDirectory;$PythonDirectory;$env:SystemRoot\System32;$env:SystemRoot"

& $PythonExe -c "from PySide6.QtCore import qVersion; import cv2, pygame, windows_capture; print('Native dependency check:', qVersion(), cv2.__version__, pygame.version.ver, windows_capture.__name__)"
if ($LASTEXITCODE -ne 0) {
    throw "Qt/OpenCV/Windows Capture dependency check failed."
}

Push-Location $ProjectRoot
try {
    & $PythonExe -m PyInstaller --noconfirm --clean `
        --distpath $DistRoot `
        --workpath (Join-Path $ProjectRoot "build\pyinstaller") `
        (Join-Path $ProjectRoot "hoyo_analyzer.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    & $PythonExe -m PyInstaller --noconfirm --clean `
        --distpath $DistRoot `
        --workpath (Join-Path $ProjectRoot "build\pyinstaller-master") `
        (Join-Path $ProjectRoot "hoyo_master.spec")
    if ($LASTEXITCODE -ne 0) { throw "Master PyInstaller build failed." }

    $BuiltDirectories = @(Get-ChildItem -LiteralPath $DistRoot -Directory |
        Sort-Object LastWriteTime -Descending)
    if ($BuiltDirectories.Count -lt 2) { throw "Expected both slave and master output directories." }
    # The master build runs second, so it is the newest output directory.
    $MasterOutputRoot = $BuiltDirectories[0].FullName
    $SlaveOutputRoot = $BuiltDirectories[1].FullName
    foreach ($OutputRoot in @($SlaveOutputRoot, $MasterOutputRoot)) {
        if (-not (Test-Path -LiteralPath $OutputRoot)) { throw "PyInstaller output directory was not found: $OutputRoot" }
        Copy-Item -LiteralPath (Join-Path $ProjectRoot "assets") -Destination $OutputRoot -Recurse -Force
    }
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "configs") -Destination $SlaveOutputRoot -Recurse -Force
    $SlaveRuntime = Join-Path $SlaveOutputRoot "runtime"
    $MasterRuntime = Join-Path $MasterOutputRoot "runtime"
    New-Item -ItemType Directory -Path $SlaveRuntime -Force | Out-Null
    New-Item -ItemType Directory -Path $MasterRuntime -Force | Out-Null
    $LocalSlaveSettings = Join-Path $ProjectRoot "runtime\slave_settings.json"
    $LocalMasterSettings = Join-Path $ProjectRoot "runtime\master_settings.json"
    if (Test-Path -LiteralPath $LocalSlaveSettings) {
        Copy-Item -LiteralPath $LocalSlaveSettings -Destination $SlaveRuntime -Force
    }
    if (Test-Path -LiteralPath $LocalMasterSettings) {
        Copy-Item -LiteralPath $LocalMasterSettings -Destination $MasterRuntime -Force
    }
    $Readme = Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "packaging") -File | Select-Object -First 1
    Copy-Item -LiteralPath $Readme.FullName -Destination $SlaveOutputRoot -Force
    Copy-Item -LiteralPath $Readme.FullName -Destination $MasterOutputRoot -Force
    $OutputExe = Get-ChildItem -LiteralPath $SlaveOutputRoot -Filter "*.exe" -File | Select-Object -First 1
    $MasterExe = Get-ChildItem -LiteralPath $MasterOutputRoot -Filter "*.exe" -File | Select-Object -First 1
    if (-not $OutputExe -or -not $MasterExe) { throw "Packaged executable was not found." }

    $SelfTest = Start-Process -FilePath $OutputExe.FullName -ArgumentList "--package-self-test" `
        -WindowStyle Hidden -Wait -PassThru
    if ($SelfTest.ExitCode -ne 0) { throw "Packaged executable self-test failed." }
    $SelfTestReport = Join-Path $SlaveOutputRoot "runtime\package-self-test.json"
    if (-not (Test-Path -LiteralPath $SelfTestReport)) { throw "Package self-test report was not created." }
    $SelfTestResult = Get-Content -LiteralPath $SelfTestReport -Raw | ConvertFrom-Json
    if (-not $SelfTestResult.ok) { throw "Package self-test reported a dependency failure." }

    $MasterSelfTest = Start-Process -FilePath $MasterExe.FullName -ArgumentList "--package-self-test" `
        -WindowStyle Hidden -Wait -PassThru
    if ($MasterSelfTest.ExitCode -ne 0) { throw "Master packaged executable self-test failed." }
    $MasterSelfTestReport = Join-Path $MasterOutputRoot "runtime\master-package-self-test.json"
    if (-not (Test-Path -LiteralPath $MasterSelfTestReport)) { throw "Master package self-test report was not created." }
    $MasterSelfTestResult = Get-Content -LiteralPath $MasterSelfTestReport -Raw | ConvertFrom-Json
    if (-not $MasterSelfTestResult.ok) { throw "Master package self-test reported a dependency failure." }

    Write-Host ""
    Write-Host "Slave build: $SlaveOutputRoot" -ForegroundColor Green
    Write-Host "Master build: $MasterOutputRoot" -ForegroundColor Green
    Write-Host "Both package self-tests: passed" -ForegroundColor Green
} finally {
    Pop-Location
}
