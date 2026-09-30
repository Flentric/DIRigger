# Original Dead Island (Chrome Engine 5) compiled player mesh

Notes from reverse-engineering `hero_logan` and `hero_xian`, extracted raw from the game's
`.rpack` files. Everything is little-endian and 32-bit. Offsets are in hex.

A player model is split into six resource parts:

| Part          | Contents |
|---------------|----------|
| `.msh`        | Main blob: header, nodes, mesh descriptors, materials, morphs. Pointers are 32-bit offsets into the blob; `FFFFFFFF` means null. |
| `.MeshFixups` | A flat `u32[]` listing the offset of every pointer in `.msh`, in no particular order. |
| `.VertexData` | Vertex streams for all meshes, one after another. |
| `.IndexData`  | `u16` triangle-list indices for all meshes. |
| `.Skin`       | Skin presets: hidden nodes and slot→material maps. Pointers are `offset + 1`; `0` means null. |
| `.SkinFixups` | `u32 size, u32 numResolves, u32 numVisible(=1)`, then `{i32 off, u32 classId, u32 count}[numResolves]`, then `u32 numPtrs, u32 ptrOffsets[]`. |

Units are centimetres, Y up. Characters face +Z with their left side on +X, and front faces wind
counter-clockwise around the stored normals, i.e. the data is right-handed like glTF (no mirroring
is needed to convert). `bip01` sits at y = 93.857, the same Biped skeleton used by the Chrome Engine 4
`Man.MSH` template.

## `.msh` header (0x44 bytes)

| Off  | Type | Meaning |
|------|------|---------|
| 00   | ptr  | file name (`hero_logan.msh`) |
| 04   | ptr  | null |
| 08   | u32  | 1 (root count?) |
| 0C   | u32  | node count |
| 10   | ptr  | node array |
| 14   | u32  | mesh node count + 1 (Logan 8, Xian 7) |
| 18   | u32  | surface-param count |
| 1C   | ptr  | surface params `u32[]`; each skin preset repeats them as `0x0A00 \| value` |
| 20   | ptr  | material database |
| 24   | u32  | morph name count |
| 28   | ptr  | morph names `{ptr name, u32 0}[]` |
| 2C,30| ptr  | null (collision trees?) |
| 3C   | ptr  | null |
| 40   | ptr  | animation script (`Anims_Player.scr`) |

## Node (0xB0 bytes each)

| Off | Type      | Meaning |
|-----|-----------|---------|
| 00  | f32[3][4] | local transform (parent-relative), row-major, translation in column 3 |
| 30  | f32[3][4] | inverse bind matrix (model → node) |
| 60  | f32[3]    | AABB centre |
| 6C  | f32[3]    | AABB half-extents |
| 78  | ptr       | name (lower-case) |
| 7C  | u32       | node index |
| 80  | ptr       | parent node (null for roots) |
| 84  | u8        | type: 8 bone, 4 dummy/camera, 2 mesh |
| 85  | u8        | child count |
| 86  | u16       | 1 on mesh nodes (LOD count?) |
| 88  | u32       | flags |
| 8C  | ptr       | morph block (mesh nodes; see below) |
| 90  | ptr       | mesh descriptor (mesh nodes) |
| 94..AC |        | unknown; nulls and zeros so far |

Bones come first, then mesh nodes. Mesh nodes have no parent and an identity transform.

### Mesh descriptor (0x20)

```
u32 0 | ptr lods[] | ptr surfaceSlots u16[nSurf] | u16 nLods | u16 nSurf | ptr palettes {ptr u16[], u32 count}[nSurf] | 3×u32 0
```

Each surface (draw call) has a material slot and its own bone palette. Surfaces cover separate,
consecutive vertex ranges.

### LOD (0x28)

```
ptr indexCounts u32[nSurf] | ptr vdecl | u16 nElems | u16 nSurf | u32 vertexCount
u32 indexByteOffset (into .IndexData) | u32 streamOffset[4] (into .VertexData, FFFFFFFF = unused) | ptr null
```

Index values are absolute within the LOD (not rebased per surface).

### Vertex declaration: `{u32 desc, ptr null}[nElems]`

`desc` = `type | usage<<8 | ?<<16 | stream<<24`. Each stream's stride is the sum of the sizes of its elements, in order.

