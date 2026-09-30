import unittest

from dirigger.rig.wizard import guess_textures, _paths


class WizardTests(unittest.TestCase):

    def test_guess_textures(self):
        got = guess_textures(["face.png", "legs.png", "sneakerbincblk.png", "vest.png"])
        self.assertEqual(got, {"head": "face.png", "torso": "vest.png", "legs": "legs.png",
                               "feet": "sneakerbincblk.png"})

    def test_dragged_paths(self):
        self.assertEqual(_paths('"C:\\Games\\Dead Island\\DI\\Data" D:\\x.rpack'),
                         ["C:\\Games\\Dead Island\\DI\\Data", "D:\\x.rpack"])
        self.assertEqual(_paths("  "), [])


if __name__ == "__main__":
    unittest.main()
