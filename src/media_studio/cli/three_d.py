"""3D model inspection, conversion, material binding, and rendering (`ms 3d`) CLI commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from media_studio.blender import (
    EXPORT_FORMATS,
    BlenderError,
    modify_model,
    project_output,
    turntable,
    write_report,
)
from media_studio.blender import convert as convert_model
from media_studio.blender import inspect as inspect_model
from media_studio.manifest import ManifestError, Project, load_project
from media_studio.materials import (
    MaterialError,
    bind,
    fetch_hdri,
    fetch_material,
    proof_frame_luminance,
)
from media_studio.triposr import TripoSRError, image_to_mesh_project

from .common import EXIT_FAILURE, EXIT_OK, resolve_monkeypatchable


def register_three_d_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``3d`` command group."""

    three_d = subparsers.add_parser("3d", help="inspect, convert, and render models")
    three_d_sub = three_d.add_subparsers(dest="three_d_command", required=True)

    inspect = three_d_sub.add_parser("inspect", help="report a model's geometry")
    inspect.add_argument("model", type=Path)
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(func=cmd_3d_inspect)

    convert = three_d_sub.add_parser("convert", help="repair and re-export a model")
    convert.add_argument("model", type=Path)
    convert.add_argument("--to", required=True, choices=sorted(EXPORT_FORMATS))
    convert.add_argument("--out", type=Path, default=None, help="output directory")
    convert.add_argument("--decimate", type=float, default=0.0, help="ratio below 1.0 to simplify")
    convert.set_defaults(func=cmd_3d_convert)

    material = three_d_sub.add_parser("material", help="fetch and bind a PBR material")
    material.add_argument("model", type=Path)
    material.add_argument("--project", required=True, help="project to stage the maps into")
    material.add_argument("--material", required=True, help="asset id, for example mossy_rock")
    material.add_argument("--hdri", default=None, help="HDRI asset id for the world lighting")
    material.add_argument("--resolution", default="1k", help="texture resolution, default 1k")
    material.add_argument("--out", type=Path, default=None, help="where to write the bound model")
    material.add_argument("--render", action="store_true", help="also render a proof frame")
    material.add_argument("--proof-resolution", default="640x360")
    material.set_defaults(func=cmd_3d_material)

    spin = three_d_sub.add_parser("turntable", help="render a turntable headlessly")
    spin.add_argument("model", type=Path)
    spin.add_argument("--project", default=None, help="project to write outputs and reports into")
    spin.add_argument("--frames", type=int, default=24)
    spin.add_argument("--resolution", default="640x360", help="WxH, default 640x360")
    spin.add_argument("--baseline", type=Path, default=None, help="a previous turntable-qc.json")
    spin.set_defaults(func=cmd_3d_turntable)

    modify = three_d_sub.add_parser("modify", help="procedurally modify a model headlessly")
    modify.add_argument("model", type=Path, help="model file to modify")
    modify.add_argument("--project", required=True, help="project for outputs")
    modify.add_argument(
        "--ops", type=Path, default=None, help="YAML/JSON file with operations list"
    )
    modify.add_argument("--mirror", choices=("x", "y", "z"), default=None, help="mirror axis")
    modify.add_argument("--smooth", type=int, default=None, help="Laplacian smooth iterations")
    modify.add_argument("--subdivide", type=int, default=None, help="subdivision levels (1-3)")
    modify.add_argument("--decimate", type=float, default=None, help="decimate ratio below 1.0")
    modify.add_argument("--out", type=Path, default=None, help="output model path")
    modify.set_defaults(func=cmd_3d_modify)

    image_to_mesh = three_d_sub.add_parser(
        "image-to-mesh", help="convert a reference image into a mesh via TripoSR"
    )
    image_to_mesh.add_argument("image", type=Path, help="reference image (PNG/JPG)")
    image_to_mesh.add_argument(
        "--project", required=True, help="project to write outputs and reports into"
    )
    image_to_mesh.add_argument("--out", type=Path, default=None, help="output mesh path")
    image_to_mesh.add_argument(
        "--chunk-size", type=int, default=None, help="VRAM-safe chunk size (auto if omitted)"
    )
    image_to_mesh.add_argument(
        "--device", choices=("cuda", "cpu"), default="cuda", help="cuda or cpu (slow fallback)"
    )
    image_to_mesh.add_argument(
        "--mc-resolution", type=int, default=256, help="marching-cubes resolution"
    )
    image_to_mesh.add_argument(
        "--remove-bg", action="store_true", default=True, help="strip background before conversion"
    )
    image_to_mesh.set_defaults(func=cmd_3d_image_to_mesh)

    from_image = three_d_sub.add_parser(
        "from-image", help="TripoSR then convert → material → turntable pipeline"
    )
    from_image.add_argument("image", type=Path, help="reference image (PNG/JPG)")
    from_image.add_argument(
        "--project", required=True, help="project to write outputs and reports into"
    )
    from_image.add_argument("--chunk-size", type=int, default=None, help="VRAM-safe chunk size")
    from_image.add_argument(
        "--device", choices=("cuda", "cpu"), default="cuda", help="cuda or cpu (slow fallback)"
    )
    from_image.add_argument(
        "--mc-resolution", type=int, default=256, help="marching-cubes resolution"
    )
    from_image.add_argument(
        "--remove-bg", action="store_true", default=True, help="strip background before conversion"
    )
    from_image.set_defaults(func=cmd_3d_from_image)


