# 독립 실행 exe(더블클릭 실행, uv/명령어 불필요) 빌드.
# 실행: 리포 루트에서 `powershell -File packaging\build_exe.ps1`
#
# --onefile: 파일 하나로 끝나게. --onedir도 시도해봤지만(백신 오탐이 적다는 장점),
# exe 옆의 _internal 폴더를 떼어내면 "지정된 모듈을 찾을 수 없습니다"로 실행이 안 되고
# 그게 사용자가 아주 흔히 하는 행동(바로가기 만들려고 exe만 끌어다 놓기)이라 되돌렸다.
#
# 대신 --onefile은 실행할 때마다 자기를 임시 폴더에 풀고 다시 실행하는데, 그 동작이
# 패커형 악성코드와 구조적으로 닮아 휴리스틱 백신에 오탐될 수 있다 → 구매자 가이드에
# 백신 예외 등록 방법을 함께 제공한다(packaging/ANTIVIRUS_GUIDE.md).
#
# SmartScreen 경고는 포장 방식과 무관하게 "서명이 없어서" 뜬다 — --onedir로 바꿔도
# 안 사라진다. 코드사이닝 인증서(EV면 즉시 해소)로만 근본 해결되므로 매출 보고 판단.
#
# --collect-all webview/clr_loader/pythonnet: pywebview가 WebView2 interop DLL과
# pythonnet의 .NET 런타임 로더를 자기 패키지 데이터 폴더에 담아두는데, PyInstaller의
# 기본 정적 분석으로는 이 비-Python 파일들을 못 찾아서 명시적으로 다 모으라고 지정.
# 이거 빠지면 빌드는 성공해도 실행 시 WebView2/CLR 로드 실패로 조용히 죽는다.

python -m PyInstaller `
  --name LeetKitManager `
  --onefile `
  --windowed `
  --icon leetkit_manager\ui\icon.ico `
  --add-data "leetkit_manager\ui;leetkit_manager\ui" `
  --collect-all webview `
  --collect-all clr_loader `
  --collect-all pythonnet `
  --hidden-import win32timezone `
  --distpath dist_exe `
  --workpath build_exe `
  --noconfirm `
  leetkit_manager\cli.py

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 체크섬까지 여기서 만든다 — 자체 업데이트가 받은 파일을 이 값과 대조하고, 백신에
# 오탐 신고할 때도 해시가 필요하다.
$exe = "dist_exe\LeetKitManager.exe"
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
"$hash  LeetKitManager.exe" | Out-File -FilePath "$exe.sha256" -Encoding ascii -NoNewline

Write-Host ""
Write-Host "빌드 완료" -ForegroundColor Green
Write-Host "  실행 파일 : $exe  (파일 하나로 끝 — 그대로 전달하면 됩니다)"
Write-Host "  SHA256    : $hash"
Write-Host "  백신 오탐 안내: packaging\ANTIVIRUS_GUIDE.md"
