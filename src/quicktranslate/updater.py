from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event

import requests
from PySide6.QtCore import QObject, QRunnable, Signal, Slot

RELEASE_API = "https://api.github.com/repos/yoonch9009/QuickTranslate/releases/latest"
DOWNLOAD_ROOT = "https://github.com/yoonch9009/QuickTranslate/releases/download/"
MAX_DOWNLOAD_SIZE = 250 * 1024 * 1024


def version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise ValueError(f"지원하지 않는 버전 형식: {value}")
    return tuple(int(part) for part in match.groups())


def executable_path() -> Path:
    # Nuitka onefile's argv[0] points to the original EXE, not its extraction folder.
    target = Path(sys.argv[0]).resolve()
    if target.suffix.lower() != ".exe" or not target.is_file():
        raise ValueError("EXE 실행본에서 사용할 수 있습니다. 소스 실행은 Git으로 업데이트하세요.")
    if target.name.lower() in {"python.exe", "pythonw.exe"}:
        raise ValueError("Python 실행 파일은 자동 업데이트할 수 없습니다.")
    return target


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    size: int
    sha256: str


def parse_release(payload: dict, current: str) -> Release | None:
    if payload.get("draft") or payload.get("prerelease"):
        raise ValueError("정식 릴리즈가 아닙니다.")
    tag = payload["tag_name"]
    if version_tuple(tag) <= version_tuple(current):
        return None
    asset = next((a for a in payload.get("assets", []) if a["name"] == "QuickTranslate.exe"), None)
    if not asset or asset.get("state") != "uploaded":
        raise ValueError("최신 릴리즈의 QuickTranslate.exe가 아직 준비되지 않았습니다.")
    digest = asset.get("digest") or ""
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise ValueError("릴리즈의 SHA-256 정보가 없어 안전하게 업데이트할 수 없습니다.")
    url = asset["browser_download_url"]
    if url != f"{DOWNLOAD_ROOT}{tag}/QuickTranslate.exe":
        raise ValueError("공식 저장소의 다운로드 주소가 아닙니다.")
    size = asset["size"]
    if not isinstance(size, int) or not 0 < size <= MAX_DOWNLOAD_SIZE:
        raise ValueError("릴리즈 파일 크기가 올바르지 않습니다.")
    return Release(tag, url, size, digest[7:].lower())


class UpdateCancelled(Exception):
    pass


def download_release(
    session: requests.Session,
    release: Release,
    destination: Path,
    cancelled: Event,
    progress: Callable[[int], None],
) -> None:
    digest = hashlib.sha256()
    total = 0
    with session.get(release.url, stream=True, timeout=(10, 30)) as response:
        response.raise_for_status()
        with destination.open("xb") as output:
            for chunk in response.iter_content(128 * 1024):
                if cancelled.is_set():
                    raise UpdateCancelled()
                total += len(chunk)
                if total > release.size:
                    raise ValueError("다운로드 크기가 릴리즈 정보와 다릅니다.")
                output.write(chunk)
                digest.update(chunk)
                progress(min(99, total * 100 // release.size))
    if cancelled.is_set():
        raise UpdateCancelled()
    if total != release.size or digest.hexdigest() != release.sha256:
        raise ValueError("다운로드 검증에 실패했습니다. 기존 프로그램은 변경되지 않았습니다.")
    with destination.open("rb") as downloaded:
        if downloaded.read(2) != b"MZ":
            raise ValueError("다운로드한 파일이 Windows 실행 파일이 아닙니다.")


def ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def replacement_script(target: Path, source: Path, digest: str, process_id: int) -> str:
    # All paths are quoted as PowerShell literals, including quotes/$ in folder names.
    return f"""$ErrorActionPreference = 'Stop'
$env:PSModulePath = Join-Path $PSHOME 'Modules'
$target = {ps_literal(str(target))}
$source = {ps_literal(str(source))}
$backup = $target + '.previous'
$stage = [IO.Path]::GetDirectoryName($source)
$replaced = $false
try {{
    if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne {ps_literal(digest)}) {{
        throw 'Downloaded file checksum mismatch.'
    }}
    $deadline = [DateTime]::UtcNow.AddSeconds(120)
    while (Get-Process -Id {process_id} -ErrorAction SilentlyContinue) {{
        if ([DateTime]::UtcNow -gt $deadline) {{ throw 'Application did not exit in time.' }}
        Start-Sleep -Milliseconds 250
    }}
    # The Nuitka onefile parent can still hold the EXE briefly after the app exits.
    while (-not $replaced) {{
        try {{
            [IO.File]::Replace($source, $target, $backup, $true)
            $replaced = $true
        }} catch {{
            if ([DateTime]::UtcNow -gt $deadline) {{ throw }}
            Start-Sleep -Milliseconds 250
        }}
    }}
    Start-Process -FilePath $target -WorkingDirectory ([IO.Path]::GetDirectoryName($target)) -WindowStyle Hidden
}} catch {{
    $message = $_.Exception.Message
    if ($replaced -and [IO.File]::Exists($backup)) {{
        try {{
            [IO.File]::Replace($backup, $target, $source, $true)
            Start-Process -FilePath $target -WindowStyle Hidden
        }} catch {{ $message += " Recovery failed: " + $_.Exception.Message }}
    }}
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($message, 'QuickTranslate update failed') | Out-Null
}} finally {{
    if ([IO.File]::Exists($source)) {{ [IO.File]::Delete($source) }}
    if ([IO.Directory]::Exists($stage)) {{ [IO.Directory]::Delete($stage, $false) }}
}}
"""


def launch_replacement(target: Path, source: Path, digest: str) -> None:
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    encoded = base64.b64encode(
        replacement_script(target, source, digest, os.getpid()).encode("utf-16le")
    ).decode("ascii")
    subprocess.Popen(
        [str(powershell), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
         "-EncodedCommand", encoded],
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True,
    )


class UpdateSignals(QObject):
    progress = Signal(int, str)
    ready = Signal(object, str)
    current = Signal()
    failed = Signal(str)
    cancelled = Signal()


class UpdateTask(QRunnable):
    def __init__(self, target: Path, current: str) -> None:
        super().__init__()
        self.target = target
        self.current_version = current
        self.cancel_event = Event()
        self.signals = UpdateSignals()
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        stage = None
        try:
            with requests.Session() as session:
                session.headers["User-Agent"] = f"QuickTranslate/{self.current_version}"
                with session.get(RELEASE_API, timeout=(10, 20)) as response:
                    response.raise_for_status()
                    release = parse_release(response.json(), self.current_version)
                if self.cancel_event.is_set():
                    raise UpdateCancelled()
                if release is None:
                    self.signals.current.emit()
                    return
                # Same volume is required for atomic replacement; this also checks write access.
                stage = Path(tempfile.mkdtemp(prefix=".quicktranslate-update-", dir=self.target.parent))
                source = stage / "QuickTranslate.exe"
                download_release(
                    session, release, source, self.cancel_event,
                    lambda percent: self.signals.progress.emit(
                        percent, f"{release.version} 다운로드 중… {percent}%"
                    ),
                )
                self.signals.ready.emit(source, release.sha256)
                stage = None  # Ownership transfers to the GUI / replacement helper.
        except UpdateCancelled:
            self.signals.cancelled.emit()
        except Exception as exc:  # noqa: BLE001 - report worker errors to the UI
            self.signals.failed.emit(f"업데이트할 수 없습니다: {exc}")
        finally:
            if stage is not None:
                shutil.rmtree(stage, ignore_errors=True)
