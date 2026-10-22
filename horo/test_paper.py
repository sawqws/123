import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paper  # noqa: E402


class PaperTest(unittest.TestCase):
    def test_bank(self):
        for ch in 'xycmt0123456789+()':
            self.assertIn(ch, paper.STAMPS, ch)

    def test_tokens(self):
        self.assertEqual(paper.tokens('a^{18}b^2'), [('a', False), ('1', True), ('8', True), ('b', False), ('2', True)])

    def test_solve_and_photo(self):
        nb = paper.Notebook(seed=3)
        nb.title('Лист 7')
        nb.solve(2, [[[('z^2 + 6z + 9', 'z^3 + 27'), ' : ', ('3z + 9', 'z^2 − 3z + 9')], ['= ', ('1', '3')]]])
        nb.solve(8, [['(', ('b^4 (b − c)^2', 'a^6 (c − a)'), ')^3 : (− ', ('b', 'a'), ')^6']])
        self.assertGreater(nb.ink.sum(), 1000)
        with tempfile.TemporaryDirectory() as d:
            p = nb.save(os.path.join(d, 't.jpg'))
            self.assertTrue(os.path.getsize(p) > 100000)

    def test_page_full(self):
        nb = paper.Notebook(seed=1)
        line = [[('x + 1', 'y')]]
        with self.assertRaises(ValueError):
            for n in range(40):
                nb.solve(n, line)

    def test_too_wide(self):
        nb = paper.Notebook()
        with self.assertRaises(ValueError):
            nb.solve(1, [[('x + ' * 40 + 'x', '2')]])


if __name__ == '__main__':
    unittest.main()
