# DIRigger

Tools for getting custom player models into the original **Dead Island** (Chrome Engine 5).

- [x] Read compiled player meshes (`.msh` + `.MeshFixups` + `.VertexData` + `.IndexData`) and skins (`.Skin` + `.SkinFixups`).
- [x] Write them. Unmodified game files rebuild byte-for-byte.
- [x] **Auto-rigger**: static humanoid `.obj` → rigged Dead Island player model with TPP/FPP skins.
- [x] Export any model to `.glb` for checking in Blender.
- [ ] Pack textures and `.mat` materials into game resources (need sample dumps, see below).
- [x] Facial morphs: a custom head gets the template's 43 targets, copied from the nearest template vertex.

Format notes: [docs/FORMAT.md](docs/FORMAT.md).

## Auto-rigger

Needs Python 3.8+ and numpy; `autorig.bat` installs numpy for you.

1. Put a raw dump of the hero you want to replace in `templates/hero_logan/`: `hero_logan.msh`,
   `.MeshFixups`, `.VertexData`, `.IndexData`, `.Skin`, `.SkinFixups`. See [templates/README.md](templates/README.md).
2. Drag your `.obj` onto **`autorig.bat`**, or run:

   ```sh
   python autorig.py CJ.obj --texture head=face.png --texture torso=vest.png \
                            --texture legs=legs.png --texture feet=sneakerbincblk.png
   ```

   The same settings can go in a JSON file named after the model (`CJ.json` next to `CJ.obj`):

   ```json
   {
     "textures": {"head": "face.png", "torso": "vest.png",
                  "legs": "legs.png", "feet": "sneakerbincblk.png"}
   }
   ```

3. Results land in `out_hero_logan/` next to the model:

   | File | What |
   |------|------|
   | `hero_logan.msh`, `.MeshFixups`, `.VertexData`, `.IndexData` | the rigged mesh (Logan's skeleton, untouched) |
   | `hero_logan.Skin`, `.SkinFixups` | `Logan_TPP`, `Logan_FPP`, … presets |
   | `textures/hero_logan_<slot>.png/.dds` | one texture per material slot |
   | `preview_tpp.glb`, `preview_fpp.glb` | read back from the files above, open in Blender to check |
   | `report.txt` | parts, bone palettes, material slots, skins |

### What it does

1. **Orientation and size.** Detects up/forward automatically (override with `--up -z --forward +y`)
   and scales the model to the template's height (`--keep-size` to skip).
2. **Skeleton fit.** Measures the legs, spine and arms of both the model and the template, then
   moves every template joint to the matching spot in the model. Works for A-pose and T-pose.
3. **Weights.** Copies skin weights from the template's own body and head, which is deformed onto
   the fitted skeleton first. It then refits using the resulting body regions, and smooths and
   limits the weights to 4 per vertex.
4. **Re-pose.** Moves the model into the template's bind pose, so the game's skeleton and all
   animations are used unchanged.
5. **Game parts.**
   - `body`: everything except the head.
   - `head`: hidden in first person.
   - `head_shadow`: a shadow-only copy of the head, shown only in first person, so the player still casts a full shadow.
   - Surfaces are split to at most 45 bones each.
6. **Materials.** By default the model draws with the **template's own material slots**
   (Logan's body material for the body, his head material for the head, `shadow_def.mat` for
   the head shadow). The material table and the skins' material maps are copied from the
   template unchanged, so every material the model names already exists in the game.
   Textures that share one material (torso, legs, feet) are packed into a single atlas and
   the UVs are moved into its cells. `report.txt` lists which `.dds` goes with which material:
   replace that material's diffuse texture with it to see your model's textures.

### Invisible model?

A material name the game can't find draws nothing. Older builds (and `--custom-materials`
now) named new materials such as `hero_logan_torso.mat` that don't exist in the game's
resource packs, so every surface using them was invisible. Only the head shadow still
showed, because it uses `shadow_def.mat`, which the game has. That's why you could see the
head moving only in the shadow.

The default build avoids this by only using materials that are already in the game. Use
`--custom-materials` (plus `--material slot=name.mat`) only once real `.mat` resources for
those names are packed into the game.

## Other commands

```sh
python -m dirigger info  hero_logan/hero_logan.msh --bones     # describe a model, its materials and skins
python -m dirigger gltf  hero_logan/hero_logan.msh logan.glb --skin Logan_FPP
```

## Still needed from a game dump

The mesh files are complete. To make the materials work in game, I need raw dumps
(same tool and settings as the hero dumps) of:

- one hero material, e.g. `hero_logan_body.mat` and its fixup parts
- one of its textures (all parts)
- the `.rpack` containing them, so the output can be packed back in

## Tests

Game files are not included. Point `DIRIGGER_SAMPLES` at a folder containing extracted heroes
(and optionally an `.obj` to auto-rig):

```sh
DIRIGGER_SAMPLES=/path/to/samples python -m unittest discover tests
```
