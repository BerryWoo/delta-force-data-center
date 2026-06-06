@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "PORT=18080"
set "PORT_END=18099"
set "DFDC_DEV_LOCAL_ROOT=%ROOT%"
set "DFDC_DEV_LOCAL_PORT=%PORT%"
set "DFDC_DEV_LOCAL_PORT_END=%PORT_END%"

if /I "%~1"=="start" goto start
if /I "%~1"=="stop" goto stop_arg
if /I "%~1"=="restart" goto restart_arg
if /I "%~1"=="status" goto status_arg

:menu
cls
echo Delta KPI dev-local
echo.
echo 1. Start local server (foreground; closing this window stops it)
echo 2. Stop local server
echo 3. Restart local server
echo 4. Show status
echo Q. Quit
echo.
choice /C 1234Q /N /M "Select [1/2/3/4/Q]: "
if errorlevel 5 exit /b 0
if errorlevel 4 goto status_menu
if errorlevel 3 goto restart_menu
if errorlevel 2 goto stop_menu
goto start

:start
call :StopServer quiet
cd /d "%ROOT%"
echo.
echo Starting dev-local...
echo URL: http://127.0.0.1:%PORT%
echo Close this window or press Ctrl+C to stop the local server.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $root=$env:DFDC_DEV_LOCAL_ROOT; $port=[int]$env:DFDC_DEV_LOCAL_PORT; Set-Location -LiteralPath $root; $dataDir=Join-Path $root 'data'; New-Item -ItemType Directory -Force -Path $dataDir | Out-Null; $pidFile=Join-Path $dataDir 'dev-local-server.pid'; $python=(Get-Command python.exe -ErrorAction SilentlyContinue).Source; $args=@(); if($python){ $args=@('-B','-m','src','web','-p',[string]$port) } else { $python=(Get-Command py.exe -ErrorAction SilentlyContinue).Source; if($python){ $args=@('-3','-B','-m','src','web','-p',[string]$port) } }; if(-not $python){ Write-Host 'Python was not found. Install Python or add it to PATH.'; exit 1 }; $p=Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory $root -NoNewWindow -PassThru; $p.Id | Set-Content -LiteralPath $pidFile -Encoding ascii; try { Wait-Process -Id $p.Id } finally { try { if(-not $p.HasExited){ Stop-Process -Id $p.Id -Force } } catch {}; Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue }"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo dev-local exited.
pause
exit /b %EXIT_CODE%

:stop_arg
call :StopServer
exit /b %ERRORLEVEL%

:restart_arg
call :StopServer quiet
goto start

:status_arg
call :ShowStatus
exit /b %ERRORLEVEL%

:stop_menu
call :StopServer
pause
goto menu

:restart_menu
call :StopServer quiet
goto start

:status_menu
call :ShowStatus
pause
goto menu

:StopServer
set "DFDC_QUIET_STOP=0"
if /I "%~1"=="quiet" set "DFDC_QUIET_STOP=1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$root=$env:DFDC_DEV_LOCAL_ROOT; $portStart=[int]$env:DFDC_DEV_LOCAL_PORT; $portEnd=[int]$env:DFDC_DEV_LOCAL_PORT_END; $quiet=$env:DFDC_QUIET_STOP -eq '1'; $pidFile=Join-Path (Join-Path $root 'data') 'dev-local-server.pid'; $ids=@(); if(Test-Path -LiteralPath $pidFile){ $text=Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1; $parsed=0; if([int]::TryParse([string]$text,[ref]$parsed)){ $ids += $parsed } }; foreach($line in (& netstat -ano)){ if($line -match '^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$'){ $port=[int]$Matches[1]; if($port -ge $portStart -and $port -le $portEnd){ $ids += [int]$Matches[2] } } }; $stopped=0; foreach($id in ($ids | Sort-Object -Unique)){ $proc=Get-Process -Id $id -ErrorAction SilentlyContinue; if(-not $proc){ continue }; if($proc.ProcessName -notlike 'python*' -and $proc.ProcessName -notlike 'py*'){ continue }; Stop-Process -Id $id -Force -ErrorAction SilentlyContinue; $stopped++ }; Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue; if(-not $quiet){ if($stopped -gt 0){ Write-Host ('Stopped dev-local processes: ' + $stopped) } else { Write-Host 'No running dev-local process found.' } }"
exit /b %ERRORLEVEL%

:ShowStatus
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$portStart=[int]$env:DFDC_DEV_LOCAL_PORT; $portEnd=[int]$env:DFDC_DEV_LOCAL_PORT_END; $rows=@(); foreach($line in (& netstat -ano)){ if($line -match '^\s*TCP\s+(\S+):(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$'){ $port=[int]$Matches[2]; if($port -ge $portStart -and $port -le $portEnd){ $ownerPid=[int]$Matches[3]; $proc=Get-Process -Id $ownerPid -ErrorAction SilentlyContinue; $rows += [pscustomobject]@{Port=$port; PID=$ownerPid; Process= if($proc){$proc.ProcessName}else{'?'} } } } }; if($rows.Count){ $rows | Sort-Object Port | Format-Table -AutoSize; Write-Host ('URL: http://127.0.0.1:' + (($rows | Sort-Object Port | Select-Object -First 1).Port)) } else { Write-Host 'dev-local is not listening on ports 18080-18099.' }"
exit /b %ERRORLEVEL%
