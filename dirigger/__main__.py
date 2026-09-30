"""DIRigger command line.

  python -m dirigger info  <model.msh>
  python -m dirigger gltf  <model.msh> <out.glb> [--skin NAME] [--all]
  python -m dirigger unpack <level.rpack> [name] [--out DIR]
  python -m dirigger install <out_hero_logan> <level.rpack | folder> ... [--mesh-only]
"""

import argparse
import os
import sys

from .ce5.msh import load_msh, NODE_MESH
from .ce5.skin import parse_skin


def _load_skin(msh_path):
    folder, fname = os.path.split(os.path.abspath(msh_path))
    base = os.path.splitext(fname)[0]
    for cand in os.listdir(folder):
        if cand.lower() == (base + ".skin").lower():
            with open(os.path.join(folder, cand), "rb") as f:
                return parse_skin(f.read())
    return None


def cmd_info(args):
    m = load_msh(args.msh)
    skin = _load_skin(args.msh)
    print(f"{m.name}  anim script: {m.anim_script}")
    print(f"nodes: {len(m.nodes)} ({len(m.bones)} bones/dummies, {len(m.mesh_nodes)} meshes)")
    print(f"\nmaterials ({m.slot_count} slots, surface params {m.surface_params}):")
    for i, mat in enumerate(m.materials):
        print(f"  {i:2d} {mat.name}  flags={mat.flags:#x}")
    print("\nmesh nodes:")
    for n in m.mesh_nodes:
        me = n.mesh
        lod = me.lods[0]
        print(f"  [{n.index}] {n.name}: {lod.vertex_count} verts, "
              f"{sum(lod.index_counts) // 3} tris, slots {me.surface_slots}, "
              f"palettes {[len(p) for p in me.palettes]}"
              + (f", {len(me.morphs.names)} morphs" if me.morphs else ""))
    if args.bones:
        print("\nskeleton:")
        for n in m.nodes:
            if n.type != NODE_MESH:
                par = m.nodes[n.parent].name if n.parent >= 0 else "-"
                print(f"  {n.index:3d} {n.name:24s} parent={par}")
    if skin:
        print("\nskins:")
        names = {n.index: n.name for n in m.nodes}
        for s in skin.skins:
            hid = ", ".join(names.get(h, str(h)) for h, _ in s.hidden)
            mats = ", ".join(f"{sl}:{m.materials[mi].name}" for sl, mi in s.material_map)
            print(f"  {s.name}\n    hides: {hid}\n    slots: {mats}")


def cmd_gltf(args):
    from .export.gltf import export_glb
    m = load_msh(args.msh)
    skin = None
    sf = _load_skin(args.msh)
    if sf:
        skin = sf.get(args.skin) if args.skin else sf.skins[0]
        if skin is None:
            sys.exit(f"skin {args.skin!r} not found; have: {[s.name for s in sf.skins]}")
    export_glb(m, args.out, skin=skin, include_hidden=args.all)
    print(f"wrote {args.out}" + (f" (skin {skin.name})" if skin else ""))


def cmd_unpack(args):
    from .build.install import unpack
    out = args.out or os.path.join("templates", args.name)
    for p in unpack(args.rpack, args.name, out):
        print(f"wrote {p}")


def cmd_install(args):
    import glob
    import shutil
    from .build.install import install
    base = args.name or next((os.path.splitext(f)[0] for f in os.listdir(args.model)
                              if f.lower().endswith(".msh")), None)
    if not base:
        sys.exit(f"no .msh in {args.model}")
    packs = []
    for t in args.rpacks:
        packs += sorted(glob.glob(os.path.join(t, "*.rpack"))) if os.path.isdir(t) else [t]
    if not packs:
        sys.exit("no .rpack files given")
    for pk in packs:
        bak = pk + ".bak"
        if not os.path.exists(bak):
            shutil.copyfile(pk, bak)
        changed = install(pk, args.model, base, textures=not args.mesh_only,
                          flat_normals=not args.keep_normals)
        if changed:
            print(f"{os.path.basename(pk)}: replaced {', '.join(changed)}  (original kept as .bak)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dirigger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info", help="describe a compiled mesh")
    p.add_argument("msh")
    p.add_argument("--bones", action="store_true", help="list the skeleton")
    p.set_defaults(fn=cmd_info)
    p = sub.add_parser("gltf", help="export a compiled mesh to .glb")
    p.add_argument("msh")
    p.add_argument("out")
    p.add_argument("--skin", help="skin preset to apply (default: first)")
    p.add_argument("--all", action="store_true", help="include nodes the skin hides")
    p.set_defaults(fn=cmd_gltf)
    p = sub.add_parser("unpack", help="extract a mesh (e.g. hero_logan) from a level .rpack")
    p.add_argument("rpack")
    p.add_argument("name", nargs="?", default="hero_logan")
    p.add_argument("--out", help="output folder (default: templates/<name>)")
    p.set_defaults(fn=cmd_unpack)
    p = sub.add_parser("install", help="put a built model into level .rpack files")
    p.add_argument("model", help="the autorig output folder (e.g. out_hero_logan)")
    p.add_argument("rpacks", nargs="+", help=".rpack files, or folders of them")
    p.add_argument("--name", help="hero to replace (default: the .msh name in the folder)")
    p.add_argument("--mesh-only", action="store_true", help="leave the textures alone")
    p.add_argument("--keep-normals", action="store_true",
                   help="keep the hero's normal maps instead of flat ones")
    p.set_defaults(fn=cmd_install)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
