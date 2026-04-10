param(
    [int[]]$Ports = @(8765, 8000)
)

$ErrorActionPreference = "Stop"

function Get-ListeningPidsByPort {
    param([int]$Port)
    $lines = netstat -ano | Select-String -Pattern "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$"
    $pids = @()
    foreach ($line in $lines) {
        $text = $line.ToString().Trim()
        $parts = $text -split "\s+"
        if ($parts.Length -ge 5) {
            $procId = 0
            if ([int]::TryParse($parts[-1], [ref]$procId) -and $procId -gt 0) {
                $pids += $procId
            }
        }
    }
    return $pids | Sort-Object -Unique
}

$allKilled = @()
$allSkipped = @()

foreach ($port in $Ports) {
    $pids = Get-ListeningPidsByPort -Port $port
    foreach ($procId in $pids) {
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if (-not $proc) {
            continue
        }

        if ($proc.ProcessName -notlike "python*") {
            $allSkipped += [PSCustomObject]@{
                Port   = $port
                PID    = $procId
                Name   = $proc.ProcessName
                Action = "Skip(non-python)"
            }
            continue
        }

        try {
            taskkill /PID $procId /F | Out-Null
            $allKilled += [PSCustomObject]@{
                Port   = $port
                PID    = $procId
                Name   = $proc.ProcessName
                Action = "Killed"
            }
        } catch {
            $allSkipped += [PSCustomObject]@{
                Port   = $port
                PID    = $procId
                Name   = $proc.ProcessName
                Action = "FailedToKill"
            }
        }
    }
}

Write-Host "=== Cleanup Result ==="
if ($allKilled.Count -gt 0) {
    Write-Host "[Killed]"
    $allKilled | Format-Table -AutoSize | Out-String | Write-Host
} else {
    Write-Host "[Killed] none"
}

if ($allSkipped.Count -gt 0) {
    Write-Host "[Skipped]"
    $allSkipped | Format-Table -AutoSize | Out-String | Write-Host
}

Write-Host "Done."
