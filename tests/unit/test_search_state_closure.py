"""UI/state closure only; no real index/model/prefs writes."""
import unittest
from unittest.mock import patch
from pathlib import Path
import backend.app as app

class SearchSnapshotTests(unittest.TestCase):
    def rows(self,n):
        return [dict(name=f'{i}.pdf',path=str(app.settings.PROJECT/'workspace'/f'{i}.pdf'),extension='pdf',size=i,modified_time='2026-10-15T00:00:00',score=n-i) for i in range(n)]

    def state(self):
        rows=self.rows(15)
        return app.Session(last_search_query={'semantic_query':'test concept','scope':'workspace'},last_search_results=rows,current_filtered_results=list(rows),display_limit=10,history=[{'role':'user','content':'previous question'}])

    def test_source_total_never_becomes_card_or_action_count(self):
        state=self.state();event=app.search_snapshot(state,{'results':state.last_search_results,'total':19})
        self.assertEqual(event['counts'],{'candidates':19,'verified':15,'displayed':10})
        self.assertEqual(event['total'],15)
        self.assertEqual(len(event['results']),10)
        self.assertIn('展示 10',app.listing_reply(event))
        self.assertEqual(event['search_state']['query'],state.last_search_query)

    def test_filter_reuses_semantic_snapshot_and_history(self):
        state=self.state();app.search_snapshot(state,{'results':state.last_search_results,'total':19})
        with patch.object(app.file_tools,'is_path_allowed',return_value=True), patch.object(app.search_retrieval,'find_files',side_effect=AssertionError('must not scan')):
            event=app.structured_search({'reuse_current':True,'keyword':'test concept','scope':'workspace','extensions':['pdf'],'min_size':7,'sort_by':'size_desc'},state)
        self.assertEqual(event['tool'],'filter_results')
        self.assertEqual(event['counts'],{'candidates':19,'verified':8,'displayed':8})
        self.assertEqual(event['results'][0]['size'],14)
        self.assertEqual(state.history,[{'role':'user','content':'previous question'}])
        self.assertEqual(state.last_search_query['semantic_query'],'test concept')
        # Clearing form filter restores only the original qualified snapshot.
        with patch.object(app.file_tools,'is_path_allowed',return_value=True):
            event=app.structured_search({'reuse_current':True,'keyword':'test concept','scope':'workspace'},state)
        self.assertEqual(event['counts']['verified'],15)

    def test_changed_query_cannot_claim_reuse(self):
        with self.assertRaises(ValueError):app.structured_search({'reuse_current':True,'keyword':'other','scope':'workspace'},self.state())
        with self.assertRaises(ValueError):app.structured_search({'reuse_current':True,'keyword':'test concept','scope':'desktop'},self.state())

    def test_new_form_search_uses_same_count_contract(self):
        state=self.state()
        with patch.object(app.search_retrieval,'find_files',return_value={'results':self.rows(15),'total':19,'limit':20}):
            event=app.structured_search({'keyword':'new','scope':'workspace'},state)
        self.assertEqual(event['counts'],{'candidates':19,'verified':15,'displayed':15})
        self.assertEqual(state.history,[{'role':'user','content':'previous question'}])

    def test_missing_index_cannot_publish_stale_cards(self):
        event=app.search_snapshot(self.state(),{'results':[],'total':0,'needs_index':True})
        self.assertEqual(event['counts']['displayed'],0)

    def test_ui_mode_and_compact_details(self):
        self.assertIn('高级筛选</button>',app.HTML)
        self.assertIn('syncFilterForm()',app.HTML)
        self.assertIn('<details id="indexDetails">',app.HTML)
        self.assertNotIn('结果已按相关性排序',app.HTML)
        self.assertIn('currentSearchState=null;',app.HTML)

if __name__=='__main__':unittest.main()
