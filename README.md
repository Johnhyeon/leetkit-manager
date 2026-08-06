# LeetKit Manager

**StockLens · DartLens · TelegramLens 통합 진단·설치·업데이트 도구**

세 Lens를 하나씩 터미널 명령으로 관리하는 대신, 창 하나에서 상태를 보고 고치는
무료 데스크톱 앱입니다. Lens 자체(라이선스 키가 필요한 유료 상품)는 별매이며,
이 도구는 그 설치·진단·업데이트 과정을 도와주는 companion 앱입니다.

## 할 수 있는 것

- **진단** — 세 Lens 모두 설치 여부·버전·MCP 등록 상태·라이선스 상태를 한 화면에서 확인
- **MCP 등록** — Claude Desktop/Claude Code에 자동 등록
- **활성화** — 라이선스 키 입력(창을 벗어나지 않고 붙여넣기)
- **복구** — 각 Lens가 스스로 안전하다고 판단한 문제만 자동으로 고침(라이선스·세션·수집 데이터는 건드리지 않음)
- **설치/업데이트** — PyPI 최신 버전으로 설치, 실패 시 이전 버전으로 롤백 명령 안내
- **지원 문의** — 로그를 안전 목록만 모아 zip으로 만들고, 메일 받는사람/제목/내용을 복사해서 어떤 메일 앱에든 붙여넣기
- **자기 자신 업데이트** — LeetKit Manager 새 버전이 있으면 상단에 업데이트 버튼이 나타남

## 설치

```
uv tool install leetkit-manager
leetkit-manager
```

인자 없이 실행하면 대시보드 창이 뜹니다. 최초 실행 시 바탕화면에 바로가기가
자동으로 만들어져서, 다음부터는 명령어 없이 아이콘 더블클릭으로 켤 수 있습니다.

### 독립 실행 exe (명령어 없이 배포)

컴퓨터·명령줄에 익숙하지 않은 사용자에게는 `uv tool install` 대신 exe 파일
하나를 바로 건넬 수도 있습니다. Python/uv 설치 여부와 무관하게 더블클릭으로
바로 실행됩니다.

```
pip install -e ".[build]"
powershell -File packaging\build_exe.ps1
```

`dist_exe\LeetKitManager.exe` 하나가 나옵니다. 서명되지 않은 exe라 스마트스크린
경고가 뜰 수 있으니, 실제 배포 전에는 코드사이닝 인증서 적용을 검토하세요.

## 요구 사항

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) — Lens 설치·업데이트에 사용
- Windows (현재 1차 지원 플랫폼)

## 라이선스

[LICENSE](LICENSE) 참고 — 별도 키 없이 누구나 설치·실행 가능하지만, 재배포·2차
저작물 제작은 금지됩니다.
