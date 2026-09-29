# Templates

Put the raw rpack dump of the hero you want to replace here, one folder per hero:

```
templates/
  hero_logan/
    hero_logan.msh
    hero_logan.MeshFixups
    hero_logan.VertexData
    hero_logan.IndexData
    hero_logan.Skin
    hero_logan.SkinFixups
```

`autorig.bat` / `autorig.py` use `templates/hero_logan/hero_logan.msh` when no `--template` is
given. Game files are not distributed with this repository.
