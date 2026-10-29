# Stop the processes started by scripts\start_demo.ps1 (only those running from this project).
$root = Split-Path -Parent $PSScriptRoot
$patterns = @('*manage.py runserver 127.0.0.1:8000*', '*edge\tls_edge.py*', '*streamlit run dashboard\app.py*')
$stopped = 0
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {
    $line = $_.CommandLine
    $line -and ($patterns | Where-Object { $line -like $_ })
} | ForEach-Object {
    try {
        Stop-Process -Id $_.ProcessId -Force -Confirm:$false -ErrorAction Stop
        $stopped += 1
    } catch {}
}
Write-Output "Stopped $stopped HumanProof process(es) started from $root."
