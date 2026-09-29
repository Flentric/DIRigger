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
6. **Materials.** If the OBJ has several materials, each becomes a slot. With a single material
   (like the CJ rip), UV islands are sorted into head / torso / legs / feet from the weights and
   given the textures you listed. `--material torso=hero_logan_body.mat` reuses an existing game
   material instead of the new `hero_logan_<slot>.mat` names.

## Other commands

```sh
python -m dirigger info  hero_logan/hero_logan.msh --bones     # describe a model
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
