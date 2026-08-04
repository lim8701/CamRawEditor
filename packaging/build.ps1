# Film Rawstery — deterministic onedir packaging.
# Usage:  .\packaging\build.ps1            (release; smoke-tests 10s)
#         .\packaging\build.ps1 -SmokeSeconds 6
#         .\packaging\build.ps1 -NoInstaller   (zip 만, Inno Setup 생략)
# Does: stop running app -> clean dist -> PyInstaller build -> smoke-test the exe
#       from a different directory -> zip -> Inno Setup installer -> print result.
#       Throws on any failure (installer step: Inno 미설치면 경고 후 생략).
param([int]$SmokeSeconds = 10, [switch]$NoInstaller)

$ErrorActionPreference = 'Stop'
$proj = Split-Path -Parent $PSScriptRoot          # packaging/ -> project root
$venvPy = Join-Path $proj '.venv\Scripts\python.exe'
$spec = Join-Path $proj 'FilmRawstery.spec'
$exe  = Join-Path $proj 'dist\FilmRawstery\FilmRawstery.exe'
# zip 은 dist/ 안에 생성(프로젝트 루트 오염 방지, gitignore 동일 적용). [1/5] 클린이 이전 zip 도 제거.
# 파일명에 버전 명시 — main.py APP_VERSION 을 파싱해 자동 동기화(별도 갱신 지점 없음).
$verMatch = Select-String -Path (Join-Path $proj 'main.py') -Pattern 'APP_VERSION = "([^"]+)"'
if (-not $verMatch) { throw "APP_VERSION not found in main.py" }
$ver = $verMatch.Matches[0].Groups[1].Value
$zip  = Join-Path $proj ("dist\FilmRawstery-v{0}-win64.zip" -f $ver)

if (-not (Test-Path $venvPy)) { throw "venv python not found: $venvPy" }
if (-not (Test-Path $spec))   { throw "spec not found: $spec" }

# spec 은 상대경로(luts/shaders/fonts 등)를 쓰므로 호출 위치와 무관하게 항상 프로젝트 루트에서 빌드.
Set-Location $proj

Write-Host "[1/5] stopping any running app + cleaning dist..."
Get-Process FilmRawstery -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
# 개발 중 실행해 둔 `python main.py` 도 함께 종료 — 이 인스턴스가 살아 있으면 single-instance
# named pipe 를 잡고 있어서 [3/5] 스모크 테스트가 exe 를 띄우자마자 '이미 실행 중'으로 종료돼
# 빌드 산출물이 멀쩡해도 항상 실패한다. 창을 먼저 정중히 닫고, 안 닫히면 강제 종료.
$devApp = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
          Where-Object { $_.CommandLine -like '*main.py*' }
foreach ($p in $devApp) {
    $proc = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
    if ($proc) { $null = $proc.CloseMainWindow() }
}
if ($devApp) {
    Start-Sleep -Milliseconds 1200
    foreach ($p in $devApp) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
}
Start-Sleep -Milliseconds 400
Remove-Item -Recurse -Force (Join-Path $proj 'dist') -ErrorAction SilentlyContinue

Write-Host "[2/5] building (PyInstaller)..."
# `-m PyInstaller` (not the console-script wrapper) — robust to venv path quirks.
& $venvPy -m PyInstaller $spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed (exit $LASTEXITCODE)" }
if (-not (Test-Path $exe)) { throw "exe not produced: $exe" }

Write-Host "[3/5] smoke-testing exe from a different directory ($SmokeSeconds s)..."
$err = Join-Path $env:TEMP 'fr_smoke_err.txt'
Remove-Item $err -ErrorAction SilentlyContinue
$p = Start-Process -FilePath $exe -WorkingDirectory $env:TEMP -PassThru -RedirectStandardError $err
Start-Sleep -Seconds $SmokeSeconds
if ($p.HasExited) {
    Write-Host "  SMOKE FAILED — app exited before ${SmokeSeconds}s. stderr:" -ForegroundColor Red
    if (Test-Path $err) { Get-Content $err -Tail 25 }
    throw "Smoke test failed"
}
Stop-Process -Id $p.Id -Force
Start-Sleep -Milliseconds 600

Write-Host "[4/5] zipping..."
Compress-Archive -Path (Join-Path $proj 'dist\FilmRawstery') -DestinationPath $zip -CompressionLevel Optimal -Force
$mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)

# [5/5] Inno Setup 설치 패키지. zip(포터블)과 setup.exe(설치형)를 함께 배포한다.
# ISCC 미설치 머신에서도 zip 빌드는 되도록 경고 후 생략(throw 아님) — 단 있는데 실패하면 throw.
$setup = Join-Path $proj ("dist\FilmRawstery-v{0}-setup.exe" -f $ver)
$setupMsg = $null
if (-not $NoInstaller) {
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
              "${env:ProgramFiles}\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) { $iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source }
    if ($iscc) {
        Write-Host "[5/5] building installer (Inno Setup)..."
        & $iscc /Q ("/DAppVersion=$ver") (Join-Path $proj 'packaging\FilmRawstery.iss')
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed (exit $LASTEXITCODE)" }
        if (-not (Test-Path $setup)) { throw "installer not produced: $setup" }
        $smb = [math]::Round((Get-Item $setup).Length / 1MB, 1)
        $setupMsg = "OK  ->  $setup  ($smb MB)"
    } else {
        Write-Host "[5/5] Inno Setup not found - skipping installer (zip only)." -ForegroundColor Yellow
    }
} else {
    Write-Host "[5/5] -NoInstaller - skipping installer." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "OK  ->  $zip  ($mb MB)" -ForegroundColor Green
if ($setupMsg) { Write-Host $setupMsg -ForegroundColor Green }
