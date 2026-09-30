"""Question-and-answer front end for autorig.bat: drag an .obj on it and pick what you want.

Answers are saved in <model>.json (textures at the top level, where the plain
command line reads them too; the rest under "wizard"), so the next run only
needs Enter to repeat them.
"""

import glob
import json
import os
import shlex

from .cli import HERE, _default_template

MODES = (
    ("loose", "Loose files: add it as a new character (no pack editing)"),
    ("rpack", "Replace Logan inside the level .rpack files"),
    ("build", "Just build the files (I'll install them myself)"),
)

REGIONS = ("head", "torso", "legs", "feet")
GUESS = {
    "head": ("face", "head"),
    "torso": ("torso", "vest", "shirt", "body", "chest", "jacket", "top"),
    "legs": ("leg", "pant", "jean", "trouser", "short"),
    "feet": ("feet", "foot", "shoe", "sneak", "boot"),
}


def _paths(text):
    """Split what was typed or dragged into the window into paths."""
    text = text.strip()
    if not text:
        return []
    try:
        parts = shlex.split(text, posix=False)
    except ValueError:
        parts = [text]
    return [p.strip('"') for p in parts if p.strip('"')]


def guess_textures(pngs):
    out = {}
    for region in REGIONS:
        for p in pngs:
            name = os.path.basename(p).lower()
            if any(k in name for k in GUESS[region]) and p not in out.values():
                out[region] = p
                break
    return out


class Wizard:
    def __init__(self, obj_path, ask=input, say=print):
        self.obj = os.path.abspath(obj_path)
        self.dir = os.path.dirname(self.obj)
        self.stem = os.path.splitext(os.path.basename(self.obj))[0]
        self.ask, self.say = ask, say
        self.cfg_path = os.path.splitext(self.obj)[0] + ".json"
        self.cfg = {}
        if os.path.exists(self.cfg_path):
            with open(self.cfg_path) as f:
                self.cfg = json.load(f)
        self.saved = self.cfg.get("wizard", {})

    # --- questions ------------------------------------------------------------
    def _q(self, text, default=""):
        ans = self.ask(f"{text} [{default}]: " if default != "" else f"{text}: ").strip()
        return ans or str(default)

    def choose_mode(self):
        keys = [k for k, _ in MODES]
        default = keys.index(self.saved.get("mode", "loose")) + 1
        self.say("\nWhat do you want to make?")
        for i, (_, text) in enumerate(MODES, 1):
            self.say(f"  {i}) {text}")
        while True:
            a = self._q("Choice", default)
            if a in ("1", "2", "3"):
                return keys[int(a) - 1]
            self.say("  Type 1, 2 or 3.")

    def choose_textures(self):
        rel = lambda p: p if os.path.isabs(p) else os.path.join(self.dir, p)
        current = {k: rel(v) for k, v in self.cfg.get("textures", {}).items()}
        pngs = sorted(glob.glob(os.path.join(self.dir, "*.png")))
        if not current:
            current = guess_textures(pngs)
        if current:
            self.say("\nTextures:")
            for r in REGIONS:
                self.say(f"  {r:6s} {os.path.basename(current[r]) if r in current else '-'}")
            if self._q("Use these? (y/n)", "y").lower().startswith("y"):
                return current
        if not pngs:
            self.say("  No .png files next to the model; building without textures.")
            return {}
        self.say("\nPictures next to the model:")
        for i, p in enumerate(pngs, 1):
            self.say(f"  {i}) {os.path.basename(p)}")
        out = {}
        for r in REGIONS:
            d = pngs.index(current[r]) + 1 if r in current and current[r] in pngs else ""
            while True:
                a = self._q(f"Texture for {r} (number, 0 = none)", d if d != "" else "0")
                if a.isdigit() and 0 <= int(a) <= len(pngs):
                    if int(a):
                        out[r] = pngs[int(a) - 1]
                    break
                self.say(f"  Type a number from 0 to {len(pngs)}.")
        return out

    def choose_template(self):
        t = self.cfg.get("template")
        if t:
            t = t if os.path.isabs(t) else os.path.join(self.dir, t)
            if os.path.exists(t):
                return t
        t = _default_template()
        if t:
            return t
        self.say("\nNo Logan template yet. It can be taken straight from any level pack.")
        while True:
            a = _paths(self.ask("Drag a level .rpack here (e.g. ...\\DI\\Data\\hotel_PC.rpack), "
                                "then press Enter: "))
            if a and os.path.isfile(a[0]):
                from ..build.install import unpack
                out = os.path.join(HERE, "templates", "hero_logan")
                unpack(a[0], "hero_logan", out)
                self.say(f"  Saved Logan to {out}")
                return os.path.join(out, "hero_logan.msh")
            self.say("  That file wasn't found; try again.")

    def choose_packs(self):
        saved = self.saved.get("rpacks", [])
        if saved and all(os.path.exists(p) for p in saved):
            self.say("\nLevel packs last time:")
            for p in saved:
                self.say(f"  {p}")
            if self._q("Use these again? (y/n)", "y").lower().startswith("y"):
                return saved
        self.say("\nDrag the game's Data folder (every level) or some .rpack files here.")
        while True:
            a = _paths(self.ask("Then press Enter: "))
            ok = [p for p in a if os.path.isdir(p) or p.lower().endswith(".rpack")]
            if ok and all(os.path.exists(p) for p in ok):
                return ok
            self.say("  Couldn't find those; drag the folder or .rpack files again.")

    # --- run --------------------------------------------------------------------
    def run(self):
        from .cli import main as autorig
        self.say(f"DIRigger: {os.path.basename(self.obj)}")
        mode = self.choose_mode()
        name = None
        if mode == "loose":
            name = self._q("\nCharacter name", self.saved.get("name", f"hero_{self.stem.lower()}"))
            name = "".join(c if c.isalnum() or c == "_" else "_" for c in name).lower()
        textures = self.choose_textures()
        template = self.choose_template()
        packs = self.choose_packs() if mode == "rpack" else None

        self.cfg["textures"] = {k: os.path.relpath(v, self.dir) for k, v in textures.items()}
        self.cfg["wizard"] = dict(self.saved, mode=mode, **({"name": name} if name else {}),
                                  **({"rpacks": packs} if packs else {}))
        with open(self.cfg_path, "w") as f:
            json.dump(self.cfg, f, indent=2)

        argv = [self.obj, "--template", template]
        for r, p in textures.items():
            argv += ["--texture", f"{r}={p}"]
        base = name or "hero_logan"
        out = os.path.join(self.dir, f"out_{base}")
        argv += ["--out", out]
        if mode == "loose":
            argv += ["--name", name, "--loose", name]
        self.say("")
        autorig(argv)

        if mode == "rpack":
            from ..__main__ import main as dirigger
            self.say("")
            dirigger(["install", out] + packs)
            self.say("\nDone. Originals are kept as .rpack.bak next to each pack.")
        elif mode == "loose":
            folder = os.path.join(out, "loose")
            self.say(f"\nDone. Copy the four files in\n  {folder}\ninto a folder under the game's "
                     f"Data folder, e.g. Data\\(Character Textures)\\{self.stem}")
            if hasattr(os, "startfile"):
                os.startfile(folder)
        else:
            self.say(f"\nDone. Files are in {out}")
        self.say(f"(Your answers are saved in {os.path.basename(self.cfg_path)}.)")
        return out


def run(obj_path, ask=input, say=print):
    return Wizard(obj_path, ask, say).run()
