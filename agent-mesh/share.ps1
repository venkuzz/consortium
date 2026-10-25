# Consortium — one-command public share via Cloudflare Tunnel.
#
#   Starts the Consortium server, then opens a Cloudflare "quick tunnel" that
#   gives you a public https://<random>.trycloudflare.com URL. Hand that URL to
#   your 2 teammates — they open it in a browser from anywhere. No firewall
#   rule, no VPN, no router config.
#
# Usage (from the agent-mesh/ folder):
#   powershell -ExecutionPolicy Bypass -File .\share.ps1
#   powershell -ExecutionPolicy Bypass -File .\share.ps1 -Port 8000 -LeadProvider mock -RoomCode hunter2
#
# Press Ctrl+C to stop both the tunnel and the server.

param(
    [int]$Port = 8000,
    [string]$LeadProvider = "mock",
    [string]$RoomCode = ""
)

$ErrorActionPreference = "Stop"
$cf = "$env:USERPROFILE\.consortium\bin\cloudflared.exe"

# ── 1. Ensure cloudflared is present (download once, no admin needed) ────────
if (-not (Test-Path $cf)) {
    Write-Host "Downloading cloudflared (one-time)..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path (Split-Path $cf) | Out-Null
    $url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    Invoke-WebRequest -Uri $url -OutFile $cf -UseBasicParsing
}

# ── 2. Start the Consortium server (new window so it stays up) ───────────────
$env:LEAD_PROVIDER = $LeadProvider
if ($RoomCode -ne "") { $env:CONSORTIUM_ROOM_CODE = $RoomCode }
$env:PORT = "$Port"

Write-Host "Starting Consortium server on port $Port (LEAD_PROVIDER=$LeadProvider)..." -ForegroundColor Cyan
$server = Start-Process -FilePath "python" -ArgumentList "-u", "-m", "server.app" `
    -WorkingDirectory $PSScriptRoot -PassThru -WindowStyle Normal

# ── 3. Wait for the server to answer on /api/health ─────────────────────────
$ready = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $h = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
        if ($h.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}
if (-not $ready) {
    Write-Host "Server did not become healthy on port $Port. Check its window for errors." -ForegroundColor Red
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force }
    exit 1
}
Write-Host "Server is healthy at http://localhost:$Port" -ForegroundColor Green
if ($RoomCode -ne "") {
    Write-Host "Room code required to join: $RoomCode  (share it with your teammates)" -ForegroundColor Yellow
}

# ── 4. Open the Cloudflare quick tunnel (this blocks and prints the URL) ─────
Write-Host ""
Write-Host "Opening public tunnel... your shareable URL will appear below" -ForegroundColor Cyan
Write-Host "(look for  https://<something>.trycloudflare.com )" -ForegroundColor Cyan
Write-Host "Press Ctrl+C here to stop sharing and shut everything down." -ForegroundColor DarkGray
Write-Host ""

try {
    & $cf tunnel --url "http://localhost:$Port"
}
finally {
    Write-Host "`nShutting down server..." -ForegroundColor Cyan
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force }
}
