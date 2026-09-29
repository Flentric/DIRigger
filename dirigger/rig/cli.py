"""`autorig` command: OBJ -> rigged Dead Island player model."""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HELP = """\
Auto-rig a humanoid OBJ onto a Dead Island player skeleton and write the game
files (msh, MeshFixups, VertexData, IndexData, Skin, SkinFixups) plus textures
and glTF previews.

Settings can come from a JSON file (given with --config, or <model>.json next
to the OBJ). Command-line flags win over the JSON. Example CJ.json:

  {
    "template": "templates/hero_logan/hero_logan.msh",
    "textures": {"head": "face.png", "torso": "vest.png",
                 "legs": "legs.png", "feet": "sneakerbincblk.png"}
  }

Texture regions (used when the OBJ has a single material): head, torso (with
arms and hands), legs (with the belt/pelvis), feet.
"""


def _default_template():
    for cand in ("templates/hero_logan/hero_logan.msh", "templates/hero_logan.msh"):
        p = os.path.join(HERE, cand)
        if os.path.exists(p):
            return p
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="autorig", description=HELP,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("obj", help="input model (.obj)")
    ap.add_argument("--config", help="JSON settings file")
    ap.add_argument("--template", help="template player .msh (default: templates/hero_logan)")
    ap.add_argument("--out", help="output folder (default: <obj folder>/out_<name>)")
    ap.add_argument("--name", help="output base name (default: template name, e.g. hero_logan)")
    ap.add_argument("--up", help="source up axis, e.g. +y, -z (default: auto)")
    ap.add_argument("--forward", help="source forward axis, e.g. +y (default: auto)")
    ap.add_argument("--keep-size", action="store_true",
                    help="keep the model's own height instead of matching the template")
    ap.add_argument("--texture", action="append", default=[], metavar="REGION=FILE",
                    help="texture for a region (head/torso/legs/feet); repeatable")
    ap.add_argument("--material", action="append", default=[], metavar="SLOT=NAME.mat",
                    help="use an existing game material for a slot; repeatable")
    args = ap.parse_args(argv)

    obj_path = os.path.abspath(args.obj)
    obj_dir = os.path.dirname(obj_path)
    cfg = {}
    cfg_path = args.config or os.path.splitext(obj_path)[0] + ".json"
    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            cfg = json.load(f)
        cfg_dir = os.path.dirname(os.path.abspath(cfg_path))
        print(f"settings: {cfg_path}")
    else:
        cfg_dir = obj_dir

    def rel(p, root):
        return p if p is None or os.path.isabs(p) else os.path.join(root, p)

    template = args.template or rel(cfg.get("template"), cfg_dir) or _default_template()
    if not template or not os.path.exists(template):
        sys.exit("error: no template found. Pass --template path/to/hero_logan.msh "
                 "(the raw rpack dump folder with .msh, .MeshFixups, .VertexData, .IndexData, "
                 ".Skin, .SkinFixups) or put it in templates/hero_logan/.")
    textures = {k: rel(v, cfg_dir) for k, v in cfg.get("textures", {}).items()}
    for t in args.texture:
        k, v = t.split("=", 1)
        textures[k.strip().lower()] = os.path.abspath(v)
    materials = dict(cfg.get("materials", {}))
    for t in args.material:
        k, v = t.split("=", 1)
        materials[k.strip().lower()] = v.strip()

    import numpy  # noqa: F401  (fail early with a clear message)
    from ..ce5.msh import load_msh
    from ..ce5.skin import parse_skin
    from ..io.obj import load_obj
    from .template import load_template
    from .autorig import autorig
    from ..build.player import build_player, verify_output
    from ..export.gltf import export_glb

    t0 = time.time()
    tm = load_msh(template)
    skin_path = os.path.splitext(template)[0] + ".Skin"
    tskin = parse_skin(open(skin_path, "rb").read()) if os.path.exists(skin_path) else None
    base = args.name or cfg.get("name") or os.path.splitext(tm.name)[0]
    out = args.out or rel(cfg.get("out"), cfg_dir) or os.path.join(obj_dir, f"out_{base}")
    print(f"template: {template} ({len(tm.bones)} bones)")

    obj = load_obj(obj_path)
    print(f"model: {obj_path} ({len(obj.positions)} vertices, {len(obj.tris_pos)} triangles, "
          f"materials: {', '.join(obj.materials)})")
    tmpl = load_template(tm)
    rig = autorig(tmpl, obj.positions, obj.tris_pos,
                  up=args.up or cfg.get("up"), forward=args.forward or cfg.get("forward"),
                  keep_size=args.keep_size or cfg.get("keep_size", False))

    stats = build_player(tm, tskin, obj, rig, out, base=base, textures=textures or None,
                         materials=materials)
    written = verify_output(out, base)
    sf = parse_skin(open(os.path.join(out, base + ".Skin"), "rb").read())
    for label, skin_name in (("tpp", next((s.name for s in sf.skins if "tpp" in s.name.lower()), None)),
                             ("fpp", next((s.name for s in sf.skins if "fpp" in s.name.lower()), None))):
        if skin_name:
            export_glb(written, os.path.join(out, f"preview_{label}.glb"), skin=sf.get(skin_name))

    lines = [f"output: {out}", "", "mesh nodes:"]
    for name, nv, pals, slots in stats["parts"]:
        lines.append(f"  {name}: {nv} vertices, surfaces={len(pals)} palettes={pals} slots={slots}")
    lines += ["", "material slots:"]
    for s, mname, tex in stats["materials"]:
        lines.append(f"  {s:8s} -> {mname}   texture: {tex or 'MISSING (set one with --texture ' + s + '=file.png)'}")
    lines += ["", "skins:"]
    for name, hidden in stats["skins"]:
        lines.append(f"  {name}: hides {', '.join(hidden) or 'nothing'}")
    lines.append(f"\ndone in {time.time() - t0:.1f}s")
    report = "\n".join(lines)
    print(report)
    with open(os.path.join(out, "report.txt"), "w") as f:
        f.write(report + "\n")


if __name__ == "__main__":
    main()
