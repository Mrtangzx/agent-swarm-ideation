# start_livekit_server.ps1 — boot the LiveKit dev server in the foreground.
# Stops with Ctrl-C.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location (Resolve-Path "$here\..")

if (-not (Test-Path "livekit/bin/livekit-server.exe")) {
    Write-Host "❌ livekit-server.exe not found at livekit/bin/." -ForegroundColor Red
    Write-Host "Download it from https://github.com/livekit/livekit/releases/latest" -ForegroundColor Yellow
    exit 1
}

Write-Host "▶ Starting LiveKit server on ws://localhost:7880 (dev mode, ctrl-c to stop)" -ForegroundColor Green
& ".\livekit\bin\livekit-server.exe" --config ".\livekit\livekit.yaml" --dev --bind 0.0.0.0