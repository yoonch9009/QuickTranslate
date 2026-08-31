# QuickTranslate

Windows 백그라운드에서 실행되며, 클립보드의 텍스트나 이미지를 번역해 팝업으로 보여주는 데스크톱 앱입니다.

기본 모델:

- `openrouter/z-ai/glm-5.3-flash`
- `deepseek/deepseek-v4-flash-vision-exp` (폴백)

기본 대상 언어:

- 한국어 (`ko`)

## 기능

- 시스템 트레이 상주
- 글로벌 `Ctrl+C+C` 트리거
- 클립보드 텍스트 자동 번역
- OpenRouter `responses` API 스트리밍 사용
- OpenRouter와 DeepSeek의 이미지 번역 지원
- Codex의 ChatGPT 요금제로 `gpt-5.6-luna` 텍스트·이미지 번역 지원
- OpenRouter 모델 메타데이터에 따른 reasoning 자동 설정
- reasoning 수동 선택 및 사용자 지정 JSON 지원
- 모델·모드별 권장 파라미터 자동 적용 및 OpenRouter 지원 파라미터 필터링
- 기본/폴백 모델별 수동 `temperature`, `top_p`, 추가 파라미터 JSON 설정
- 기본 모델과 폴백 모델의 모델명·reasoning·파라미터 전체 스왑
- 모델 설정 조합을 이름 붙여 저장·불러오기·삭제
- 기본·폴백 프리셋을 연속 저장해도 서로 덮어쓰지 않도록 이름 입력 초기화
- OpenRouter/DeepSeek 기본 모델 + 폴백 모델 지원
- 기본/폴백 모델 직접 입력 지원
- 텍스트 중심 미니멀 팝업 표시
- 짧은 클립보드 polling 기반 빠른 캡처
- 번역되는 내용을 팝업에 실시간 표시
- 팝업에 실제 응답 모델과 폴백 사용 여부 표시
- 팝업 모델명 옆에 요청한 reasoning 수준 표시
- 번역 작성 중에도 `비교` 버튼을 눌러 기본·폴백 번역을 같은 창에서 동시에 2열 비교
- 비교 요청은 설정된 폴백 모델만 한 번 호출하며 텍스트·이미지 번역 모두 지원
- 고정·복사·닫기 버튼의 세로 클릭 영역 확대
- 외부 클릭에도 창을 유지하는 핀 고정 버튼
- 고정된 번역창을 유지한 채 다음 번역을 별도 창으로 표시
- 고정된 번역이 진행 중이어도 새 번역을 별도 창에서 동시 실행
- `상시 고정`이 꺼져 있으면 새 번역창은 고정 해제 상태로 시작
- `상시 고정`을 켜면 현재 창을 즉시 고정하고 이후 새 번역창도 고정 상태로 열며 설정 저장
- 스트리밍 결과에 맞춘 팝업 자동 크기 조절
- 사용자가 번역 중 직접 조절한 창 크기 보존
- 스트리밍 중 사용자가 옮긴 스크롤 위치 보존
- 팝업 자동 크기 조절
- 팝업 이동 및 수동 리사이즈 지원
- HTTP 연결 재사용, TTL 캐시, 지연 우선 라우팅 적용
- API Key, 대상 언어, 모델, 팝업 크기 설정 가능

## 설치

Python 3.14.2가 필요합니다.

```powershell
py -3.14 -m venv .venv314
.\.venv314\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e . pytest ruff nuitka ordered-set zstandard
```

## 실행

콘솔에서 실행:

```powershell
.\.venv314\Scripts\Activate.ps1
python -m quicktranslate.app
```

콘솔 없이 실행:

```powershell
.\.venv314\Scripts\Activate.ps1
pythonw .\QuickTranslate.pyw
```

처음 실행하면 설정 창이 열립니다. OpenRouter와 DeepSeek 직접 호출은 해당 API Key가
필요하지만, `codex/` 모델은 설치된 Codex의 ChatGPT 로그인 상태를 사용합니다.
모델 입력칸은 직접 수정할 수 있고, 기본 목록도 실제 모델 ID 그대로 표시됩니다.
OpenRouter 모델은 `openrouter/<모델 ID>`, DeepSeek 직접 호출 모델은
`deepseek/<모델 ID>`, Codex 요금제 모델은 `codex/<모델 ID>` 형식으로 입력합니다.
예: `codex/gpt-5.6-luna`. Codex Luna의 자동 reasoning은 `max`이며,
temperature와 top_p는 Codex app-server 요청에 보내지 않습니다.
`자동(속도·가격 우선)`은 OpenRouter 모델 메타데이터를 조회하여 reasoning을 끌 수 있으면 명시적으로 끄고, 필수 모델이면 지원되는 가장 낮은 effort를 보냅니다. 필요하면 모델별 effort 또는 reasoning JSON을 직접 지정할 수 있습니다.

파라미터 모드가 `자동(모델 권장값)`이면 알려진 모델과 thinking 모드에 맞는 권장값을 사용하고, OpenRouter가 지원한다고 알린 파라미터만 전송합니다. 예를 들어 Qwen3.8 Flash 비사고 모드에는 `temperature=0.7`, `top_p=0.8`, `top_k=20`, `presence_penalty=1.5`를 적용합니다. 지원 여부를 확인할 수 없는 선택 파라미터는 추측해서 보내지 않습니다.

설정 화면의 전체 스왑 버튼은 기본/폴백의 모델명, reasoning, 파라미터 모드와 값을 함께 바꿉니다. 프로필 영역에서는 각 모델 설정 조합을 이름 붙여 저장하고 어느 슬롯에든 다시 적용할 수 있습니다.

OpenRouter에서 기본 모델이 오류를 반환하면 같은 모델을 재시도하지 않고 즉시 폴백 판단을 수행합니다. 팝업에는 실제로 응답한 모델과 폴백 여부가 표시됩니다.

## 사용 방법

1. 텍스트는 선택한 뒤 `Ctrl+C`를 빠르게 두 번 누릅니다.
2. 이미지는 먼저 클립보드에 복사한 뒤 트레이 메뉴의 `클립보드 번역`을 누릅니다. 이미지가 유지되는 앱에서는 `Ctrl+C+C`도 사용할 수 있습니다.
3. 커서 근처에 번역 팝업이 표시됩니다.
4. 번역 작성 중이거나 완료된 뒤 `비교`를 누르면 같은 원문을 폴백 모델로 번역해 오른쪽 열에서 비교할 수 있습니다.
5. 팝업은 내용 길이에 맞춰 자동으로 커지거나 줄어들며, 창 가장자리 또는 우하단 그립으로 더 크게 늘릴 수 있습니다.

## 빌드

Qt 공식 `pyside6-deploy`가 Nuitka를 사용해 단일 EXE를 만듭니다.

```powershell
.\build.ps1
```

빌드 결과물은 `release\QuickTranslate.exe`에 생성됩니다. 빌드는 Ruff와 pytest를 먼저 통과해야 하며 PyInstaller 또는 복원 DLL을 사용하지 않습니다.

## 설정 파일

설정은 아래 경로에 저장됩니다.

```text
%APPDATA%\QuickTranslate\settings.json
```

## 참고

- 글로벌 키 입력 감지는 `keyboard` 라이브러리를 사용합니다.
- 일부 관리자 권한 프로그램 위에서는 전역 키 감지가 제한될 수 있습니다.
