import json
import unittest
from copy import deepcopy
from pathlib import Path
from jsonschema import Draft202012Validator
from validate_fixture import parse_datetime, validate_board, validate_comparison, validate_revision

ROOT=Path(__file__).resolve().parents[1]
FX=ROOT/'fixtures'
records=json.loads((FX/'synthetic_records.json').read_text())
revision=json.loads((FX/'synthetic_analysis_revision.json').read_text())
child=json.loads((FX/'synthetic_analysis_revision_excluded.json').read_text())
board=json.loads((FX/'synthetic_board.json').read_text())
comparison=json.loads((FX/'synthetic_revision_comparison.json').read_text())
revisions=[revision,child]
board_schema=Draft202012Validator(json.loads((ROOT/'contracts'/'investigation_board.schema.json').read_text()))
comparison_schema=Draft202012Validator(json.loads((ROOT/'contracts'/'revision_comparison.schema.json').read_text()))

def item_of_kind(b,kind):
    return next(i for i in b['items'] if i['kind']==kind)

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

    def test_node_without_status_passes(self):
        node=item_of_kind(board,'node')
        self.assertNotIn('status',node)
        self.assertEqual(list(board_schema.iter_errors(board)),[])
        self.assertEqual(validate_board(records,revisions,board),[])

    def test_claim_without_status_fails(self):
        broken=deepcopy(board)
        del item_of_kind(broken,'claim')['status']
        self.assertTrue(list(board_schema.iter_errors(broken)))
        self.assertTrue(validate_board(records,revisions,broken))

    def test_unresolved_status_allowed_by_schema(self):
        ok=deepcopy(board)
        item_of_kind(ok,'claim')['status']='unresolved'
        self.assertEqual(list(board_schema.iter_errors(ok)),[])
        # ...but it must still match the source claim, which is 'reported'.
        self.assertTrue(validate_board(records,revisions,ok))

class TestTimestamps(unittest.TestCase):
    def test_same_instant_different_offsets(self):
        a=parse_datetime('2026-11-14T20:00:00Z')
        b=parse_datetime('2026-11-14T15:00:00-05:00')
        self.assertEqual(a,b)
        same=deepcopy(board)
        same['created_at']='2026-11-14T20:03:30Z'
        same['updated_at']='2026-11-14T21:03:30+01:00'
        self.assertEqual(validate_board(records,revisions,same),[])

    def test_offset_ordering_not_lexical(self):
        # Lexically '...T19...' < '...T20...', but 19:00-05:00 is 00:00Z next day.
        broken=deepcopy(board)
        broken['created_at']='2026-11-14T19:00:00-05:00'
        self.assertTrue(validate_board(records,revisions,broken))

    def test_naive_timestamp_fails(self):
        self.assertIsNone(parse_datetime('2026-11-14T20:00:00'))
        broken=deepcopy(board)
        broken['updated_at']='2026-11-14T20:03:30'
        self.assertTrue(validate_board(records,revisions,broken))

    def test_unparseable_timestamp_fails(self):
        self.assertIsNone(parse_datetime('last tuesday'))
        broken=deepcopy(board)
        broken['notes'][0]['created_at']='last tuesday'
        self.assertTrue(validate_board(records,revisions,broken))
        broken_cmp=deepcopy(comparison)
        broken_cmp['created_at']='not-a-date'
        self.assertTrue(validate_comparison(revisions,broken_cmp))

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

    def test_null_compared_ref_rejects_fabricated_evidence(self):
        # Reviewer repro: item 1 has compared_ref_id None.
        broken=deepcopy(comparison)
        self.assertIsNone(broken['items'][1]['compared_ref_id'])
        broken['items'][1]['compared_evidence_ids']=['fabricated-record']
        broken['items'][1]['support_status']='unresolved'
        self.assertTrue(validate_comparison(revisions,broken))
        self.assertTrue(list(comparison_schema.iter_errors(broken)))

    def test_null_base_ref_rejects_evidence(self):
        broken=deepcopy(comparison)
        broken['items'].append({'kind':'claim','base_ref_id':None,'compared_ref_id':'claim-1',
            'support_status':'newly_supported','base_evidence_ids':['rec-01'],'compared_evidence_ids':['rec-01']})
        self.assertTrue(validate_comparison(revisions,broken))
        self.assertTrue(list(comparison_schema.iter_errors(broken)))
        broken['items'][-1]['base_evidence_ids']=[]
        self.assertEqual(validate_comparison(revisions,broken),[])
        self.assertEqual(list(comparison_schema.iter_errors(broken)),[])

