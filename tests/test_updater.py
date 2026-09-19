from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from quicktranslate.app import QuickTranslateApp
from quicktranslate.updater import (
    DOWNLOAD_ROOT,
    UpdateCancelled,
    UpdateTask,
    download_release,
    executable_path,
    launch_replacement,
    parse_release,
    replacement_script,
)


def release_payload(content=b"MZverified executable", tag="v1.8.0"):
    return {
        "tag_name": tag, "draft": False, "prerelease": False,
        "assets": [{
            "name": "QuickTranslate.exe", "state": "uploaded", "size": len(content),
            "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
            "browser_download_url": f"{DOWNLOAD_ROOT}{tag}/QuickTranslate.exe",
        }],
    }


def fake_session(content):
    session = MagicMock()
    response = session.get.return_value.__enter__.return_value
    response.iter_content.return_value = [content[:2], content[2:]]
    return session


def test_versions_use_numeric_order_and_never_downgrade():
    assert parse_release(release_payload(tag="v1.10.0"), "1.9.9") is not None
    assert parse_release(release_payload(), "1.8.0") is None
    assert parse_release(release_payload(), "1.9.0") is None


@pytest.mark.parametrize("change", [
    {"digest": None}, {"digest": "sha256:bad"}, {"size": 0},
    {"size": 9999999999}, {"state": "new"},
    {"browser_download_url": "https://evil.example/QuickTranslate.exe"},
    {"browser_download_url": DOWNLOAD_ROOT + "v1.8.0/other.exe"},
])
def test_untrusted_or_incomplete_assets_are_rejected(change):
    payload = release_payload()
    payload["assets"][0].update(change)
    with pytest.raises(ValueError):
        parse_release(payload, "1.7.1")


def test_prerelease_is_not_installed():
    payload = release_payload()
    payload["prerelease"] = True
    with pytest.raises(ValueError):
        parse_release(payload, "1.7.1")


def test_verified_download_reports_progress(tmp_path):
    content = b"MZverified executable"
    release = parse_release(release_payload(content), "1.7.1")
    progress = Mock()
    destination = tmp_path / "download.exe"
    download_release(fake_session(content), release, destination, Event(), progress)
    assert destination.read_bytes() == content
    progress.assert_called_with(99)


@pytest.mark.parametrize("content", [b"MZcorrupt executable!", b"MZshort", b"MZ" * 100])
def test_corrupt_or_truncated_download_cannot_be_installed(tmp_path, content):
    release = parse_release(release_payload(), "1.7.1")
    with pytest.raises(ValueError):
        download_release(fake_session(content), release, tmp_path / "new.exe", Event(), Mock())


def test_cancel_stops_download(tmp_path):
    cancelled = Event()
    cancelled.set()
    with pytest.raises(UpdateCancelled):
        download_release(
            fake_session(b"MZverified executable"),
            parse_release(release_payload(), "1.7.1"),
            tmp_path / "new.exe", cancelled, Mock(),
        )


def test_worker_removes_failed_download_and_leaves_installed_exe(tmp_path):
    target = tmp_path / "QuickTranslate.exe"
    target.write_bytes(b"existing app")
    task = UpdateTask(target, "1.7.1")
    errors = []
    ready = []
    task.signals.failed.connect(errors.append)
    task.signals.ready.connect(lambda *args: ready.append(args))
    with patch("quicktranslate.updater.requests.Session") as session_class:
        session = session_class.return_value.__enter__.return_value
        response = session.get.return_value.__enter__.return_value
        response.json.return_value = release_payload()
        response.iter_content.return_value = [b"corrupt"]
        task.run()
    assert errors and not ready
    assert list(tmp_path.iterdir()) == [target]
    assert target.read_bytes() == b"existing app"


def test_source_launch_does_not_target_python(tmp_path):
    target = tmp_path / "python.exe"
    target.write_bytes(b"MZ")
    with patch("sys.argv", [str(target)]), pytest.raises(ValueError):
        executable_path()


def test_cancel_after_download_never_quits_or_launches_installer(tmp_path):
    source = tmp_path / "stage" / "QuickTranslate.exe"
    source.parent.mkdir()
    source.write_bytes(b"MZ")
    cancelled = Event()
    cancelled.set()
    app = SimpleNamespace(
        _update_task=SimpleNamespace(cancel_event=cancelled),
        _finish_update=Mock(), quit=Mock(),
    )
    with patch("quicktranslate.app.launch_replacement") as launch:
        QuickTranslateApp._install_update(app, source, "digest")
    launch.assert_not_called()
    app.quit.assert_not_called()
    assert not source.parent.exists()


def test_helper_launch_failure_keeps_app_running(tmp_path):
    source = tmp_path / "stage" / "QuickTranslate.exe"
    source.parent.mkdir()
    source.write_bytes(b"MZ")
    app = SimpleNamespace(
        _update_task=SimpleNamespace(cancel_event=Event(), target=tmp_path / "app.exe"),
        _finish_update=Mock(), quit=Mock(),
    )
    with patch("quicktranslate.app.launch_replacement", side_effect=OSError("access denied")):
        QuickTranslateApp._install_update(app, source, "digest")
    app.quit.assert_not_called()
    app._finish_update.assert_called_once()
    assert not source.parent.exists()


def test_installer_launch_uses_working_windows_console_flags(tmp_path):
    # DETACHED_PROCESS combined with CREATE_NO_WINDOW made PowerShell exit 0
    # without executing the script during the installed-EXE smoke test.
    with patch("quicktranslate.updater.subprocess.Popen") as launch:
        launch_replacement(tmp_path / "app.exe", tmp_path / "new.exe", "0" * 64)
    assert launch.call_args.kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell replacement integration")
@pytest.mark.parametrize("scenario", ["success", "bad_hash", "launch_failure"])
def test_windows_helper_replaces_and_preserves_backup_with_quoted_paths(scenario):
    # A harmless Windows executable verifies actual File.Replace + Start-Process,
    # including spaces, quotes and $ characters without touching the installed app.
    with tempfile.TemporaryDirectory(prefix="qt-update-'$ ") as directory:
        root = Path(directory)
        target = root / "QuickTranslate.exe"
        original = (Path(os.environ["SystemRoot"]) / "System32/hostname.exe").read_bytes()
        target.write_bytes(original)
        stage = root / "stage"
        stage.mkdir()
        source = stage / "QuickTranslate.exe"
        content = (Path(os.environ["SystemRoot"]) / "System32/whoami.exe").read_bytes()
        source.write_bytes(content)
        digest = "0" * 64 if scenario == "bad_hash" else hashlib.sha256(content).hexdigest()
        script = replacement_script(target, source, digest, 2147483647)
        script = script.replace(
            "[System.Windows.Forms.MessageBox]::Show($message, 'QuickTranslate update failed') | Out-Null",
            "throw $message",
        )
        script = script.replace("-WindowStyle Hidden", "-WindowStyle Hidden -Wait")
        if scenario == "launch_failure":
            script = script.replace(
                "    Start-Process -FilePath $target -WorkingDirectory",
                "    throw 'Simulated launch failure'\n    Start-Process -FilePath $target -WorkingDirectory",
            )
        command = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-EncodedCommand", command],
            capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW, check=False,
        )
        if scenario == "success":
            assert result.returncode == 0, result.stderr.decode("cp949", errors="replace")
            assert target.read_bytes() == content
            assert Path(str(target) + ".previous").read_bytes() == original
        else:
            assert result.returncode != 0
            assert target.read_bytes() == original
        assert not stage.exists()
