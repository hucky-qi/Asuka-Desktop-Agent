import unittest
from unittest.mock import patch
from array import array
import test_accuracy_constraints as fixtures
import app,search_intent,semantic_search,settings

class A1Tests(unittest.TestCase):
 setUp=fixtures.HardConstraintTests.setUp
 ask=fixtures.HardConstraintTests.ask
 seed_semantic_accuracy=fixtures.HardConstraintTests.seed_semantic_accuracy
 def retrieve(self):
  p=self.desktop/'comparison.pdf';p.write_text('fixture');st=p.stat()
  with semantic_search.connect() as db:
   db.execute('INSERT INTO documents(path,mtime_ns,size,model,indexed_at) VALUES(?,?,?,?,?)',(str(p),st.st_mtime_ns,st.st_size,settings.EMBED_MODEL,1))
   db.execute('INSERT INTO chunks(path,position,text,vector,model) VALUES(?,?,?,?,?)',(str(p),0,'COMP6841 '+('Context. '*100)+' COMP5826',array('f',[1,0]).tobytes(),settings.EMBED_MODEL));db.commit()
  with patch.object(semantic_search,'embedding_vectors',return_value=[array('f',[1,0])]):return semantic_search.search_semantic('6841 5826','desktop',['pdf'],required_identifiers=['6841','5826'])
 def test_full_evidence_survives_arbitrary_display_excerpt(self):
  data=self.retrieve();self.assertEqual(data['total'],1)
  data['results'][0]['excerpt']='An intentionally different display window.'
  with patch.object(semantic_search,'search_semantic',return_value=data):
   events=self.ask('语义搜索6841和5826有关PDF')
  self.assertEqual(events[0]['total'],1)
  self.assertNotIn('_retrieval_evidence',events[0])
  self.assertNotIn('_retrieval_evidence',events[0]['results'][0])
 def test_display_text_does_not_grant_qualification(self):
  data=self.retrieve();data.pop('_retrieval_evidence');data['results'][0]['excerpt']='6841 and 5826'
  with patch.object(semantic_search,'search_semantic',return_value=data):self.assertEqual(self.ask('语义搜索6841和5826有关PDF'),[])
 def test_forged_or_wrong_path_proof_rejected_and_hard_types_remain(self):
  data=self.retrieve();c=search_intent.Constraints('语义搜索6841和5826有关PDF','desktop')
  with self.assertRaises(ValueError):c.accept_semantic_evidence([{'path':data['results'][0]['path'],'identifiers':['6841','5826']}])
  c.accept_semantic_evidence(data['_retrieval_evidence']);item=dict(data['results'][0]);other=self.desktop/'other.pdf';other.write_text('fixture');item['path']=str(other)
  with self.assertRaises(ValueError):c.check_event({'tool':'semantic_search_files','results':[item]})
  item['path']=str(self.desktop/'wrong.png')
  with self.assertRaises(ValueError):c.check_event({'tool':'semantic_search_files','results':[item]})
