# DIRigger

Tools for getting custom player models into the original **Dead Island** (Chrome Engine 5).

- [x] Read compiled player meshes (`.msh` + `.MeshFixups` + `.VertexData` + `.IndexData`) and skins (`.Skin` + `.SkinFixups`).
- [x] Write them. Unmodified game files rebuild byte-for-byte.
- [x] **Auto-rigger**: static humanoid `.obj` → rigged Dead Island player model with TPP/FPP skins.
- [x] Export any model to `.glb` for checking in Blender.
- [x] Read and write level `.rpack` files: install a built model (mesh + textures) straight into them, or extract a hero.
- [x] Facial morphs: a custom head gets the template's 43 targets, copied from the nearest template vertex.

Format notes: [docs/FORMAT.md](docs/FORMAT.md).

## Auto-rigger

Needs Python 3.8+ and numpy; `autorig.bat` installs numpy for you.

1. Put a raw dump of the hero you want to replace in `templates/hero_logan/`: `hero_logan.msh`,
   `.MeshFixups`, `.VertexData`, `.IndexData`, `.Skin`, `.SkinFixups`. See [templates/README.md](templates/README.md).
2. Drag your `.obj` onto **`autorig.bat`**. It asks what you want:

   ```
   What do you want to make?
     1) Loose files: add it as a new character (no pack editing)
     2) Replace Logan inside the level .rpack files
     3) Just build the files (I'll install them myself)
   ```

   It guesses the textures from the `.png` names next to the model (`face` → head, `vest` →
   torso, ...) and lets you change them. With no template yet, drag any level `.rpack` into the
   window and Logan is taken from it. For option 2, drag the game's `Data` folder (or some
   `.rpack` files) into the window; it lists every level pack it finds and asks which ones to
   change (`2`, `1,4`, `2-5` or `all`), so you can test with a single level first. Your answers are saved in `<model>.json`, so the next run
   is just Enter, Enter.

   Or run it with options, which skips the questions:

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

## Putting the model in the game

Every level `.rpack` carries its own copy of the hero, so every level you want to play needs the
new model. `install` writes it straight into the packs: the hero's six mesh parts, plus the colour
textures named after its materials (`hero_logan_body`, `hero_logan_head`), encoded in the game's
own size and DXT format. Their normal maps are made flat so Logan's creases don't show on your
model. No other tool or renaming is needed; the first run keeps each original as `.rpack.bak`.

```sh
python -m dirigger install out_hero_logan "C:\path\to\Dead Island\DI\Data"   # every pack in a folder
python -m dirigger install out_hero_logan hotel_PC.rpack                         # or single packs
```

or drag the `out_hero_logan` folder and then the pack(s)/folder onto `install.bat`.
`--mesh-only` leaves the textures alone. To restore a pack, delete it and remove `.bak` from the copy.

### Loose files instead of editing packs

`--loose hero_cj` renames only the body and head materials in the mesh to `hero_cj_body.mat` /
`hero_cj_head.mat` and writes `loose/` with those two `.mat` files (for the game's `templates.mtt`,
shine from `--shine`, default 0) plus their DXT5 `.dds` textures. The names in the mesh, the
`.mat` files and the textures always match, so nothing needs renaming by hand. This is the setup
tested in game: the four files go in a folder under `Data`, e.g. `Data\(Character Textures)\Cj`.

```sh
python autorig.py CJ.obj --texture head=face.png ... --loose hero_cj
```

To get a template without another tool: `python -m dirigger unpack hotel_PC.rpack hero_logan`
writes `templates/hero_logan/`.

## Other commands

```sh
python -m dirigger info  hero_logan/hero_logan.msh --bones     # describe a model, its materials and skins
python -m dirigger gltf  hero_logan/hero_logan.msh logan.glb --skin Logan_FPP
```

## Tests

Game files are not included. Point `DIRIGGER_SAMPLES` at a folder containing extracted heroes
(and optionally an `.obj` to auto-rig and a level `.rpack`):

```sh
DIRIGGER_SAMPLES=/path/to/samples python -m unittest discover tests
```
