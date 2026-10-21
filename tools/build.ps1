# Windows wrapper for tools/build.sh (needs a bash with git, e.g. MSYS2 or Git Bash).
#
#   tools/build.ps1 [-Keymaps via,default,minimal,debug]
#
# Environment overrides (all optional):
#   G80_BASH         path to bash.exe (default: bash from PATH)
#   G80_ENV_SCRIPT   shell snippet that puts arm-none-eabi-gcc and qmk on PATH (passed through to build.sh)
#   G80_QMK_DIR, G80_OUT, G80_BUILD_DATE, G80_JOBS   see build.sh
param(
    [string[]]$Keymaps = @()
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path -replace '\\', '/'
$bash = if ($env:G80_BASH) { $env:G80_BASH } else { 'bash' }
$argList = ($Keymaps | ForEach-Object { "'$_'" }) -join ' '
& $bash -lc "cd '$repo' && tools/build.sh $argList"
exit $LASTEXITCODE
