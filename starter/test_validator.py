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

def covered():
    # rec-02 left out of a one-record standard window although the vector search ranked it first.
    r=deepcopy(revision)
    r['retrieved_record_ids']=['rec-01']
    r['claims']=[c for c in r['claims'] if set(c['evidence_ids'])<={'rec-01'}]
    r['graph']={'nodes':[],'edges':[]}
    r['timeline_events']=[]
    r['retrieval_coverage']={'evidence_window':'standard','max_records':1,'max_excerpt_chars':32000,
        'skipped':[{'record_id':'rec-02','rank':2,'reason':'record_limit','vector_rank':1}],
        'budget_substituted':False,'left_out_record_ids':['rec-02'],'offer_expanded_window':True}
    return r

class TestRetrievalCoverage(unittest.TestCase):
    def test_valid_coverage(self):
        self.assertTrue(covered()['claims'])
        self.assertEqual(validate_revision(records,covered()),[])

    def test_a_skip_ranked_outside_the_window_is_not_left_out(self):
        far=covered()
        far['retrieval_coverage']['skipped'][0]['vector_rank']=2
        far['retrieval_coverage'].update({'left_out_record_ids':[],'offer_expanded_window':False})
        self.assertEqual(validate_revision(records,far),[])
        far['retrieval_coverage'].update({'left_out_record_ids':['rec-02'],'offer_expanded_window':True})
        self.assertTrue(validate_revision(records,far))

    def test_inconsistent_coverage_is_rejected(self):
        cases=[
            ('retrieved_record_ids',['rec-01','rec-02'],'lists retrieved_record_ids as skipped'),
            ('excluded_record_ids',['rec-02'],'lists excluded_record_ids as skipped'),
        ]
        for field,value,message in cases:
            broken=covered(); broken[field]=value
            self.assertTrue(any(message in e for e in validate_revision(records,broken)),field)
        changes=[
            ({'left_out_record_ids':[]},'left_out_record_ids do not follow'),
            ({'offer_expanded_window':False},'offer_expanded_window does not follow'),
            ({'evidence_window':'expanded'},'offer_expanded_window does not follow'),
            ({'budget_substituted':True},'budget_substituted without a budget skip'),
            ({'skipped':[{'record_id':'rec-02','rank':1,'reason':'record_limit','vector_rank':1}]},'does not fit reason'),
            ({'skipped':[{'record_id':'rec-99','rank':2,'reason':'record_limit'}],'left_out_record_ids':[],'offer_expanded_window':False},'not in the case corpus'),
        ]
        for change,message in changes:
            broken=covered(); broken['retrieval_coverage'].update(change)
            self.assertTrue(any(message in e for e in validate_revision(records,broken)),change)
        cited=covered(); cited['claims'][0]['evidence_ids']=['rec-02']
        self.assertTrue(any('cited records as skipped' in e for e in validate_revision(records,cited)))

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

    def test_must_be_parent_child(self):
        broken=deepcopy(comparison)
        broken['base_analysis_id'],broken['compared_analysis_id']=broken['compared_analysis_id'],broken['base_analysis_id']
        self.assertTrue(validate_comparison(revisions,broken))

if __name__=='__main__': unittest.main()
