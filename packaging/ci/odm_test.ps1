# Real OpenDroneMap run through the INSTALLED WangDoodletheGIS, on public drone photos
# (OpenDroneMap's odm_data_aukerman). Job folder deliberately contains spaces.
param([int]$Photos = 30, [string]$Preset = "standard")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root
New-Item -ItemType Directory -Force C:\ci_logs, "C:\ci\photos" | Out-Null
Get-ChildItem C:\data\aukerman\images\*.JPG | Sort-Object Name | Select-Object -First $Photos |
    Copy-Item -Destination C:\ci\photos
$jobs = "C:\ci\WangDoodletheGIS Jobs"
# start clean: the checks below inspect the (single) job folder in here
if (Test-Path $jobs) { Remove-Item -Recurse -Force $jobs }
$t0 = Get-Date
$app = "$env:ProgramFiles\WangDoodletheGIS\WangDoodletheGIS-cli.exe"
# Windows PowerShell 5.1 turns native stderr into errors under "Stop"; judge by exit code instead
$ErrorActionPreference = "Continue"
& $app process C:\ci\photos --name "Aukerman test" --preset $Preset --jobs-root $jobs 2>&1 |
    Tee-Object -FilePath C:\ci_logs\odm_cli.txt
$code = $LASTEXITCODE
$mins = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)
$job = Get-ChildItem $jobs -Directory | Select-Object -First 1
if ($job -and (Test-Path "$($job.FullName)\processing_log.txt")) {
    Get-Content "$($job.FullName)\processing_log.txt" -Tail 300 | Set-Content C:\ci_logs\odm_log_tail.txt
}
# inspect the GIS outputs with the build environment's Python (rasterio)
$py = Join-Path $root ".venv\Scripts\python.exe"
$check = @"
import json, glob, os, sys
import rasterio
gis = glob.glob(r'$jobs\*\GIS')
res = {'exit_code': $code, 'minutes': $mins, 'gis_folder': bool(gis), 'files': {}}
if gis:
    for f in sorted(glob.glob(os.path.join(gis[0], '*'))):
        if f.endswith('.tif'):
            with rasterio.open(f) as ds:
                res['files'][os.path.basename(f)] = {'crs': str(ds.crs), 'res_m': round(ds.res[0], 4),
                                                    'size': [ds.width, ds.height]}
        else:
            res['files'][os.path.basename(f)] = os.path.getsize(f) if os.path.isfile(f) else 'dir'
    s = os.path.join(gis[0], 'summary.json')
    if os.path.exists(s):
        res['summary'] = {k: v for k, v in json.load(open(s))['summary'].items()
                          if not k.endswith('_preview')}
names = ' '.join(res['files'])
res['ok'] = $code == 0 and all(k in names for k in ('orthomosaic', 'DSM', 'DTM', 'contours'))
json.dump(res, open(r'C:\ci_logs\odm_test.json', 'w'), indent=1, default=str)
print(json.dumps(res, indent=1, default=str))
sys.exit(0 if res['ok'] else 1)
"@
$check | Set-Content build\check_odm.py -Encoding UTF8
& $py build\check_odm.py
exit $LASTEXITCODE