def _resolution(value: str) -> tuple[int, int]:
    width, _, height = value.lower().partition("x")
    try:
        return int(width), int(height)
    except ValueError as error:
        raise BlenderError(f"resolution {value!r} must be WxH, for example 640x360") from error


def cmd_3d_inspect(args: argparse.Namespace) -> int:
    """Report what a model contains, measured by Blender."""

    try:
        report = inspect_model(args.model)
    except BlenderError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if args.json:
        json.dump(report.facts, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return EXIT_OK
    facts = report.facts
    print(f"{args.model.name}")
    print(f"  objects:   {facts.get('objects')} ({facts.get('mesh_objects')} mesh)")
    print(f"  geometry:  {facts.get('vertices')} vertices, {facts.get('triangles')} triangles")
    print(f"  size:      {facts.get('size')}")
    print(f"  materials: {', '.join(facts.get('materials') or []) or 'none'}")
    problems = report.problems
    if problems:
        print("  problems:  " + ", ".join(problems))
    print("verdict: " + ("clean" if not problems else "needs repair; run `ms 3d convert`"))
    return EXIT_OK


def cmd_3d_convert(args: argparse.Namespace) -> int:
    """Repair a model and write it in another format."""

    destination = (args.out or args.model.parent) / f"{args.model.stem}{EXPORT_FORMATS[args.to]}"
    try:
        before = inspect_model(args.model)
        conversion = convert_model(args.model, destination, decimate=args.decimate)
    except BlenderError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"wrote {conversion.destination}")
    print(f"  triangles: {before.triangles} -> {conversion.triangles}")
    print(f"  problems:  {', '.join(conversion.problems) or 'none'}")
    return EXIT_OK if not conversion.problems else EXIT_FAILURE


