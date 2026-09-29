# DIRigger

Tools for getting custom player models into the original **Dead Island** (Chrome Engine 5).

Current status:

- [x] Read compiled player meshes (`.msh` + `.MeshFixups` + `.VertexData` + `.IndexData`): skeleton, skinned geometry, material slots, facial morph names.
- [x] Read and write `.Skin` / `.SkinFixups` (TPP/FPP presets); unmodified files rebuild byte-for-byte.
- [x] Export to `.glb` for inspection in Blender, per skin preset.
- [ ] Write compiled meshes (custom geometry → `.msh` + fixups + vertex/index data).
- [ ] Auto-rigger: fit a custom model to the Dead Island Biped skeleton and transfer weights.
- [ ] Material (`.mat`) generation.

Format notes: [docs/FORMAT.md](docs/FORMAT.md).

## Usage

Needs Python 3.8+ and nothing else.

```sh
# summary of nodes, surfaces, materials and skins
python -m dirigger info hero_logan/hero_logan.msh --bones

# export the third-person or first-person look to glTF
python -m dirigger gltf hero_logan/hero_logan.msh logan_tpp.glb --skin Logan_TPP
python -m dirigger gltf hero_logan/hero_logan.msh logan_fpp.glb --skin Logan_FPP
```

The part files (`.MeshFixups`, `.VertexData`, …) must sit next to the `.msh` with the same base name,
as produced by a raw `.rpack` dump.

## Tests

Game files are not included. Point `DIRIGGER_SAMPLES` at a folder of extracted heroes:

```sh
DIRIGGER_SAMPLES=/path/to/samples python -m unittest discover tests
```