def linked_case():
    """BLA-63: base claim-2 cites rec-02 and rec-01; rec-02 is excluded and the child rewords
    it as claim-x on rec-01. Both items list the advisory possible match."""
    base=deepcopy(revision); kid=deepcopy(child); cmp=deepcopy(comparison)
    base['claims'][1]['evidence_ids']=['rec-02','rec-01']
    kid['claims'].append({'id':'claim-x','text':'Jordan may meet Casey.','evidence_ids':['rec-01'],'status':'reported'})
    link={'shared_evidence_ids':['rec-01'],'remaining_sources':1,'original_sources':2}
    cmp['items'][1].update(support_status='unresolved',base_evidence_ids=['rec-02','rec-01'],
        possible_matches=[{'ref_id':'claim-x',**link}])
    cmp['items'].append({'kind':'claim','base_ref_id':None,'compared_ref_id':'claim-x','support_status':'newly_supported',
        'base_evidence_ids':[],'compared_evidence_ids':['rec-01'],'possible_matches':[{'ref_id':'claim-2',**link}]})
    return [base,kid],cmp

class TestPossibleMatches(unittest.TestCase):
    def test_valid_link(self):
        revs,cmp=linked_case()
        self.assertEqual(validate_comparison(revs,cmp),[])
        self.assertEqual(list(comparison_schema.iter_errors(cmp)),[])

    def test_link_to_unknown_item(self):
        revs,cmp=linked_case()
        cmp['items'][1]['possible_matches'][0]['ref_id']='claim-9'
        self.assertTrue(validate_comparison(revs,cmp))

    def test_link_must_be_mirrored(self):
        revs,cmp=linked_case()
        del cmp['items'][-1]['possible_matches']
        self.assertTrue(validate_comparison(revs,cmp))

    def test_shared_records_cited_by_both_and_permitted(self):
        revs,cmp=linked_case()
        for item in (cmp['items'][1],cmp['items'][-1]):
            item['possible_matches'][0]['shared_evidence_ids']=['rec-02']
        self.assertTrue(validate_comparison(revs,cmp))

    def test_counts_must_match(self):
        revs,cmp=linked_case()
        for item in (cmp['items'][1],cmp['items'][-1]):
            item['possible_matches'][0]['remaining_sources']=2
        self.assertTrue(validate_comparison(revs,cmp))

    def test_link_only_on_unmatched_items(self):
        revs,cmp=linked_case()
        cmp['items'][0]['possible_matches']=cmp['items'][1]['possible_matches']
        self.assertTrue(list(comparison_schema.iter_errors(cmp)))
        revs,cmp=linked_case()
        cmp['items'][-1]['support_status']='unresolved'
        self.assertTrue(validate_comparison(revs,cmp))

    def test_link_schema_shape(self):
        revs,cmp=linked_case()
        cmp['items'][1]['possible_matches']=[]
        self.assertTrue(list(comparison_schema.iter_errors(cmp)))
        revs,cmp=linked_case()
        cmp['items'][1]['possible_matches'][0]['shared_evidence_ids']=[]
        self.assertTrue(list(comparison_schema.iter_errors(cmp)))

    def test_must_be_parent_child(self):
        broken=deepcopy(comparison)
        broken['base_analysis_id'],broken['compared_analysis_id']=broken['compared_analysis_id'],broken['base_analysis_id']
        self.assertTrue(validate_comparison(revisions,broken))

if __name__=='__main__': unittest.main()
