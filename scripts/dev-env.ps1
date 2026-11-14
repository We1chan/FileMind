# Dot-source from PowerShell before running FileMind development commands.
$FileMindDataDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../data'))
$env:UV_CACHE_DIR = Join-Path $FileMindDataDir 'cache/uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $FileMindDataDir 'python'
$env:PYTHONPYCACHEPREFIX = Join-Path $FileMindDataDir 'cache/python'
$env:TEMP = Join-Path $FileMindDataDir 'tmp'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
