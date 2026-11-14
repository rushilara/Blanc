import json
import unittest
from copy import deepcopy
from pathlib import Path
from validate_fixture import validate_board, validate_comparison, validate_revision

ROOT=Path(__file__).resolve().parents[1]
FX=ROOT/'fixtures'
records=json.loads((FX/'synthetic_records.json').read_text())
revision=json.loads((FX/'synthetic_analysis_revision.json').read_text())
child=json.loads((FX/'synthetic_analysis_revision_excluded.json').read_text())
board=json.loads((FX/'synthetic_board.json').read_text())
comparison=json.loads((FX/'synthetic_revision_comparison.json').read_text())
revisions=[revision,child]

class TestEvidenceGrounding(unittest.TestCase):
    def test_valid_fixture(self):
        self.assertEqual(validate_revision(records,revision),[])

    def test_valid_child_fixture(self):
        self.assertEqual(validate_revision(records,child),[])

    def test_cannot_cite_excluded_record(self):
        broken=deepcopy(revision)
        broken['excluded_record_ids']=['rec-02']
        broken['retrieved_record_ids']=['rec-01']
        self.assertTrue(validate_revision(records,broken))

    def test_cross_case_blocked(self):
        broken_records=deepcopy(records)
        broken_records[0]['case_id']='other-case'
        self.assertTrue(validate_revision(broken_records,revision))

class TestBoard(unittest.TestCase):
    def test_valid_board(self):
        self.assertEqual(validate_board(records,revisions,board),[])

    def test_board_cites_unknown_evidence(self):
        broken=deepcopy(board)
        broken['items'][0]['evidence_ids']=['rec-99']
        self.assertTrue(validate_board(records,revisions,broken))

    def test_board_cross_case_blocked(self):
        broken=deepcopy(board)
        broken['case_id']='other-case'
        self.assertTrue(validate_board(records,revisions,broken))

    def test_board_unknown_ref(self):
        broken=deepcopy(board)
        broken['items'][0]['ref_id']='edge-99'
        self.assertTrue(validate_board(records,revisions,broken))

    def test_board_status_cannot_be_upgraded(self):
        broken=deepcopy(board)
        broken['items'][0]['status']='observed'
        self.assertTrue(validate_board(records,revisions,broken))

    def test_note_targets_unknown_item(self):
        broken=deepcopy(board)
        broken['notes'][0]['target_item_id']='item-99'
        self.assertTrue(validate_board(records,revisions,broken))

    def test_parent_revision_must_precede(self):
        broken=deepcopy(board)
        broken['parent_revision']=broken['revision']
        self.assertTrue(validate_board(records,revisions,broken))

class TestComparison(unittest.TestCase):
    def test_valid_comparison(self):
        self.assertEqual(validate_comparison(revisions,comparison),[])

    def test_excluded_ids_must_match_delta(self):
        broken=deepcopy(comparison)
        broken['excluded_record_ids']=['rec-01']
        self.assertTrue(validate_comparison(revisions,broken))

    def test_compared_side_cannot_cite_excluded(self):
        broken=deepcopy(comparison)
        broken['items'][0]['compared_evidence_ids']=['rec-01','rec-02']
        self.assertTrue(validate_comparison(revisions,broken))

    def test_not_supported_with_surviving_evidence_rejected(self):
        broken=deepcopy(comparison)
        broken['items'][0]['support_status']='not_supported'
        self.assertTrue(validate_comparison(revisions,broken))

    def test_must_be_parent_child(self):
        broken=deepcopy(comparison)
        broken['base_analysis_id'],broken['compared_analysis_id']=broken['compared_analysis_id'],broken['base_analysis_id']
        self.assertTrue(validate_comparison(revisions,broken))

if __name__=='__main__': unittest.main()
