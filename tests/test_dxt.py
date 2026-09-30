import unittest

import numpy as np

from dirigger.io.dxt import compress, decompress


class DxtTests(unittest.TestCase):

    def test_roundtrip_close(self):
        rng = np.random.default_rng(1)
        img = np.repeat(np.repeat(rng.integers(0, 256, (8, 8, 4), dtype=np.uint8), 4, 0), 4, 1)
        for fmt in ("dxt1", "dxt5"):
            out = decompress(compress(img, fmt), 32, 32, fmt)
            err = np.abs(out.astype(int) - img.astype(int))
            self.assertLess(err[..., :3].mean(), 6, fmt)     # flat 4x4 blocks: 565 rounding only
            if fmt == "dxt5":
                self.assertLess(err[..., 3].max(), 3)

    def test_sizes(self):
        img = np.zeros((16, 8, 4), np.uint8)
        self.assertEqual(len(compress(img, "dxt1")), 8 * 8)
        self.assertEqual(len(compress(img, "dxt5")), 8 * 16)


if __name__ == "__main__":
    unittest.main()
