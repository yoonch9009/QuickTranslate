# QuickTranslate

윈도우 백그라운드에서 실행되며, 텍스트를 선택한 뒤 `Ctrl + C + C`를 누르면 클립보드의 텍스트를 OpenRouter API로 번역해서 팝업으로 보여주는 가벼운 데스크톱 앱입니다.

기본 모델:

- `qwen/qwen3.5-flash-02-23`
- `google/gemma-4-26b-a4b-it`

기본 대상 언어:

- 한국어 (`ko`)

## 기능

- 시스템 트레이 상주
- 글로벌 `Ctrl+C+C` 트리거
- 클립보드 텍스트 자동 번역
- OpenRouter `responses` API 사용
- 모델별 effort 빠른 선택 + reasoning JSON 수동 설정 지원
- OpenRouter 기본 모델 + 폴백 모델 지원
- 기본/폴백 모델 직접 입력 지원
- 텍스트 중심 미니멀 팝업 표시
- 짧은 클립보드 polling 기반 빠른 캡처
- 요청 시작 즉시 로딩 팝업 표시
- 팝업 자동 크기 조절
- 팝업 이동 및 수동 리사이즈 지원
- HTTP 연결 재사용, TTL 캐시, 지연 우선 라우팅 적용
- API Key, 대상 언어, 모델, 팝업 크기 설정 가능

## 설치

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
```

## 실행

콘솔에서 실행:

```powershell
.\.venv\Scripts\Activate.ps1
python -m quicktranslate.app
```

콘솔 없이 실행:

```powershell
.\.venv\Scripts\Activate.ps1
pythonw .\run_quicktranslate.pyw
```

처음 실행하면 설정 창이 열리며 OpenRouter API Key를 입력해야 합니다.
모델 입력칸은 직접 수정할 수 있고, 기본 목록도 실제 모델 ID 그대로 표시됩니다.
각 모델별로 `effort`를 콤보로 빠르게 고를 수 있고, 필요하면 reasoning JSON으로 세부 설정을 덮어쓸 수 있습니다.

## 사용 방법

1. 앱을 실행합니다.
2. 번역할 텍스트를 마우스로 선택합니다.
3. `Ctrl+C`를 빠르게 두 번 누릅니다.
4. 커서 근처에 번역 팝업이 표시됩니다.
5. 팝업은 내용 길이에 맞춰 자동으로 커지거나 줄어들며, 창 가장자리 또는 우하단 그립으로 더 크게 늘릴 수 있습니다.

## 빌드

PyInstaller를 사용하면 단일 실행 파일로 묶을 수 있습니다.

```powershell
.\.venv\Scripts\Activate.ps1
pip install pyinstaller
pyinstaller --noconsole --name QuickTranslate --paths src run_quicktranslate.pyw
```

빌드 결과물은 `dist\QuickTranslate\QuickTranslate.exe`에 생성됩니다.

## 설정 파일

설정은 아래 경로에 저장됩니다.

```text
%APPDATA%\QuickTranslate\settings.json
```

## 참고

- 글로벌 키 입력 감지는 `keyboard` 라이브러리를 사용합니다.
- 일부 관리자 권한 프로그램 위에서는 전역 키 감지가 제한될 수 있습니다.
