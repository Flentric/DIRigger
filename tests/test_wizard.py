import unittest

from dirigger.rig.wizard import guess_textures, _paths, pick


class WizardTests(unittest.TestCase):

    def test_guess_textures(self):
        got = guess_textures(["face.png", "legs.png", "sneakerbincblk.png", "vest.png"])
        self.assertEqual(got, {"head": "face.png", "torso": "vest.png", "legs": "legs.png",
                               "feet": "sneakerbincblk.png"})

    def test_dragged_paths(self):
        self.assertEqual(_paths('"C:\\Games\\Dead Island\\DI\\Data" D:\\x.rpack'),
                         ["C:\\Games\\Dead Island\\DI\\Data", "D:\\x.rpack"])
        self.assertEqual(_paths("  "), [])

    def test_pick_packs(self):
        self.assertEqual(pick("all", 3), [0, 1, 2])
        self.assertEqual(pick("2", 3), [1])
        self.assertEqual(pick("1, 3", 3), [0, 2])
        self.assertEqual(pick("2-3", 5), [1, 2])
        self.assertEqual(pick("4", 3), [])
        self.assertEqual(pick("x", 3), [])


if __name__ == "__main__":
    unittest.main()
