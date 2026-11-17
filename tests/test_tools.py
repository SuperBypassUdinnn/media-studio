"""Tests for toolchain detection."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from media_studio.tools import (
    CheckResult,
    DoctorReport,
    ToolSpec,
    ToolStatus,
    collect_toolchain,
    deep_checks,
    first_index_of,
    first_line,
    path_order_problem,
)


def test_first_line_skips_blank_lines_and_trims() -> None:
    assert first_line("\n\n  Blender 5.2.2 LTS  \nCopyright") == "Blender 5.2.2 LTS"
    assert first_line("") == ""


def test_path_order_flags_local_bin_before_system_bin() -> None:
    problem = path_order_problem("~/.local/bin:/usr/bin:/bin")

    assert problem is not None
    assert "precedes" in problem


def test_path_order_accepts_system_first() -> None:
    assert path_order_problem("/usr/bin:/home/user/.local/bin:/bin") is None


def test_path_order_accepts_missing_entries() -> None:
    assert path_order_problem("/usr/bin:/bin") is None
    assert path_order_problem("") is None


def test_first_index_of_normalizes_home_and_ignores_absent_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "/home/user")
    entries = ["/usr/bin", "/home/user/.local/bin/", "/bin"]

    assert first_index_of(entries, Path("/usr/bin")) == 0
    assert first_index_of(entries, Path("~/.local/bin")) == 1
    assert first_index_of(entries, Path("/nowhere")) is None


def test_doctor_report_summarises_missing_required_and_warnings() -> None:
    required = ToolSpec("ghost", ("--version",), True, "nothing")
    optional = ToolSpec("fake", ("--version",), False, "testing")
    report = DoctorReport(
        tools=[
            ToolStatus(spec=required, path=None, version=None),
            ToolStatus(spec=optional, path="/usr/bin/fake", version="fake 1.0"),
        ],
        checks=[CheckResult("PATH order", False, "local bin first")],
    )

    assert report.missing_required == ["ghost"]
    assert report.warnings == ["PATH order"]
    payload = report.to_dict()
    assert payload["missing_required"] == ["ghost"]
    assert payload["warnings"] == ["PATH order"]
    assert payload["tools"][0]["required"] is True
    assert payload["checks"] == [{"name": "PATH order", "ok": False, "detail": "local bin first"}]


def test_collect_reports_missing_tool() -> None:
    spec = ToolSpec("ghost", ("--version",), True, "nothing")

    statuses = collect_toolchain(specs=[spec], which=lambda _name: None)

    assert len(statuses) == 1
    assert statuses[0].ok is False
    assert statuses[0].version is None


def test_collect_reads_version_from_stdout() -> None:
    spec = ToolSpec("fake", ("-version",), True, "testing")

    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["fake"], returncode=0, stdout="fake 1.2.3\nbuild meta\n", stderr=""
        )

    statuses = collect_toolchain(specs=[spec], which=lambda _name: "/usr/bin/fake", run=run)

    assert statuses[0].ok is True
    assert statuses[0].version == "fake 1.2.3"


def test_collect_falls_back_to_stderr_and_survives_failure() -> None:
    spec = ToolSpec("fake", ("-version",), True, "testing")

    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("boom")

    statuses = collect_toolchain(specs=[spec], which=lambda _name: "/usr/bin/fake", run=run)

    assert statuses[0].ok is True
    assert statuses[0].version is None


def test_deep_checks_reports_missing_venv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import media_studio.tools as tools_module

    monkeypatch.setattr(tools_module, "NVIDIA_VULKAN_ICD", tmp_path / "missing.json")
    monkeypatch.setattr(tools_module, "TORCH_VENVS", (("demo", tmp_path / "venv"),))

    results = deep_checks()

    by_name = {result.name: result for result in results}
    assert by_name["Vulkan NVIDIA ICD"].ok is False
    assert by_name["torch demo"].ok is False
    assert any(result.name.startswith("disk free") for result in results)


def test_deep_checks_reports_usable_venv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import media_studio.tools as tools_module

    icd = tmp_path / "nvidia_icd.json"
    icd.write_text("{}", encoding="utf-8")
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("", encoding="utf-8")
    monkeypatch.setattr(tools_module, "NVIDIA_VULKAN_ICD", icd)
    monkeypatch.setattr(tools_module, "TORCH_VENVS", (("demo", venv),))

    results = deep_checks(python_probe=lambda _venv: "2.12.0+cu130")

    by_name = {result.name: result for result in results}
    assert by_name["Vulkan NVIDIA ICD"].ok is True
    assert by_name["torch demo"] == CheckResult("torch demo", True, "2.12.0+cu130")


@pytest.mark.skipif(
    not Path("~/.config/opencode/media-engines.json").expanduser().is_file(),
    reason="media-engines.json not present on host",
)
def test_deep_checks_reports_engines_from_config() -> None:
    results = deep_checks()
    by_name = {result.name: result for result in results}
    assert "engine filmcraft" in by_name
    assert by_name["engine filmcraft"].ok is True
    assert "engine effectcraft" in by_name
    assert by_name["engine effectcraft"].ok is True


def test_deep_checks_reports_edge_tts_httpx_google_genai_optional() -> None:
    """Generator/render optional deps appear as checks in deep_checks output."""

    results = deep_checks()
    by_name = {result.name: result for result in results}
    for expected in ("edge-tts", "httpx", "google-genai", "playwright"):
        matches = [name for name in by_name if expected.lower() in name.lower()]
        assert matches, f"missing optional check containing {expected!r}"
        check = by_name[matches[0]]
        if not check.ok:
            assert "not installed" in check.detail.lower()


def test_deep_checks_reports_torch_venvs_as_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing DeepFilterNet/UVR venvs report as optional, not fatal FAIL."""

    import media_studio.tools as tools_module

    monkeypatch.setattr(
        tools_module,
        "TORCH_VENVS",
        (
            ("DeepFilterNet", tools_module.Path.home() / ".clone" / "DeepFilterNet" / ".venv"),
            (
                "demucs (UVR)",
                tools_module.Path.home() / ".clone" / "ultimatevocalremovergui" / ".venv",
            ),
        ),
    )
    monkeypatch.setattr(tools_module, "NVIDIA_VULKAN_ICD", tools_module.Path("/missing/icd.json"))

    results = deep_checks()
    by_name = {result.name: result for result in results}
    for expected in ("torch DeepFilterNet", "torch demucs (UVR)"):
        assert expected in by_name, f"missing optional check {expected!r}"
        check = by_name[expected]
        assert check.ok is False
        assert "optional" in check.detail.lower() or "missing venv" in check.detail.lower()


