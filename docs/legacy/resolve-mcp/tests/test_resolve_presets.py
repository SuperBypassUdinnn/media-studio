"""
Tests for resolve_mcp.presets: LUT management and Fusion macro generation.
"""

from __future__ import annotations

import pytest
from resolve_mcp.presets import (
    PresetError,
    find_lut,
    generate_fusion_macro,
    install_lut,
    list_installed_luts,
    save_macro,
)


class TestListInstalledLuts:
    def test_returns_list_when_dir_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("resolve_mcp.presets._RESOLVE_LUT_DIR", tmp_path / "nonexistent")
        assert list_installed_luts() == []

    def test_finds_cube_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr("resolve_mcp.presets._RESOLVE_LUT_DIR", tmp_path)
        (tmp_path / "test.cube").write_text("LUT 3D SIZE 4\n")
        (tmp_path / "not_a_lut.txt").write_text("")
        result = list_installed_luts()
        assert len(result) == 1
        assert result[0]["name"] == "test"


class TestFindLut:
    def test_returns_none_when_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("resolve_mcp.presets._RESOLVE_LUT_DIR", tmp_path)
        monkeypatch.setattr("resolve_mcp.presets._LOCAL_LUT_SEARCH", [])
        assert find_lut("nonexistent") is None

    def test_finds_partial_match(self, tmp_path, monkeypatch):
        monkeypatch.setattr("resolve_mcp.presets._RESOLVE_LUT_DIR", tmp_path)
        monkeypatch.setattr("resolve_mcp.presets._LOCAL_LUT_SEARCH", [])
        (tmp_path / "neon_cyberpunk.cube").write_text("LUT 3D SIZE 4\n")
        result = find_lut("cyberpunk")
        assert result is not None
        assert result.stem == "neon_cyberpunk"

    def test_case_insensitive(self, tmp_path, monkeypatch):
        monkeypatch.setattr("resolve_mcp.presets._RESOLVE_LUT_DIR", tmp_path)
        monkeypatch.setattr("resolve_mcp.presets._LOCAL_LUT_SEARCH", [])
        (tmp_path / "Cinematic.cube").write_text("LUT 3D SIZE 4\n")
        assert find_lut("CINEMATIC") is not None


class TestInstallLut:
    def test_rejects_missing_file(self, tmp_path):
        with pytest.raises(PresetError, match="not found"):
            install_lut(tmp_path / "ghost.cube")

    def test_rejects_non_cube(self, tmp_path):
        f = tmp_path / "lut.txt"
        f.write_text("")
        with pytest.raises(PresetError, match=r"\.cube"):
            install_lut(f)

    def test_copies_to_resolve_lut_dir(self, tmp_path, monkeypatch):
        src = tmp_path / "my_lut.cube"
        src.write_text("LUT 3D SIZE 4\n")
        dest_dir = tmp_path / "lut_dest"
        monkeypatch.setattr("resolve_mcp.presets._RESOLVE_LUT_DIR", dest_dir)
        result = install_lut(src, subfolder="")
        assert result.exists()
        assert result.name == "my_lut.cube"

    def test_subfolder_created(self, tmp_path, monkeypatch):
        src = tmp_path / "my_lut.cube"
        src.write_text("LUT 3D SIZE 4\n")
        dest_root = tmp_path / "luts"
        monkeypatch.setattr("resolve_mcp.presets._RESOLVE_LUT_DIR", dest_root)
        result = install_lut(src, subfolder="Test")
        assert result.parent.name == "Test"


class TestGenerateFusionMacro:
    def test_generates_valid_setting(self):
        content = generate_fusion_macro(
            name="TestMacro",
            description="A test macro",
            nodes=[
                {
                    "id": "Blur1",
                    "type": "Blur",
                    "params": {"XBlurSize": 3},
                    "pos": [0, 0],
                }
            ],
            inputs=[{"id": "Input", "source": "Blur1.Input"}],
            outputs=[{"id": "Output", "source": "Blur1.Output"}],
        )
        assert "TestMacro" in content
        assert "MacroOperator" in content
        assert "Blur" in content
        assert "XBlurSize" in content

    def test_generates_without_inputs_outputs(self):
        content = generate_fusion_macro(
            name="Simple",
            description="No IOs",
            nodes=[{"id": "CC1", "type": "ColorCorrector", "params": {}}],
        )
        assert "Simple" in content
        assert "MacroOperator" in content


class TestSaveMacro:
    def test_writes_to_macro_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("resolve_mcp.presets._RESOLVE_MACRO_DIR", tmp_path)
        dest = save_macro("TestMacro", "content here", subfolder="")
        assert dest.exists()
        assert dest.read_text() == "content here"

    def test_creates_subfolder(self, tmp_path, monkeypatch):
        monkeypatch.setattr("resolve_mcp.presets._RESOLVE_MACRO_DIR", tmp_path)
        dest = save_macro("TestMacro", "content", subfolder="Sub")
        assert dest.parent.name == "Sub"