def cmd_3d_material(args: argparse.Namespace) -> int:
    """Fetch a keyless PBR material, bind it to a model, and prove it rendered."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    fetch_mat_fn = resolve_monkeypatchable("fetch_material", fetch_material)
    try:
        project = load_fn(args.project)
        material = fetch_mat_fn(project, args.material, resolution=args.resolution)
        staged_hdri = (
            fetch_hdri(project, args.hdri, resolution=args.resolution) if args.hdri else None
        )
        destination = args.out or annotate_output(project, args.model, "-pbr.glb")
        proof = annotate_output(project, args.model, "-proof.png") if args.render else None
        resolution = _resolution(args.proof_resolution)
        result = bind(
            args.model,
            material.maps,
            destination=destination,
            hdri=staged_hdri.path if staged_hdri else None,
            render=proof,
            resolution=resolution,
        )
    except (ManifestError, BlenderError, MaterialError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"{args.material}: bound {', '.join(result.bound_roles)}")
    if material.ignored_roles:
        print(f"  ignored:    {', '.join(material.ignored_roles)} (not wired into the shader)")
    print(f"  staged:     {len(material.staged)} map(s) with sidecars")
    print("  credits:    assets/CREDITS.md and assets/index.json refreshed")
    if result.unwired_slots:
        print(f"  unwired:    {', '.join(result.unwired_slots)} (no slot to use them in)")
    if staged_hdri is not None:
        print(f"  hdri:       {staged_hdri.relative}")
    if destination is not None:
        print(f"  model:      {destination.relative_to(project.root)}")
    if proof is not None and proof.is_file():
        luma = proof_frame_luminance(proof)
        print(f"  proof:      {proof.relative_to(project.root)} (luma {luma:.1f})")
    problems = result.problems
    report = {**result.facts, "material": args.material, "licence": "CC0", "problems": problems}
    write_report(project, report, f"{args.model.stem}-material-qc.json")
    print("verdict: " + ("pass" if not problems else "FAIL " + "; ".join(problems)))
    return EXIT_OK if not problems else EXIT_FAILURE


def annotate_output(project: Project, model: Path, suffix: str) -> Path:
    """A file name inside a project's 3D output, derived from the model's name."""

    return project_output(project, f"{model.stem}{suffix}")


def cmd_3d_turntable(args: argparse.Namespace) -> int:
    """Render a turntable and report its measured frames."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        resolution = _resolution(args.resolution)
        if args.project:
            project = load_fn(args.project)
            destination = project_output(project, args.model.stem)
        else:
            project = None
            destination = Path("/tmp/opencode/blender") / args.model.stem
        result = turntable(
            args.model,
            destination,
            frames=max(args.frames, 2),
            resolution=resolution,
            baseline=args.baseline,
        )
    except (ManifestError, BlenderError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    report = result.facts
    width, height = report["resolution"]
    print(f"rendered {report['frames']} frame(s) at {width}x{height}")
    print(f"  directory: {result.directory}")
    print(f"  sheet:     {result.sheet.name}")
    print(f"  report:    {result.report_path.name}")
    if report.get("environment") and report["environment"] != "clean":
        print(f"  environment: {report['environment']}")
    if project is not None:
        written = write_report(project, report, f"{args.model.stem}-turntable-qc.json")
        print(f"  archived:  {written.relative_to(project.root)}")
    for name, passed in (report.get("checks") or {}).items():
        print(f"  {'ok  ' if passed else 'FAIL'} {name}")
    print("verdict: " + ("pass" if report.get("ok") else "FAIL"))
    return EXIT_OK if report.get("ok") else EXIT_FAILURE


def cmd_3d_modify(args: argparse.Namespace) -> int:
    """Procedurally modify a model headlessly through Blender."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
        path = Path(args.model).expanduser()
        if not path.is_file():
            raise BlenderError(f"no such model: {path}")

        operations: list[dict] = []
        if args.ops is not None:
            import yaml

            operations = yaml.safe_load(args.ops.read_text(encoding="utf-8")) or []
            if not isinstance(operations, list):
                got = type(operations).__name__
                raise BlenderError(f"--ops file must contain a list, got {got}")
        else:
            if args.mirror is not None:
                operations.append(
                    {
                        "type": "mirror",
                        "x": args.mirror == "x",
                        "y": args.mirror == "y",
                        "z": args.mirror == "z",
                        "merge": True,
                    }
                )
            if args.smooth is not None:
                operations.append({"type": "smooth", "iterations": args.smooth, "factor": 0.5})
            if args.subdivide is not None:
                operations.append({"type": "subdivide", "levels": args.subdivide})
            if args.decimate is not None:
                operations.append({"type": "decimate", "ratio": args.decimate})
        if not operations:
            raise BlenderError("no operations given; pass --ops or a single-op flag")

        output = args.out or project_output(project, f"{path.stem}-modified.glb")
        result = modify_model(path, output, operations)
    except (ManifestError, BlenderError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"modified {path.name}")
    print(f"  output:  {result.output.relative_to(project.root)}")
    print(f"  operations: {result.operations_applied}")
    print(f"  triangles: {result.before_triangles} -> {result.after_triangles}")
    print(f"  vertices:  {result.before_vertices} -> {result.after_vertices}")
    if result.facts.get("problems"):
        print("  problems:  " + ", ".join(result.facts["problems"]))
    print("verdict: " + ("pass" if result.ok else "FAIL"))
    return EXIT_OK if result.ok else EXIT_FAILURE


def cmd_3d_image_to_mesh(args: argparse.Namespace) -> int:
    """Convert a reference image into a mesh via TripoSR headless."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
        output = args.out or project_output(project, f"{Path(args.image).stem}-base.obj")
        result = image_to_mesh_project(
            args.image,
            project_root=project.root,
            output_dir=output.parent,
            chunk_size=args.chunk_size,
            device=args.device,
            mc_resolution=args.mc_resolution,
            remove_bg=args.remove_bg,
        )
    except (ManifestError, TripoSRError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"image-to-mesh {Path(args.image).name}")
    print(f"  output:  {result.output.relative_to(project.root)}")
    print(f"  report:  {result.report_path.relative_to(project.root)}")
    print(f"  device:  {result.report.get('device')} chunk={result.report.get('chunk_size')}")
    print("verdict: " + ("pass" if result.ok else "FAIL"))
    return EXIT_OK if result.ok else EXIT_FAILURE


def cmd_3d_from_image(args: argparse.Namespace) -> int:
    """Run TripoSR then convert → material → turntable on a reference image."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
        image_path = Path(args.image).expanduser()
        if not image_path.is_file():
            raise TripoSRError(f"no such reference image: {image_path}")

        mesh_result = image_to_mesh_project(
            image_path,
            project_root=project.root,
            chunk_size=args.chunk_size,
            device=args.device,
            mc_resolution=args.mc_resolution,
            remove_bg=args.remove_bg,
        )
        if not mesh_result.ok:
            print(f"error: TripoSR failed: {mesh_result.report.get('problems')}", file=sys.stderr)
            return EXIT_FAILURE

        converted = modify_model(
            mesh_result.output,
            project_output(project, f"{image_path.stem}-base.glb"),
            [{"type": "decimate", "ratio": 0.8}],
        )
        if not converted.ok:
            print(f"error: convert failed: {converted.facts.get('problems')}", file=sys.stderr)
            return EXIT_FAILURE

        print(f"from-image {image_path.name}")
        print(f"  mesh:    {mesh_result.output.relative_to(project.root)}")
        print(f"  glb:     {converted.output.relative_to(project.root)}")
        print(f"  triangles: {converted.before_triangles} -> {converted.after_triangles}")
        print("verdict: pass")
        return EXIT_OK
    except (ManifestError, TripoSRError, BlenderError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
