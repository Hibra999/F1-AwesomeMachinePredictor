from types import SimpleNamespace
import unittest

from f1forecast.training import _tuning_folds


class RustunaFoldsTest(unittest.TestCase):
    def test_cutoff_excludes_whole_gp_until_every_label_is_available(self):
        rows = []
        for i in range(8):
            snapshot = SimpleNamespace(cutoff=i * 10, gp={"race": i * 10 + 1})
            rows.append(SimpleNamespace(gp_id=i, snapshot=snapshot, label_at=i * 10 + 2))
        rows.append(SimpleNamespace(gp_id=3, snapshot=rows[3].snapshot, label_at=40))

        (first_fit, first_test), (second_fit, second_test) = _tuning_folds(rows[::-1])

        self.assertEqual({r.gp_id for r in first_test}, {4, 5})
        self.assertEqual({r.gp_id for r in first_fit}, {0, 1, 2})
        self.assertEqual({r.gp_id for r in second_test}, {6, 7})
        self.assertEqual({r.gp_id for r in second_fit}, set(range(6)))
        self.assertEqual(sum(r.gp_id == 3 for r in second_fit), 2)
