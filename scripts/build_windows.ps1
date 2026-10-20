[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$SkipInstall,
    [switch]$IncludeLocalSettings,
    [string]$SigningPrivateKey = "",
    [string]$OutputDirectory = "dist"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$SigningKeyPath = if ($SigningPrivateKey) {
    [IO.Path]::GetFullPath((Join-Path $ProjectRoot $SigningPrivateKey))
} else {
    Join-Path $ProjectRoot "runtime\update-signing-private.json"
}
if (-not (Test-Path -LiteralPath $SigningKeyPath -PathType Leaf)) {
    throw "Update signing private key was not found: $SigningKeyPath"
}
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
    $ClientVersion = (& $PythonExe -c "import sys; sys.path.insert(0, 'src'); from hoyo_analyzer import __version__; print(__version__)").Trim()
    if ($LASTEXITCODE -ne 0 -or $ClientVersion -notmatch '^V\d+\.\d+\.\d+$') {
        throw "Client version must use the V1.0.0 format."
    }
    if (Test-Path -LiteralPath $DistRoot) {
        Write-Host "Removing previous release output: $DistRoot" -ForegroundColor Yellow
        Remove-Item -LiteralPath $DistRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null

    $UpdaterDist = Join-Path $ProjectRoot "build\updater-dist"
    if (Test-Path -LiteralPath $UpdaterDist) {
        Remove-Item -LiteralPath $UpdaterDist -Recurse -Force
    }
    & $PythonExe -m PyInstaller --noconfirm --clean `
        --distpath $UpdaterDist `
        --workpath (Join-Path $ProjectRoot "build\pyinstaller-updater") `
        (Join-Path $ProjectRoot "hoyo_updater.spec")
    if ($LASTEXITCODE -ne 0) { throw "Updater PyInstaller build failed." }
    $UpdaterFiles = @(Get-ChildItem -LiteralPath $UpdaterDist -Filter "*.exe" -File)
    if ($UpdaterFiles.Count -ne 1) { throw "Expected exactly one packaged updater executable." }
    $UpdaterExe = $UpdaterFiles[0]
    $UpdaterSelfTest = Start-Process -FilePath $UpdaterExe.FullName -ArgumentList "--self-test" `
        -WindowStyle Hidden -Wait -PassThru
    if ($UpdaterSelfTest.ExitCode -ne 0) { throw "Packaged updater self-test failed." }

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

    $BuiltDirectories = @(Get-ChildItem -LiteralPath $DistRoot -Directory | Sort-Object LastWriteTime -Descending)
    if ($BuiltDirectories.Count -ne 2) { throw "Expected exactly two packaged application directories." }
    # The master build runs second, so it is the newest output directory.
    $MasterOutputRoot = $BuiltDirectories[0].FullName
    $SlaveOutputRoot = $BuiltDirectories[1].FullName
    foreach ($OutputRoot in @($SlaveOutputRoot, $MasterOutputRoot)) {
        if (-not (Test-Path -LiteralPath $OutputRoot)) { throw "PyInstaller output directory was not found: $OutputRoot" }
        Copy-Item -LiteralPath (Join-Path $ProjectRoot "assets") -Destination $OutputRoot -Recurse -Force
        Copy-Item -LiteralPath $UpdaterExe.FullName -Destination $OutputRoot -Force
    }
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "configs") -Destination $SlaveOutputRoot -Recurse -Force
    $SlaveRuntime = Join-Path $SlaveOutputRoot "runtime"
    $MasterRuntime = Join-Path $MasterOutputRoot "runtime"
    New-Item -ItemType Directory -Path $SlaveRuntime -Force | Out-Null
    New-Item -ItemType Directory -Path $MasterRuntime -Force | Out-Null
    # Published packages must start without machine-specific identities or
    # credentials. Each slave/master writes its own settings on first setup.
    if ($IncludeLocalSettings) {
        $LocalSlaveSettings = Join-Path $ProjectRoot "runtime\slave_settings.json"
        $LocalMasterSettings = Join-Path $ProjectRoot "runtime\master_settings.json"
        if (-not (Test-Path -LiteralPath $LocalSlaveSettings)) {
            throw "Local slave settings were requested but runtime\slave_settings.json was not found."
        }
        if (-not (Test-Path -LiteralPath $LocalMasterSettings)) {
            throw "Local master settings were requested but runtime\master_settings.json was not found."
        }
        Copy-Item -LiteralPath $LocalSlaveSettings -Destination $SlaveRuntime -Force
        Copy-Item -LiteralPath $LocalMasterSettings -Destination $MasterRuntime -Force
        Write-Host "Including local device/master credentials in this private build." -ForegroundColor Yellow
    }
    $Readme = Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "packaging") -File | Select-Object -First 1
    Copy-Item -LiteralPath $Readme.FullName -Destination $SlaveOutputRoot -Force
    Copy-Item -LiteralPath $Readme.FullName -Destination $MasterOutputRoot -Force
    $OutputExe = Get-ChildItem -LiteralPath $SlaveOutputRoot -Filter "*.exe" -File | Where-Object Name -ne $UpdaterExe.Name
    $MasterExe = Get-ChildItem -LiteralPath $MasterOutputRoot -Filter "*.exe" -File | Where-Object Name -ne $UpdaterExe.Name
    if (-not $OutputExe -or -not $MasterExe) { throw "Packaged executable was not found." }

    $SelfTest = Start-Process -FilePath $OutputExe.FullName -ArgumentList "--package-self-test" `
        -WindowStyle Hidden -Wait -PassThru
    if ($SelfTest.ExitCode -ne 0) { throw "Packaged executable self-test failed." }
    $SelfTestReport = Join-Path $SlaveOutputRoot "runtime\package-self-test.json"
    if (-not (Test-Path -LiteralPath $SelfTestReport)) { throw "Package self-test report was not created." }
    $SelfTestResult = Get-Content -LiteralPath $SelfTestReport -Raw | ConvertFrom-Json
    if (-not $SelfTestResult.ok) { throw "Package self-test reported a dependency failure." }
    if ($SelfTestResult.client_version -ne $ClientVersion) { throw "Slave package version does not match the release version." }

    $PreviousQtPlatform = $env:QT_QPA_PLATFORM
    try {
        $env:QT_QPA_PLATFORM = "offscreen"
        $UiSelfTest = Start-Process -FilePath $OutputExe.FullName -ArgumentList "--package-ui-self-test" `
            -WindowStyle Hidden -Wait -PassThru
    } finally {
        if ($null -eq $PreviousQtPlatform) {
            Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
        } else {
            $env:QT_QPA_PLATFORM = $PreviousQtPlatform
        }
    }
    if ($UiSelfTest.ExitCode -ne 0) { throw "Packaged UI startup self-test failed." }
    $UiSelfTestReport = Join-Path $SlaveOutputRoot "runtime\package-ui-self-test.json"
    if (-not (Test-Path -LiteralPath $UiSelfTestReport)) { throw "Package UI self-test report was not created." }
    $UiSelfTestResult = Get-Content -LiteralPath $UiSelfTestReport -Raw | ConvertFrom-Json
    if (-not $UiSelfTestResult.ok) { throw "Package UI self-test reported a startup failure." }

    $MasterSelfTest = Start-Process -FilePath $MasterExe.FullName -ArgumentList "--package-self-test" `
        -WindowStyle Hidden -Wait -PassThru
    if ($MasterSelfTest.ExitCode -ne 0) { throw "Master packaged executable self-test failed." }
    $MasterSelfTestReport = Join-Path $MasterOutputRoot "runtime\master-package-self-test.json"
    if (-not (Test-Path -LiteralPath $MasterSelfTestReport)) { throw "Master package self-test report was not created." }
    $MasterSelfTestResult = Get-Content -LiteralPath $MasterSelfTestReport -Raw | ConvertFrom-Json
    if (-not $MasterSelfTestResult.ok) { throw "Master package self-test reported a dependency failure." }
    if ($MasterSelfTestResult.client_version -ne $ClientVersion) { throw "Master package version does not match the release version." }

    $MasterUiSelfTest = Start-Process -FilePath $MasterExe.FullName -ArgumentList "--package-ui-self-test" `
        -WindowStyle Hidden -Wait -PassThru
    if ($MasterUiSelfTest.ExitCode -ne 0) { throw "Master packaged UI startup self-test failed." }
    $MasterUiResult = Get-Content -LiteralPath (Join-Path $MasterRuntime "master-package-ui-self-test.json") -Raw | ConvertFrom-Json
    if (-not $MasterUiResult.ok) { throw "Master package UI self-test reported a startup failure." }
    # UI self-tests create synthetic startup/close logs; never publish these.
    $TestOutbox = Join-Path $SlaveRuntime "monitor-log-outbox.json"
    if (Test-Path -LiteralPath $TestOutbox) {
        Remove-Item -LiteralPath $TestOutbox -Force
    }

    $SlaveArchive = Join-Path $DistRoot ((Split-Path -Leaf $SlaveOutputRoot) + ".zip")
    $MasterArchive = Join-Path $DistRoot ((Split-Path -Leaf $MasterOutputRoot) + ".zip")
    Compress-Archive -LiteralPath $SlaveOutputRoot -DestinationPath $SlaveArchive -CompressionLevel Optimal
    Compress-Archive -LiteralPath $MasterOutputRoot -DestinationPath $MasterArchive -CompressionLevel Optimal
    $SlaveHash = (Get-FileHash -LiteralPath $SlaveArchive -Algorithm SHA256).Hash.ToLowerInvariant()
    $MasterHash = (Get-FileHash -LiteralPath $MasterArchive -Algorithm SHA256).Hash.ToLowerInvariant()
    $SlavePackage = [ordered]@{
        filename = (Split-Path -Leaf $SlaveArchive)
        sha256 = $SlaveHash
        size = (Get-Item -LiteralPath $SlaveArchive).Length
        download_path = "/api/v1/updates/download/slave/$ClientVersion"
        archive_root = (Split-Path -Leaf $SlaveOutputRoot)
        executable = $OutputExe.Name
    }
    $MasterPackage = [ordered]@{
        filename = (Split-Path -Leaf $MasterArchive)
        sha256 = $MasterHash
        size = (Get-Item -LiteralPath $MasterArchive).Length
        download_path = "/api/v1/updates/download/master/$ClientVersion"
        archive_root = (Split-Path -Leaf $MasterOutputRoot)
        executable = $MasterExe.Name
    }
    $Manifest = [ordered]@{
        schema_version = 2
        built_at = [DateTime]::UtcNow.ToString("o")
        client_version = $ClientVersion
        contains_local_credentials = [bool]$IncludeLocalSettings
        packages = [ordered]@{
            slave = $SlavePackage
            master = $MasterPackage
        }
        layout = [ordered]@{
            slave_directory = (Split-Path -Leaf $SlaveOutputRoot)
            master_directory = (Split-Path -Leaf $MasterOutputRoot)
            slave_archive = (Split-Path -Leaf $SlaveArchive)
            master_archive = (Split-Path -Leaf $MasterArchive)
        }
        checks = [ordered]@{
            slave_dependencies = $true
            slave_ui_startup = $true
            master_dependencies = $true
            master_ui_startup = $true
            client_version_sync = $true
        }
        sha256 = [ordered]@{
            slave_archive = $SlaveHash
            master_archive = $MasterHash
        }
    }
    $SignatureLines = @(
        "hoyo-update-v1", "2", $ClientVersion,
        "slave", $SlavePackage.filename, $SlavePackage.sha256, [string]$SlavePackage.size,
        $SlavePackage.download_path, $SlavePackage.archive_root, $SlavePackage.executable,
        "master", $MasterPackage.filename, $MasterPackage.sha256, [string]$MasterPackage.size,
        $MasterPackage.download_path, $MasterPackage.archive_root, $MasterPackage.executable
    )
    $SignaturePayload = [Text.Encoding]::UTF8.GetBytes(($SignatureLines -join "`n") + "`n")
    $StoredKey = Get-Content -LiteralPath $SigningKeyPath -Raw | ConvertFrom-Json
    $PublicKey = Get-Content -LiteralPath (Join-Path $ProjectRoot "assets\update-public-key.json") -Raw | ConvertFrom-Json
    if ($StoredKey.Modulus -ne $PublicKey.modulus -or $StoredKey.Exponent -ne $PublicKey.exponent) {
        throw "Update signing private key does not match the public key embedded in the clients."
    }
    $Parameters = [System.Security.Cryptography.RSAParameters]::new()
    foreach ($Name in @("Modulus", "Exponent", "D", "P", "Q", "DP", "DQ", "InverseQ")) {
        $Parameters.$Name = [Convert]::FromBase64String([string]$StoredKey.$Name)
    }
    $Rsa = [System.Security.Cryptography.RSA]::Create()
    try {
        $Rsa.ImportParameters($Parameters)
        $SignatureBytes = $Rsa.SignData(
            $SignaturePayload,
            [System.Security.Cryptography.HashAlgorithmName]::SHA256,
            [System.Security.Cryptography.RSASignaturePadding]::Pkcs1
        )
    } finally {
        $Rsa.Dispose()
    }
    $Manifest.signature = [ordered]@{
        algorithm = "RSA-3072-SHA256"
        value = [Convert]::ToBase64String($SignatureBytes)
    }
    $ManifestPath = Join-Path $DistRoot "release-manifest.json"
    $ManifestJson = ($Manifest | ConvertTo-Json -Depth 6) + [Environment]::NewLine
    [IO.File]::WriteAllText($ManifestPath, $ManifestJson, [Text.UTF8Encoding]::new($false))
    & $PythonExe -c "import json,sys; sys.path.insert(0,'src'); from hoyo_analyzer.auto_update import verify_manifest_signature; d=json.load(open(sys.argv[1],encoding='utf-8-sig')); raise SystemExit(0 if verify_manifest_signature(d) else 1)" $ManifestPath
    if ($LASTEXITCODE -ne 0) { throw "Release manifest signature self-test failed." }

    Write-Host ""
    Write-Host "Slave build: $SlaveOutputRoot" -ForegroundColor Green
    Write-Host "Master build: $MasterOutputRoot" -ForegroundColor Green
    Write-Host "Slave archive: $SlaveArchive" -ForegroundColor Green
    Write-Host "Master archive: $MasterArchive" -ForegroundColor Green
    Write-Host "Updater, signature, dependency, UI startup, and master package self-tests: passed" -ForegroundColor Green
} finally {
    Pop-Location
}
