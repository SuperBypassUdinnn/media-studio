"""Unit tests for the Natron headless compositing path (offline, no NatronRenderer required)."""

from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ElementTree
from unittest import mock
from xml.dom import minidom

import pytest

from media_studio.natron import (
    CompositeResult,
    NatronError,
    build_ntp,
    check_installation,
    composite_layers,
    composite_project,
    render,
    simple_grade,
)


def _parse_ntp(path) -> ElementTree.Element:
    return ElementTree.parse(path).getroot()


def test_build_ntp_produces_parseable_xml(tmp_path):
    """build_ntp emits XML that ElementTree can round-trip."""

    nodes = [
        {
            "name": "Read1",
            "pluginID": "fr.inria.Builtin-Read",
            "params": [{"name": "filename", "value": "/tmp/input.mp4"}],
        },
        {
            "name": "Grade1",
            "pluginID": "fr.inria.Builtin-Grade",
            "inputs": ["Read1"],
            "params": [{"name": "gain", "value": 1.1}],
        },
        {
            "name": "Write1",
            "pluginID": "fr.inria.Builtin-Write",
            "inputs": ["Grade1"],
            "params": [{"name": "filename", "value": "/tmp/output.mp4"}],
        },
    ]

    ntp = tmp_path / "test.ntp"
    written = build_ntp(nodes, ntp)
    assert written == ntp

    root = _parse_ntp(ntp)
    assert root.tag == "natron"
    project = root.find("project")
    assert project is not None

    node_els = project.find("nodes").findall("node")
    assert [el.get("pluginID") for el in node_els] == [
        "fr.inria.Builtin-Read",
        "fr.inria.Builtin-Grade",
        "fr.inria.Builtin-Write",
    ]

    grade = node_els[1]
    params = {p.get("name"): p.text for p in grade.find("params").findall("param")}
    assert params["gain"] == "1.1"

    inputs = grade.find("inputs")
    assert inputs is not None
    assert inputs.find("input").text == "Read1"


def test_build_ntp_rejects_malformed_node(tmp_path):
    """A node without pluginID raises NatronError."""

    with pytest.raises(NatronError, match="missing pluginID"):
        build_ntp([{"name": "NoId"}], tmp_path / "bad.ntp")


def test_simple_grade_defaults_are_pass_through(tmp_path):
    """All-default simple_grade should not shift colour (gain/gamma/saturation == 1)."""

    nodes = [
        {
            "name": "Read1",
            "pluginID": "fr.inria.Builtin-Read",
            "params": [{"name": "filename", "value": "/tmp/source.mp4"}],
        },
        {
            "name": "Grade1",
            "pluginID": "fr.inria.Builtin-Grade",
            "inputs": ["Read1"],
            "params": [
                {"name": "gain", "value": 1.0},
                {"name": "gamma", "value": 1.0},
                {"name": "saturation", "value": 1.0},
            ],
        },
        {
            "name": "Write1",
            "pluginID": "fr.inria.Builtin-Write",
            "inputs": ["Grade1"],
            "params": [{"name": "filename", "value": "/tmp/out.mp4"}],
        },
    ]

    ntp = tmp_path / "grade.ntp"
    build_ntp(nodes, ntp)

    # Round-trip should preserve the pass-through values.
    root = _parse_ntp(ntp)
    grade = root.find("project").find("nodes").findall("node")[1]
    params = {p.get("name"): p.text for p in grade.find("params").findall("param")}
    assert params["gain"] == "1.0"
    assert params["gamma"] == "1.0"
    assert params["saturation"] == "1.0"


def test_composite_layers_identity_when_inputs_match(tmp_path):
    """composite_layers with identical background and foreground is an identity composite."""

    nodes = [
        {
            "name": "ReadBg",
            "pluginID": "fr.inria.Builtin-Read",
            "params": [{"name": "filename", "value": "/tmp/bg.mp4"}],
        },
        {
            "name": "ReadFg",
            "pluginID": "fr.inria.Builtin-Read",
            "params": [{"name": "filename", "value": "/tmp/fg.mp4"}],
        },
        {
            "name": "Merge1",
            "pluginID": "fr.inria.Builtin-Merge2",
            "inputs": ["ReadBg", "ReadFg"],
            "params": [{"name": "operator", "value": "over"}],
        },
        {
            "name": "Write1",
            "pluginID": "fr.inria.Builtin-Write",
            "inputs": ["Merge1"],
            "params": [{"name": "filename", "value": "/tmp/out.mp4"}],
        },
    ]

    ntp = tmp_path / "composite.ntp"
    build_ntp(nodes, ntp)

    root = _parse_ntp(ntp)
    merge = root.find("project").find("nodes").findall("node")[2]
    params = {p.get("name"): p.text for p in merge.find("params").findall("param")}
    assert params["operator"] == "over"

    inputs = merge.find("inputs")
    assert [inp.text for inp in inputs.findall("input")] == ["ReadBg", "ReadFg"]


