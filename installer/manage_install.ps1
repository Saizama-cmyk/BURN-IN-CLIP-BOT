param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Install', 'Uninstall')]
    [string]$Action,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)
$ErrorActionPreference = 'Stop'
try {
    $setupArguments = @()
    foreach ($argument in $Arguments) {
        if ($argument -ieq '/quiet') {
            $setupArguments += @('/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
        } else {
            throw "Unsupported argument: $argument. Use /quiet for a silent installation."
        }
    }
    if ($Action -eq 'Install') {
        $releaseRoot = Split-Path -Parent $PSScriptRoot
        $setupPath = Join-Path $releaseRoot 'dist\BURN-IN-Setup.exe'
        if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) {
            throw 'Installer not found. Run installer\build_installer.bat first.'
        }
    } else {
        $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{6C1F2B4E-5A0D-4F7B-9C3E-5D0B7A51C0B7}_is1'
        $installed = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
        if (-not $installed -or -not $installed.UninstallString) {
            throw 'No registered BURN-IN installation was found. Install the current BURN-IN setup first to migrate an older manual installation.'
        }
        $setupPath = $installed.UninstallString.Trim('"')
        if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) {
            throw "The registered uninstaller was not found: $setupPath"
        }
    }
    Write-Host "$Action BURN-IN"
    $startParameters = @{ FilePath = $setupPath; Wait = $true; PassThru = $true }
    if ($setupArguments.Count) { $startParameters.ArgumentList = $setupArguments }
    $setupProcess = Start-Process @startParameters
    if ($setupProcess.ExitCode -ne 0 -and $setupProcess.ExitCode -ne 3010) {
        throw "BURN-IN setup exited with code $($setupProcess.ExitCode)."
    }
    exit 0
} catch {
    Write-Error $_
    exit 1
}