def test_check_effectcraft_update_detects_newer_version(tmp_path: Path) -> None:
    from media_studio.tools import check_effectcraft_update

    cache = tmp_path / "ec-update.json"
    result = check_effectcraft_update(
        installed_version="0.19.0",
        cache_path=cache,
        force=True,
        opener=lambda: "0.24.1",
    )
    assert result["update_available"] is True
    assert result["installed"] == "0.19.0"
    assert result["latest"] == "0.24.1"
    assert result["error"] is None

    # Cached read should return the same
    cached_result = check_effectcraft_update(
        installed_version="0.19.0",
        cache_path=cache,
        force=False,
    )
    assert cached_result["update_available"] is True
    assert cached_result["latest"] == "0.24.1"


def test_check_effectcraft_update_up_to_date(tmp_path: Path) -> None:
    from media_studio.tools import check_effectcraft_update

    cache = tmp_path / "ec-update.json"
    result = check_effectcraft_update(
        installed_version="0.24.1",
        cache_path=cache,
        force=True,
        opener=lambda: "0.24.1",
    )
    assert result["update_available"] is False
    assert result["installed"] == "0.24.1"


def test_check_effectcraft_update_handles_opener_error(tmp_path: Path) -> None:
    from media_studio.tools import check_effectcraft_update

    def broken_opener() -> str:
        raise OSError("network down")

    cache = tmp_path / "ec-update.json"
    result = check_effectcraft_update(
        installed_version="0.24.1",
        cache_path=cache,
        force=True,
        opener=broken_opener,
    )
    assert result["update_available"] is False
    assert result["error"] == "network down"
