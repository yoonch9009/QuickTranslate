# QuickTranslate

Windows 백그라운드에서 실행되며, 클립보드의 텍스트나 이미지를 AI 모델로 번역해
가벼운 팝업으로 보여주는 데스크톱 앱입니다.

[![Latest release](https://img.shields.io/github/v/release/yoonch9009/QuickTranslate)](https://github.com/yoonch9009/QuickTranslate/releases/latest)
[![Windows](https://img.shields.io/badge/platform-Windows-0078D4)](https://github.com/yoonch9009/QuickTranslate/releases/latest)
[![License](https://img.shields.io/github/license/yoonch9009/QuickTranslate)](LICENSE)

## 다운로드

[최신 Windows EXE 다운로드](https://github.com/yoonch9009/QuickTranslate/releases/latest)

별도 Python 설치 없이 릴리즈의 `QuickTranslate.exe`를 실행하면 됩니다. 현재 릴리즈는
`v1.7.1`이며, Windows 64비트용 단일 실행 파일입니다.

## 기본 구성

| 용도 | 기본 모델 | 제공자 |
| --- | --- | --- |
| 기본 번역 | `openrouter/z-ai/glm-5.3-flash` | OpenRouter |
| 폴백 번역 | `deepseek/deepseek-v4-flash-vision-exp` | DeepSeek |

모델 ID 앞에 다음 접두사를 붙여 제공자를 선택합니다.

- `openrouter/<모델 ID>`: OpenRouter API
- `deepseek/<모델 ID>`: DeepSeek API 직접 호출
- `codex/<모델 ID>`: 설치된 Codex의 ChatGPT 로그인 사용

예: `openrouter/qwen/qwen3.8-flash`, `codex/gpt-5.6-luna`

## 주요 기능

- 시스템 트레이 상주 및 글로벌 `Ctrl+C+C` 번역
- 클립보드 텍스트와 이미지 OCR 번역
- OpenRouter, DeepSeek, ChatGPT 구독 Codex 모델 지원
- 스트리밍 결과 실시간 표시
- 기본 모델 실패 시 같은 모델을 재시도하지 않고 설정된 폴백 모델 사용
- 번역 작성 중에도 기본·폴백 번역을 2열로 비교
- 모델별 reasoning, `temperature`, `top_p`, 추가 파라미터 설정
- OpenRouter 모델 메타데이터 기반 reasoning 및 지원 파라미터 자동 판단
- 기본·폴백 모델명과 모든 파라미터를 한 번에 교환
- 모델 설정 조합을 프리셋으로 저장·불러오기·삭제
- 결과 길이에 맞춘 팝업 자동 크기 조절과 사용자 크기 보존
- 스트리밍 중 사용자가 옮긴 스크롤 위치 보존
- 팝업 이동, 수동 크기 조절, 복사 및 닫기
- 개별 고정과 이후 새 창까지 적용되는 상시 고정
- 고정된 번역창을 유지하면서 여러 번역을 별도 창에서 동시 실행

## 사용 방법

1. 처음 실행한 뒤 트레이 메뉴의 `설정`에서 사용할 제공자의 API 키와 모델을 지정합니다.
2. 텍스트를 선택하고 `Ctrl+C`를 빠르게 두 번 누릅니다.
3. 이미지는 클립보드에 복사한 뒤 트레이 메뉴의 `클립보드 번역`을 누릅니다. 이미지가 유지되는 앱에서는 `Ctrl+C+C`도 사용할 수 있습니다.
4. 번역 팝업이 커서 근처에 나타나고 결과가 실시간으로 표시됩니다.

팝업 상단에서는 다음 기능을 사용할 수 있습니다.

- `비교`: 같은 원문을 폴백 모델로 한 번 번역해 오른쪽 열에 표시
- `상시`: 현재 창을 고정하고 이후 새 번역창도 고정 상태로 생성
- `고정`: 현재 창만 외부 클릭으로 닫히지 않도록 유지
- `복사`: 현재 번역 또는 비교 결과 복사
- `닫기`: 현재 팝업 닫기

팝업을 직접 크게 조절하면 번역이 완성되어도 사용자가 정한 크기를 유지합니다. 팝업이
열리자마자 드래그해도 이동 중 자동 닫기나 스트리밍 자동 크기 조절과 충돌하지 않습니다.

## 모델 및 파라미터 설정

`자동(속도·가격 우선)` reasoning 모드는 OpenRouter 모델 메타데이터를 확인해 reasoning을
끌 수 있으면 명시적으로 끄고, 필수인 모델이면 지원되는 가장 낮은 effort를 보냅니다.
필요하면 `none`, `low`, `max` 등의 effort 또는 reasoning JSON을 직접 지정할 수 있습니다.

파라미터 모드가 `자동(모델 권장값)`이면 알려진 모델과 thinking 모드에 맞는 권장값을
사용하고, OpenRouter가 지원한다고 알린 파라미터만 전송합니다. 예를 들어 Qwen3.8 Flash
비사고 모드에는 `temperature=0.7`, `top_p=0.8`, `top_k=20`,
`presence_penalty=1.5`를 적용합니다. 지원 여부를 확인할 수 없는 선택 파라미터는 보내지
않습니다.

`codex/gpt-5.6-luna`는 별도 OpenAI API 키 대신 설치된 Codex의 ChatGPT 로그인 상태를
사용합니다. 자동 reasoning은 `max`이며 `temperature`와 `top_p`는 Codex app-server
요청에 보내지 않습니다.

## 소스에서 실행

개발에는 Python 3.14.2가 필요합니다.

```powershell
py -3.14 -m venv .venv314
.\.venv314\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e . pytest ruff nuitka ordered-set zstandard
python -m quicktranslate.app
```

콘솔 없이 실행하려면 다음 명령을 사용합니다.

```powershell
pythonw .\QuickTranslate.pyw
```

## 빌드

Qt 공식 `pyside6-deploy`와 Nuitka를 사용해 단일 EXE를 만듭니다. PyInstaller 또는 복원된
Qt DLL은 사용하지 않습니다.

```powershell
.\build.ps1
```

빌드 전에 Ruff와 전체 pytest 검사가 실행되며, 결과물은
`release\QuickTranslate.exe`에 생성됩니다.

## 설정 및 개인정보

설정과 로그는 로컬의 다음 폴더에 저장되며 Git 저장소에는 포함되지 않습니다.

```text
%APPDATA%\QuickTranslate\
```

OpenRouter와 DeepSeek 직접 호출에는 해당 API 키가 필요합니다. API 키는 사용자 설정
파일에만 저장되므로 설정 파일과 로그를 외부에 공유하지 않는 것을 권장합니다.

## 참고

- 글로벌 키 입력 감지는 `keyboard` 라이브러리를 사용합니다.
- 일부 관리자 권한 프로그램 위에서는 전역 키 감지가 제한될 수 있습니다.
- 지원 모델과 파라미터는 제공자의 API 지원 범위에 따라 달라질 수 있습니다.
