"""Tests for the sample calculator. These fail until the off-by-one is fixed."""

import unittest

from calc import add, double


class TestCalc(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)

    def test_double(self):
        self.assertEqual(double(4), 8)


if __name__ == "__main__":
    unittest.main()
