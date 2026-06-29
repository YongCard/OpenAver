$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

$TestEnvDir = Join-Path $RootDir ".tmp\openaver-test-env"
$TestLogs = Join-Path $TestEnvDir "logs"
$TestTemp = Join-Path $TestEnvDir "tmp"
New-Item -ItemType Directory -Force -Path $TestLogs, $TestTemp | Out-Null
$env:OPENAVER_LOG_DIR = $TestLogs
$env:TMPDIR = $TestTemp
$env:TMP = $TestTemp
$env:TEMP = $TestTemp

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE"
    }
}

if ($env:PYTHON_BIN) {
    $PythonBin = $env:PYTHON_BIN
} elseif (Test-Path ".venv\Scripts\python.exe") {
    $PythonBin = ".venv\Scripts\python.exe"
} elseif (Test-Path "venv\Scripts\python.exe") {
    $PythonBin = "venv\Scripts\python.exe"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonBin = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonBin = "py"
} else {
    throw "Python 3.12 is required but was not found."
}

if ($env:OPENAVER_SKIP_INSTALL -ne "1") {
    Invoke-Checked { & $PythonBin -m pip install -r requirements-test.txt }
}

Write-Host "==> Enterprise hygiene check"
Invoke-Checked { & $PythonBin scripts/check_enterprise_hygiene.py }

Write-Host "==> Unit and integration tests"
Invoke-Checked { & $PythonBin -m pytest tests/unit tests/integration -v --cache-clear }

Write-Host "==> Python lint"
Invoke-Checked { & $PythonBin -m ruff check . }

if ($env:OPENAVER_SKIP_NPM_INSTALL -ne "1") {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "npm is required for frontend lint but was not found."
    }
    Invoke-Checked { npm ci }
}

Write-Host "==> Frontend lint"
Invoke-Checked { npm run lint }

Write-Host "All OpenAver local verification checks passed."
