import unittest

import numpy as np

from dirigger.build.player import align_face


class FaceAlignTests(unittest.TestCase):

    def test_landmarks_line_up(self):
        src = {"chin": 159.0, "nose": 167.0, "crown": 182.0, "nose_z": 15.0, "cx": 0.0,
               "width": 18.0, "depth": 20.0}
        dst = {"chin": 156.0, "nose": 165.0, "crown": 183.0, "nose_z": 14.0, "cx": 0.0,
               "width": 18.0, "depth": 23.0}
        pts = np.array([[0, 159.0, 15.0], [0, 167.0, 15.0], [0, 182.0, 15.0],
                        [0, 163.0, 12.0]])
        q, scale = align_face(pts, src, dst)
        np.testing.assert_allclose(q[:3, 1], [156.0, 165.0, 183.0])
        np.testing.assert_allclose(q[:3, 2], 14.0)
        self.assertAlmostEqual(q[3, 1], 160.5)                   # halfway chin->nose stays halfway
        self.assertAlmostEqual(scale[3, 1], 9.0 / 8.0)


if __name__ == "__main__":
    unittest.main()
