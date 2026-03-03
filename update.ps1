# WiFi Monitor Update Script
# Updates the service from GitHub without requiring Git
# Run as Administrator

param(
    [string]$Branch = "main",
    [string]$Repo = "dcplibrary/wifi-monitor"
)

Write-Host "`n=== WiFi Monitor Update Script ===" -ForegroundColor Cyan
Write-Host "Repository: $Repo" -ForegroundColor Gray
Write-Host "Branch: $Branch`n" -ForegroundColor Gray

# Check if running as administrator
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: This script must be run as Administrator" -ForegroundColor Red
    Write-Host "Right-click PowerShell and select 'Run as Administrator'" -ForegroundColor Yellow
    exit 1
}

# Stop the service
Write-Host "[1/5] Stopping service..." -ForegroundColor Yellow
$service = Get-Service -Name "WirelessStatsService" -ErrorAction SilentlyContinue
if ($service) {
    if ($service.Status -eq "Running") {
        net stop WirelessStatsService
        Start-Sleep -Seconds 2
    }
    Write-Host "      Service stopped" -ForegroundColor Green
} else {
    Write-Host "      Service not found (will continue anyway)" -ForegroundColor Gray
}

# Download latest code as ZIP
Write-Host "[2/5] Downloading latest version from GitHub..." -ForegroundColor Yellow
$zipUrl = "https://github.com/$Repo/archive/refs/heads/$Branch.zip"
$zipFile = "$env:TEMP\wifi-monitor-update.zip"
$extractPath = "$env:TEMP\wifi-monitor-update"

try {
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipFile -UseBasicParsing
    Write-Host "      Downloaded successfully" -ForegroundColor Green
} catch {
    Write-Host "      ERROR: Failed to download from GitHub" -ForegroundColor Red
    Write-Host "      $_" -ForegroundColor Red
    if ($service) { net start WirelessStatsService }
    exit 1
}

# Extract
Write-Host "[3/5] Extracting files..." -ForegroundColor Yellow
if (Test-Path $extractPath) { 
    Remove-Item $extractPath -Recurse -Force 
}
Expand-Archive -Path $zipFile -DestinationPath $extractPath -Force

# Find the extracted folder (GitHub creates a subfolder with repo name)
$sourceFolder = Get-ChildItem -Path $extractPath -Directory | Select-Object -First 1
Write-Host "      Extracted to temp location" -ForegroundColor Green

# Copy files (preserve .env, database, and logs)
Write-Host "[4/5] Updating files (preserving config and data)..." -ForegroundColor Yellow
$currentDir = $PSScriptRoot
$excludeFiles = @(".env", "wireless_stats.db", "wireless_service.log", ".git")

Get-ChildItem -Path "$($sourceFolder.FullName)\*" -Recurse | ForEach-Object {
    $relativePath = $_.FullName.Substring($sourceFolder.FullName.Length + 1)
    $shouldExclude = $false
    
    foreach ($exclude in $excludeFiles) {
        if ($relativePath -like "*$exclude*") {
            $shouldExclude = $true
            break
        }
    }
    
    if (-not $shouldExclude) {
        $destination = Join-Path $currentDir $relativePath
        if ($_.PSIsContainer) {
            if (-not (Test-Path $destination)) {
                New-Item -ItemType Directory -Path $destination -Force | Out-Null
            }
        } else {
            Copy-Item -Path $_.FullName -Destination $destination -Force
        }
    }
}
Write-Host "      Files updated" -ForegroundColor Green

# Cleanup temp files
Write-Host "[5/5] Cleaning up..." -ForegroundColor Yellow
Remove-Item $zipFile -Force -ErrorAction SilentlyContinue
Remove-Item $extractPath -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "      Cleanup complete" -ForegroundColor Green

# Restart service
if ($service) {
    Write-Host "`nRestarting service..." -ForegroundColor Yellow
    net start WirelessStatsService
    Start-Sleep -Seconds 2
    
    $serviceStatus = (Get-Service -Name "WirelessStatsService").Status
    if ($serviceStatus -eq "Running") {
        Write-Host "Service restarted successfully" -ForegroundColor Green
    } else {
        Write-Host "WARNING: Service did not start. Status: $serviceStatus" -ForegroundColor Yellow
        Write-Host "Check logs at: $currentDir\wireless_service.log" -ForegroundColor Gray
    }
}

Write-Host "`n=== Update Complete ===" -ForegroundColor Cyan
Write-Host "Current directory: $currentDir" -ForegroundColor Gray
Write-Host ""
