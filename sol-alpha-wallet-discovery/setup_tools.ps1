param(
  [string]$VendorDir = "$PSScriptRoot\vendor"
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $VendorDir | Out-Null

function Sync-Repo($Url, $Name) {
  $Path = Join-Path $VendorDir $Name
  if (Test-Path (Join-Path $Path ".git")) {
    Write-Host "[update] $Name"
    git -C $Path pull --ff-only
  } else {
    Write-Host "[clone] $Name"
    git clone --depth 1 $Url $Path
  }
}

Sync-Repo "https://github.com/carlosmmora26/DegenRadar.git" "DegenRadar"
Sync-Repo "https://github.com/GMGNAI/gmgn-skills.git" "gmgn-skills"
Sync-Repo "https://github.com/non-contextual/solana-holder-analyzer.git" "solana-holder-analyzer"

if (Get-Command npm -ErrorAction SilentlyContinue) {
  Write-Host "[install] gmgn-cli"
  npm install -g gmgn-cli
} else {
  Write-Warning "npm not found; GMGN CLI was not installed."
}

Write-Host ""
Write-Host "Upstreams ready under: $VendorDir"
Write-Host "Historical Walk-forward still uses timestamp-bounded chain data as the source of truth."