| type | size | format |
|------|------|--------|
| 02 | 12 | float3 position (morphable head) |
| 04 | 4  | ubyte4 (blend weights, sum 255 / blend indices) |
| 06 | 4  | short2 UV, `/4096` |
| 07 | 8  | short4 position, `/8` → cm, w = 1 |
| 0A | 8  | short4n normal / tangent, `/32767`, tangent w = ±1 handedness |

| usage | meaning |
|-------|---------|
| 0 | position |
| 1 | blend weights (D3DCOLOR order: strongest influence in byte 2, then 1, 0, 3) |
| 2 | blend indices, stored as **palette index × 3**, same byte order |
| 3 | normal |
| 4 | UV0 |
| 5 | tangent |

Common layouts:
- Skinned mesh, 6 elements: stream0 = pos(8) + weights(4) + indices(4) + uv(4) = 20 bytes; stream1 = normal(8) + tangent(8) = 16 bytes.
- Rigid mesh (hair), 5 elements: no weights, so stream0 is 16 bytes.
- Shadow proxy (`head_shadow`), 3 elements: pos + weights + indices only.
- Morphable head: stream0 = float3 pos (12); stream1 = weights + indices + uv (12); stream2 = normal + tangent (16).

### Morph block (0x20, `head` only)

```
u32 nMorphs | ptr targetOffsets u32[] | ptr remap u16[] | ptr names ptr[] | u32 vertexCount | ptr data | u32 1 | u32 0
```

The facial targets (`#e_r_blink`, `open`, `pbm`, …) are stored inside `.msh`; see the end of this file.

### Material database

```
u16 slotCount | u16 materialCount | ptr entries {ptr name, u32 flags, u32 0}[materialCount]
```

## `.Skin`

Root (0x14): `ptr skins | 0 | 0 | u32 count | u32 0x00010000`.

Skin preset (0x40):

```
00 u32 0 | 04 u32 0 | 08 ptr name | 0C..23 zero
24 u16 flags(0x81) | 26 u16 ? | 28 u16 nMatMap | 2A u16 nHidden | 2C u32 nParams
30 ptr matMap {u16 slot, u16 material}[] | 34 ptr hidden {u16 node, u16 flags(3)}[] | 38 ptr params u32[] | 3C 0
```

Resolve classes: `A0000000` string, `…01` root, `…02` skin, `…05` params, `…06` hidden list, `…08` material map.
Identical arrays are shared between presets.

### First / third person

The game swaps between presets named `<Hero>_TPP` and `<Hero>_FPP`:

- **TPP** hides the first-person-only nodes (`head_player`, `head_shadow`) plus optional extras such as `backpack`.
- **FPP** hides everything around the camera (`head`, `hairs`/`hair`, collars such as `body_coat` / `coat_player`). It keeps `head_player` (a small neck/collar piece seen from first person) and `head_shadow` (a low-poly head that uses `shadow_def.mat` so the player still casts a full shadow).
- `_pfury` variants remap every slot to the `*_pfury.mat` materials.

## Serialization order and alignment

The game's compiler writes the blob depth-first, and `msh_writer.py` reproduces it byte for byte:

```
header (0x44) | surface params | file name | anim script name
nodes (8-aligned, 0xB0 each)
for each node:  name string
                mesh nodes only:  A record (16-aligned) | B record
                                  [morphs: offsets u32[] | remap u16[] (8) | name ptrs (8) | names | base data (16) | targets (16 each)]
                                  palette table (16) | slot list (8) | each palette u16[] (16)
                                  LOD records (4) | per LOD: index counts (4) | vertex declaration (4)
material db (4): u16 slotCount, u16 count, ptr entries | entries {ptr name, u32 flags, u32 0} | names (deduplicated)
morph name table (4): {ptr name, u32 0}[] (points at the head's morph name strings)
```

`.MeshFixups` lists every pointer slot, including null ones, in node, mesh, LOD, declaration and
header fields. The game's list is unordered; the writer emits it sorted.

Mesh node records keep null pointers at +0x94..+0xA0. Morph base data is the head's float3 positions (a copy of vertex stream 0), in Logan's case followed
by 84 bytes of leftover padding. Each target is `int16 dx, dy, dz` per vertex, stored in a 16-byte
padded block.
