# Builds the FieldOrtho Windows installers. Everything needed is downloaded automatically.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#
# Produces in dist\:
#   FieldOrtho_Setup_<v>_Online.exe   small; downloads the OpenDroneMap engine during setup
#   FieldOrtho_Setup_<v>_Offline.exe  everything inside (engine included) - for computers without internet
#   build-info.json                   versions, sizes and SHA-256 of what was built
#
# Options:  -SkipTests   -SkipOffline   -Python <path to python.exe 3.11/3.12>
param([switch]$SkipTests, [switch]$SkipOffline, [string]$Python = "python")
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"          # makes Invoke-WebRequest much faster
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$version = (Select-String -Path fieldortho\__init__.py -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
New-Item -ItemType Directory -Force dist, data, packaging\redist, build | Out-Null
$headers = @{ "User-Agent" = "fieldortho-build" }
if ($env:GITHUB_TOKEN) { $headers["Authorization"] = "Bearer $env:GITHUB_TOKEN" }
function Step($t) { Write-Host ""; Write-Host "==== $t" -ForegroundColor Cyan }

Step "1/7 Python environment"
if (-not (Test-Path .venv)) { & $Python -m venv .venv }
$py = Join-Path $root ".venv\Scripts\python.exe"
& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r requirements.txt pyinstaller pytest --quiet
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Step "2/7 EGM96 geoid grid (sea-level height conversion)"
if (-not (Test-Path data\us_nga_egm96_15.tif)) {
    Invoke-WebRequest "https://cdn.proj.org/us_nga_egm96_15.tif" -OutFile data\us_nga_egm96_15.tif -Headers $headers
}

Step "3/7 OpenDroneMap engine (latest release)"
$rel = Invoke-RestMethod "https://api.github.com/repos/OpenDroneMap/ODM/releases/latest" -Headers $headers
$asset = $rel.assets | Where-Object { $_.name -like "ODM_Setup_*.exe" } | Select-Object -First 1
if (-not $asset) { throw "No ODM_Setup_*.exe in OpenDroneMap release $($rel.tag_name)" }
$odmExe = "packaging\redist\ODM_Setup.exe"
if (-not (Test-Path $odmExe) -or (Get-Item $odmExe).Length -ne $asset.size) {
    Write-Host "   downloading $($asset.name) ($([math]::Round($asset.size/1MB)) MB)"
    Invoke-WebRequest $asset.browser_download_url -OutFile $odmExe -Headers @{ "User-Agent" = "fieldortho-build" }
}
$odmSha = (Get-FileHash $odmExe -Algorithm SHA256).Hash.ToLower()
Write-Host "   $($rel.tag_name)  sha256 $odmSha"

Step "4/7 Inno Setup compiler"
$isccPaths = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
               "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")
$iscc = $isccPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
    } elseif (Get-Command choco -ErrorAction SilentlyContinue) {
        choco install innosetup -y --no-progress | Out-Null
    } else {
        $is = "build\innosetup.exe"
        Invoke-WebRequest "https://jrsoftware.org/download.php/is.exe" -OutFile $is -Headers $headers
        Start-Process $is -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-" -Wait
    }
    $iscc = $isccPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) { throw "Inno Setup could not be installed - install Inno Setup 6 from jrsoftware.org and rerun" }
}

$testResult = "skipped"
if (-not $SkipTests) {
    Step "5/7 Automated tests"
    # Windows PowerShell 5.1 turns redirected stderr into errors; relax while the tests run
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & $py -m pytest -q -W ignore tests 2>&1 | Tee-Object -FilePath build\pytest.log
    $code = $LASTEXITCODE
    $ErrorActionPreference = $old
    $testResult = if ($code -eq 0) { "passed" } else { "FAILED" }
    Write-Host "   tests: $testResult"
}

Step "6/7 Freeze the application (PyInstaller)"
& $py -m PyInstaller packaging\fieldortho.spec --noconfirm --distpath dist --workpath build\pyi
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
& dist\FieldOrtho\fieldortho-cli.exe --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Frozen command line does not start" }

Step "7/7 Installers (Inno Setup)"
& $iscc /Qp "/DMyAppVersion=$version" "/DODM_URL=$($asset.browser_download_url)" "/DODM_SHA256=$odmSha" `
        "/DODM_VERSION=$($rel.tag_name)" packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw "Online installer failed to compile" }
$offline = $null
if (-not $SkipOffline) {
    & $iscc /Qp "/DMyAppVersion=$version" /DBUNDLE_ENGINE "/DODM_VERSION=$($rel.tag_name)" packaging\installer.iss
    if ($LASTEXITCODE -ne 0) { throw "Offline installer failed to compile" }
    $offline = Get-Item "dist\FieldOrtho_Setup_${version}_Offline.exe"
}
$online = Get-Item "dist\FieldOrtho_Setup_${version}_Online.exe"

$info = [ordered]@{
    version = $version; built = (Get-Date).ToString("s"); tests = $testResult
    odm_version = $rel.tag_name; odm_url = $asset.browser_download_url; odm_sha256 = $odmSha
    online = @{ file = $online.Name; mb = [math]::Round($online.Length / 1MB, 1);
                sha256 = (Get-FileHash $online.FullName).Hash.ToLower() }
}
if ($offline) {
    $info.offline = @{ file = $offline.Name; mb = [math]::Round($offline.Length / 1MB, 1);
                       sha256 = (Get-FileHash $offline.FullName).Hash.ToLower() }
}
$info | ConvertTo-Json -Depth 4 | Set-Content dist\build-info.json -Encoding UTF8
Get-Content dist\build-info.json
Write-Host "Done." -ForegroundColor Green
