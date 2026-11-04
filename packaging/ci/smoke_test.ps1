# Installs the freshly built Online installer silently (which downloads + installs the
# OpenDroneMap engine) and checks that the installed app really works on Windows.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root
New-Item -ItemType Directory -Force C:\ci_logs | Out-Null
$results = [ordered]@{}
function Check($name, $ok, $detail = "") {
    $results[$name] = @{ ok = [bool]$ok; detail = "$detail" }
    $mark = if ($ok) { "PASS" } else { "FAIL" }
    Write-Host "[$mark] $name  $detail"
}

$setup = Get-ChildItem dist\FieldOrtho_Setup_*_Online.exe | Select-Object -First 1
Write-Host "Installing $($setup.Name) silently..."
$t0 = Get-Date
$p = Start-Process $setup.FullName -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /LOG=C:\ci_logs\install.log" -Wait -PassThru
$mins = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)
Check "installer exit code 0" ($p.ExitCode -eq 0) "code $($p.ExitCode), $mins min"

$app = "$env:ProgramFiles\FieldOrtho"
Check "FieldOrtho.exe installed" (Test-Path "$app\FieldOrtho.exe")
Check "fieldortho-cli.exe installed" (Test-Path "$app\fieldortho-cli.exe")
Check "geoid grid bundled" (Test-Path "$app\_internal\data\us_nga_egm96_15.tif")
Check "engine downloaded and installed (C:\ODM\run.bat)" (Test-Path "C:\ODM\run.bat")
Check "start menu shortcut" (Test-Path "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\FieldOrtho\FieldOrtho.lnk")

# Windows PowerShell 5.1 turns native stderr (e.g. Python warnings) into errors under "Stop";
# relax it for the native calls and judge them by exit code + output instead
$ErrorActionPreference = "Continue"
$out = & "$app\fieldortho-cli.exe" engine 2>&1 | Out-String
$out | Set-Content C:\ci_logs\cli_engine.txt
Check "app finds the engine" ($LASTEXITCODE -eq 0) $out.Trim()

$geo = "C:\ci\georef out"
$out = & "$app\fieldortho-cli.exe" georef tests\data\maps\poblacion_landslide.jpg --lon "124d47'E" --lat "8d52'N" `
        --crs EPSG:3125 --classes mgb_landslide --formats gtiff,png,kmz --out $geo 2>&1 | Out-String
$out | Set-Content C:\ci_logs\cli_georef.txt
$rmse = if ($out -match "RMSE ([0-9.]+) m") { [double]$Matches[1] } else { 999 }
Check "georeferencer (installed app)" (($LASTEXITCODE -eq 0) -and ($rmse -lt 1.0)) "RMSE $rmse m"
$ErrorActionPreference = "Stop"
Check "georef GeoTIFF + polygons written" ((Test-Path "$geo\poblacion_landslide_georef.tif") -and (Test-Path "$geo\poblacion_landslide_classes.gpkg"))

# GUI must start and stay up (catches missing DLLs / Qt plugins)
$gui = Start-Process "$app\FieldOrtho.exe" -PassThru
Start-Sleep -Seconds 20
$alive = -not $gui.HasExited
Check "GUI starts and keeps running" $alive $(if (-not $alive) { "exit code $($gui.ExitCode)" } else { "" })
if ($alive) { Stop-Process -Id $gui.Id -Force }

$results | ConvertTo-Json -Depth 4 | Set-Content C:\ci_logs\smoke.json -Encoding UTF8
$failed = @($results.Values | Where-Object { -not $_.ok }).Count
if ($failed -gt 0) { Write-Host "$failed check(s) failed"; exit 1 }
