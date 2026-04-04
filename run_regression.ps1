param(
    [switch]$StrictNetwork,
    [switch]$SkipSmoke,
    [switch]$SkipUI,
    [switch]$SkipAlpha
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$UiRoot = Join-Path $Root 'ui\quant-dashboard'
$UiDistAssets = Join-Path $UiRoot 'dist\public\assets'
$Results = New-Object System.Collections.Generic.List[object]

function Add-StepResult {
    param(
        [string]$Status,
        [string]$Check,
        [double]$DurationSeconds,
        [string]$Detail
    )

    $Results.Add([pscustomobject]@{
        Status = $Status
        Check = $Check
        DurationSeconds = $DurationSeconds
        Duration = ('{0:N2}s' -f $DurationSeconds)
        Detail = $Detail
    }) | Out-Null
}

function Invoke-Step {
    param(
        [string]$Name,
        [string]$SuccessDetail,
        [scriptblock]$Action
    )

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $global:LASTEXITCODE = 0
    try {
        & $Action | Out-Host
        $code = if ($null -ne $global:LASTEXITCODE) { [int]$global:LASTEXITCODE } else { 0 }
        if ($code -eq 0) {
            Add-StepResult -Status 'OK' -Check $Name -DurationSeconds $sw.Elapsed.TotalSeconds -Detail $SuccessDetail
            return $true
        }
        Add-StepResult -Status 'ERR' -Check $Name -DurationSeconds $sw.Elapsed.TotalSeconds -Detail ('exit_code={0}' -f $code)
        return $false
    }
    catch {
        Add-StepResult -Status 'ERR' -Check $Name -DurationSeconds $sw.Elapsed.TotalSeconds -Detail $_.Exception.Message
        return $false
    }
    finally {
        $sw.Stop()
    }
}

function Invoke-UiStep {
    param(
        [string]$Name,
        [string]$SuccessDetail,
        [scriptblock]$Command
    )

    Invoke-Step -Name $Name -SuccessDetail $SuccessDetail -Action {
        Push-Location $UiRoot
        try {
            & $Command
        }
        finally {
            Pop-Location
        }
    }
}

function Invoke-UiHealthStep {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $warnings = New-Object System.Collections.Generic.List[string]
        $indexPaths = @(
            (Join-Path $UiRoot 'client\index.html'),
            (Join-Path $UiRoot 'quant-dashboard\client\index.html')
        )

        foreach ($indexPath in $indexPaths) {
            if (-not (Test-Path $indexPath)) {
                continue
            }
            $indexContent = Get-Content $indexPath -Raw -Encoding UTF8
            if ($indexContent -match '%VITE_ANALYTICS_[A-Z0-9_]+%') {
                $relative = $indexPath.Substring($Root.Length + 1)
                $warnings.Add(('analytics env placeholders remain in {0}' -f $relative)) | Out-Null
                break
            }
        }

        if (Test-Path $UiDistAssets) {
            $oversized = Get-ChildItem $UiDistAssets -Filter *.js |
                Where-Object { $_.Length -gt 512000 } |
                Sort-Object Length -Descending
            if ($oversized) {
                $top = $oversized |
                    Select-Object -First 3 |
                    ForEach-Object { '{0}={1}KB' -f $_.Name, [int][Math]::Round($_.Length / 1KB) }
                $warnings.Add(('oversized bundles: {0}' -f ($top -join ', '))) | Out-Null
            }
        }

        if ($warnings.Count -gt 0) {
            Add-StepResult -Status 'WARN' -Check 'ui.post_build' -DurationSeconds $sw.Elapsed.TotalSeconds -Detail ($warnings -join '; ')
            return $false
        }

        Add-StepResult -Status 'OK' -Check 'ui.post_build' -DurationSeconds $sw.Elapsed.TotalSeconds -Detail 'UI post-build checks passed.'
        return $true
    }
    catch {
        Add-StepResult -Status 'ERR' -Check 'ui.post_build' -DurationSeconds $sw.Elapsed.TotalSeconds -Detail $_.Exception.Message
        return $false
    }
    finally {
        $sw.Stop()
    }
}

Push-Location $Root
try {
    Invoke-Step -Name 'system.doctor' -SuccessDetail 'run_doctor.py passed.' -Action {
        python run_doctor.py
    } | Out-Null

    if (-not $SkipSmoke) {
        if ($StrictNetwork) {
            Invoke-Step -Name 'system.smoke' -SuccessDetail 'run_smoke_check.py completed in strict-network mode.' -Action {
                python run_smoke_check.py --strict-network
            } | Out-Null
        }
        else {
            Invoke-Step -Name 'system.smoke' -SuccessDetail 'run_smoke_check.py completed.' -Action {
                python run_smoke_check.py
            } | Out-Null
        }
    }

    if (-not $SkipAlpha) {
        Invoke-Step -Name 'alpha.mainline' -SuccessDetail 'Alpha regression tests passed.' -Action {
            python -m unittest tests.test_alpha_regression tests.test_execution_layer -v
        } | Out-Null
    }

    if (-not $SkipUI) {
        Invoke-UiStep -Name 'ui.check' -SuccessDetail 'UI type check passed.' -Command {
            npm.cmd run check
        } | Out-Null

        Invoke-UiStep -Name 'ui.test' -SuccessDetail 'UI test suite passed.' -Command {
            npm.cmd run test
        } | Out-Null

        $buildOk = Invoke-UiStep -Name 'ui.build' -SuccessDetail 'UI production build succeeded.' -Command {
            npm.cmd run build
        }
        if ($buildOk) {
            Invoke-UiHealthStep | Out-Null
        }
    }
}
finally {
    Pop-Location
}

Write-Output '| Status | Check | Duration | Detail |'
Write-Output '|---|---|---:|---|'
foreach ($item in $Results) {
    $detail = [string]$item.Detail
    $detail = $detail -replace '\|', '/'
    $detail = $detail -replace [Environment]::NewLine, '<br>'
    Write-Output ('| {0} | {1} | {2} | {3} |' -f $item.Status, $item.Check, $item.Duration, $detail)
}
Write-Output ''
$errors = @($Results | Where-Object { $_.Status -eq 'ERR' }).Count
$warnings = @($Results | Where-Object { $_.Status -eq 'WARN' }).Count
$totalDuration = (($Results | Measure-Object -Property DurationSeconds -Sum).Sum)
if ($null -eq $totalDuration) {
    $totalDuration = 0
}
Write-Output ('Summary: errors={0} warnings={1} duration={2:N2}s strict_network={3}' -f $errors, $warnings, $totalDuration, ($(if ($StrictNetwork) { 'on' } else { 'off' })))

if ($errors -gt 0) {
    exit 1
}
exit 0