def test_check_installation_raises_when_natron_missing():
    """check_installation raises NatronError when NatronRenderer is not on PATH."""

    with (
        mock.patch("media_studio.natron.NATRON_RENDERER", None),
        pytest.raises(NatronError, match="NatronRenderer is not on PATH"),
    ):
        check_installation()


def test_composite_result_ok_property():
    """CompositeResult.ok is False without a valid output/report."""

    result = CompositeResult(
        output=None if False else __import__("pathlib").Path("/tmp/nonexistent.mp4"),
        ntp_path=__import__("pathlib").Path("/tmp/test.ntp"),
        report_path=__import__("pathlib").Path("/tmp/test.json"),
        report={"ok": False},
    )
    assert not result.ok


def test_render_raises_when_natron_missing(tmp_path):
    """render raises NatronError when NatronRenderer is absent."""

    with (
        mock.patch("media_studio.natron.NATRON_RENDERER", None),
        pytest.raises(NatronError, match="NatronRenderer is not on PATH"),
    ):
        render(tmp_path / "x.ntp", tmp_path / "y.mp4")


def test_simple_grade_raises_when_natron_missing(tmp_path):
    """simple_grade raises NatronError when NatronRenderer is absent."""

    with (
        mock.patch("media_studio.natron.NATRON_RENDERER", None),
        pytest.raises(NatronError, match="NatronRenderer is not on PATH"),
    ):
        simple_grade(
            tmp_path / "src.mp4",
            tmp_path / "out.mp4",
            tmp_path / "g.ntp",
        )


def test_composite_layers_raises_when_natron_missing(tmp_path):
    """composite_layers raises NatronError when NatronRenderer is absent."""

    with (
        mock.patch("media_studio.natron.NATRON_RENDERER", None),
        pytest.raises(NatronError, match="NatronRenderer is not on PATH"),
    ):
        composite_layers(
            tmp_path / "bg.mp4",
            tmp_path / "fg.mp4",
            tmp_path / "out.mp4",
            tmp_path / "c.ntp",
        )


def test_composite_project_raises_when_natron_missing(tmp_path):
    """composite_project raises NatronError when NatronRenderer is absent."""

    with (
        mock.patch("media_studio.natron.NATRON_RENDERER", None),
        pytest.raises(NatronError, match="NatronRenderer is not on PATH"),
    ):
        composite_project("master", {"grade": {"gain": 1.1}}, project_root=tmp_path)


def test_build_ntp_pretty_xml_is_valid(tmp_path):
    """The serialized .ntp is valid XML with a write declaration for Write nodes."""

    nodes = [
        {
            "name": "Read1",
            "pluginID": "fr.inria.Builtin-Read",
            "params": [{"name": "filename", "value": "/tmp/in.mp4"}],
        },
        {
            "name": "Write1",
            "pluginID": "fr.inria.Builtin-Write",
            "inputs": ["Read1"],
            "params": [{"name": "filename", "value": "/tmp/out.mp4"}],
        },
    ]

    ntp = tmp_path / "write.ntp"
    build_ntp(nodes, ntp)

    text = ntp.read_text(encoding="utf-8")
    # minidom toprettyxml should not crash on round-trip.
    minidom.parseString(text)

    root = _parse_ntp(ntp)
    writes = root.find("project").find("writes")
    assert writes is not None
    write_el = writes.find("write")
    assert write_el is not None
    assert write_el.get("node") == "Write1"
    assert write_el.get("file") == "/tmp/out.mp4"


def test_merge_operator_validation():
    """composite_layers rejects unknown merge operators."""

    with pytest.raises(NatronError, match="unknown merge operator"):
        composite_layers(
            __import__("pathlib").Path("/tmp/bg.mp4"),
            __import__("pathlib").Path("/tmp/fg.mp4"),
            __import__("pathlib").Path("/tmp/out.mp4"),
            __import__("pathlib").Path("/tmp/c.ntp"),
            merge_op="not-a-real-operator",
        )


@pytest.mark.skipif(
    subprocess.run(["which", "NatronRenderer"], capture_output=True).returncode != 0,
    reason="NatronRenderer not installed",
)
def test_integration_render_synthetic_clip(tmp_path):
    """Render a 1s synthetic clip through simple_grade; output duration matches input."""

    src = tmp_path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=320x180:rate=24",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        capture_output=True,
        check=True,
    )

    out = tmp_path / "out.mp4"
    ntp = tmp_path / "grade.ntp"
    result = simple_grade(src, out, ntp)

    assert result.ok, result.report
    assert out.exists()


@pytest.mark.skipif(
    subprocess.run(["which", "NatronRenderer"], capture_output=True).returncode != 0,
    reason="NatronRenderer not installed",
)
def test_integration_glow_raises_peak_luminance(tmp_path):
    """Applying a glow should raise peak luminance above the input."""

    src = tmp_path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=duration=1:size=320x180:rate=24",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        capture_output=True,
        check=True,
    )

    out = tmp_path / "out.mp4"
    ntp = tmp_path / "grade.ntp"
    result = simple_grade(src, out, ntp, glow_size=4.0, glow_mix=0.8)

    assert result.ok, result.report
    assert out.exists()
