[CmdletBinding()]
param(
    [switch]$SkipPip,
    [switch]$SkipVerify,
    [string]$Python
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $Python) {
    $Candidates = @(
        "$env:USERPROFILE\AppData\Local\Python\pythoncore-3.14-64\python.exe",
        "$env:USERPROFILE\AppData\Local\Programs\Python\Python314\python.exe",
        "$env:USERPROFILE\AppData\Local\Programs\Python\Python313\python.exe",
        "$env:USERPROFILE\AppData\Local\Programs\Python\Python312\python.exe",
        "$env:USERPROFILE\AppData\Local\Programs\Python\Python311\python.exe",
        "python.exe"
    )
    foreach ($cand in $Candidates) {
        if (Test-Path $cand) {
            $Python = $cand
            break
        }
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if ($cmd) {
            $Python = $cmd.Source
            break
        }
    }
}

if (-not $Python) {
    Write-Error "[setup:error] Python 3.10+ was not found. Please install Python and ensure it is in PATH."
    exit 1
}

$SetupScript = Join-Path $ScriptDir "setup.py"
$ArgsList = @($SetupScript)
if ($SkipPip) { $ArgsList += "--skip-pip" }
if ($SkipVerify) { $ArgsList += "--skip-verify" }
if ($Python) { $ArgsList += @("--python", $Python) }

& $Python $ArgsList
exit $LASTEXITCODE
