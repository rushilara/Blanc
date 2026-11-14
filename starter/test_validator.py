import json
import unittest
from copy import deepcopy
from pathlib import Path
from validate_fixture import validate_revision

ROOT=Path(__file__).resolve().parents[1]
records=json.loads((ROOT/'fixtures'/'synthetic_records.json').read_text())
revision=json.loads((ROOT/'fixtures'/'synthetic_analysis_revision.json').read_text())

class TestEvidenceGrounding(unittest.TestCase):
    def test_valid_fixture(self):
        self.assertEqual(validate_revision(records,revision),[])

    def test_cannot_cite_excluded_record(self):
        broken=deepcopy(revision)
        broken['excluded_record_ids']=['rec-02']
        broken['retrieved_record_ids']=['rec-01']
        self.assertTrue(validate_revision(records,broken))

    def test_cross_case_blocked(self):
        broken_records=deepcopy(records)
        broken_records[0]['case_id']='other-case'
        self.assertTrue(validate_revision(broken_records,revision))

if __name__=='__main__': unittest.main()
