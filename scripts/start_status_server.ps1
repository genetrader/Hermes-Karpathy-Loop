$py = "<LOCALAPPDATA>\..\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
$existing = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if (-not $existing) {
  Start-Process -FilePath $py -ArgumentList '"C:\CODING\project-improver\scripts\status_server.py"' -WindowStyle Hidden
}
