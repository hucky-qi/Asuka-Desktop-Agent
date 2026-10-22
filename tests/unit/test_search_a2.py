import unittest,json,tempfile
from pathlib import Path
from array import array
from types import SimpleNamespace as NS
from unittest.mock import patch
import settings,app,file_tools,search_intent,semantic_search,identifier_intent
from tests.unit import test_accuracy_constraints as fixtures

class A2Tests(unittest.TestCase):
 setUp=fixtures.HardConstraintTests.setUp
 def seed(self, rows):
  with semantic_search.connect() as db:
   for name,chunks in rows:
    p=self.desktop/name;p.parent.mkdir(exist_ok=True,parents=True);p.write_text('fixture');st=p.stat()
    db.execute('INSERT INTO documents(path,mtime_ns,size,model,indexed_at) VALUES(?,?,?,?,?)',(str(p),st.st_mtime_ns,st.st_size,settings.EMBED_MODEL,1))
    for i,text in enumerate(chunks):db.execute('INSERT INTO chunks(path,position,text,vector,model) VALUES(?,?,?,?,?)',(str(p),i,text,array('f',[1,0]).tobytes(),settings.EMBED_MODEL))
   db.commit()
 def ask(self,q,tool='semantic_search_files',args=None,session=None,scope='desktop',fast=False):
  args=args or {'query':'project','scope':'all','extensions':['pdf']}
  responses=iter([NS(message=NS(content='',tool_calls=[NS(function=NS(name=tool,arguments=args))])),NS(message=NS(content='ok',tool_calls=[]))])
  with patch.object(app,'chat',side_effect=lambda **kw:next(responses)),patch.object(semantic_search,'embedding_vectors',return_value=[array('f',[1,0])]):return app.answer(q,session or app.Session(),scope,fast=fast)[1]
 def names(self,events):return {item['name'] for e in events for item in e.get('results',[])}
 def test_full_identifier_and_attributes_have_distinct_roles(self):
  c=search_intent.Constraints('找 R8042 的PDF，2026-01-01之后，大于2MB，版本v2.1','all')
  self.assertEqual(c.identifiers(),['r8042']);self.assertEqual(c.types,{'.pdf'});self.assertEqual(c.bounds['min_size'],2*1024**2+1)
  for q in ['找2025年文档','找最近7天小于12KB的PDF','找版本3.2的说明','打开第2个文件']:
   self.assertEqual(search_intent.Constraints(q,'all').identifiers(),[],q)
  self.assertEqual(search_intent.Constraints('找 AB12-CD34 资料','all').identifiers(),['ab12-cd34'])
  self.assertEqual(search_intent.Constraints('找项目编号V123的PDF','all').identifiers(),['v123'])
  self.assertEqual(search_intent.Constraints('在“资料P123”文件夹找PDF','all').identifiers(),[])
 def test_full_prefix_cannot_collapse(self):
  self.seed([('MATH5826.pdf',['Mathematics']),('COMP5826.pdf',['Computing']),('5826.pdf',['Numeric only'])])
  self.assertEqual(self.names(self.ask('语义搜索MATH5826有关PDF')),{'MATH5826.pdf'})
  self.assertEqual(self.names(self.ask('找MATH5826有关PDF',tool='find_files',args={'keyword':'course','scope':'all'})),{'MATH5826.pdf'})
 def test_project_identifier_survives_model_omission(self):
  self.seed([('P207.pdf',['Project']),('P314.pdf',['Project'])])
  self.assertEqual(self.names(self.ask('语义搜索P207相关PDF')),{'P207.pdf'})
  self.assertEqual(self.names(self.ask('找P207相关PDF',tool='find_files',args={'keyword':'project'})),{'P207.pdf'})
 def test_and_across_chunks_and_or_not_same(self):
  self.seed([('both.pdf',['ENGR4107 subject','BIO315 subject']),('first.pdf',['ENGR4107 subject']),('second.pdf',['BIO315 subject']),('unrelated.pdf',['ENGR8107 subject'])])
  both=self.ask('语义搜索同时涉及ENGR4107和BIO315的PDF')
  either=self.ask('语义搜索ENGR4107或BIO315的PDF')
  self.assertEqual(self.names(both),{'both.pdf'});self.assertEqual(self.names(either),{'both.pdf','first.pdf','second.pdf'})
  self.assertNotIn('_retrieval_evidence',both[0]);self.assertNotIn('_retrieval_evidence',either[0])
 def test_or_proof_only_claims_actual_matches(self):
  self.seed([('only.pdf',['ZX9 evidence'])])
  with patch.object(semantic_search,'embedding_vectors',return_value=[array('f',[1,0])]):
   data=semantic_search.search_semantic('project','desktop',['pdf'],required_identifiers=['zx9','r8042'],identifier_groups=(('zx9',),('r8042',)))
  proof=data['_retrieval_evidence'][0];self.assertEqual(proof.identifiers,('zx9',));self.assertFalse(proof.matches(self.desktop/'only.pdf',['zx9','r8042']))
 def test_plain_path_and_or_before_top_n(self):
  for name in ['ENGR4107.pdf','BIO315.pdf','ENGR4107-BIO315.pdf','ENGR4107.png','ENGR14107.pdf']:(self.desktop/name).write_text('fixture')
  for relation,expected in [('同时涉及ENGR4107和BIO315',{'ENGR4107-BIO315.pdf'}),('ENGR4107或BIO315',{'ENGR4107.pdf','BIO315.pdf','ENGR4107-BIO315.pdf'})]:
   self.assertEqual(self.names(self.ask('找'+relation+'的PDF',tool='find_files',args={'keyword':'wrong','limit':20})),expected)
 def test_ambiguous_list_requires_clarification(self):
  for q in ['语义搜索ZX9、R8042有关PDF','找ZX9 R8042资料']:
   c=search_intent.Constraints(q,'all')
   with self.assertRaisesRegex(ValueError,'关系不明确'):c.identifier_groups()
 def test_type_scope_and_followup_keep_qualified_set(self):
  self.seed([('ZX9.pdf',['Identifier']),('ZX9.txt',['Identifier']),('R8042.pdf',['Identifier'])]);(self.downloads/'ZX9.pdf').write_text('fixture')
  s=app.Session();first=self.ask('找桌面上的ZX9相关文件',tool='find_files',args={'keyword':'wrong','scope':'downloads'},session=s)
  self.assertEqual(self.names(first),{'ZX9.pdf','ZX9.txt'})
  self.assertEqual(self.names(self.ask('只看PDF',session=s,fast=True)),{'ZX9.pdf'})
  self.assertEqual(self.names(self.ask('按最近修改排序',session=s,fast=True)),{'ZX9.pdf'})
 def test_truncated_excerpt_does_not_decide_cross_chunk_eligibility(self):
  self.seed([('both.pdf',['ZX9 '+('Context '*200),'R8042 evidence'])])
  c=search_intent.Constraints('语义搜索同时涉及ZX9与R8042的PDF','desktop')
  with patch.object(semantic_search,'embedding_vectors',return_value=[array('f',[1,0])]):data=semantic_search.search_semantic('project','desktop',['pdf'],required_identifiers=c.identifiers(),identifier_groups=c.identifier_groups())
  data['results'][0]['excerpt']='display only';c.accept_semantic_evidence(data['_retrieval_evidence']);c.check_event({'tool':'semantic_search_files','results':data['results']})
 def test_semantic_media_path_preserves_identifiers(self):
  (self.desktop/'ZX9.png').write_text('fixture');(self.desktop/'R8042.png').write_text('fixture')
  self.assertEqual(self.names(self.ask('语义搜索ZX9图片',args={'query':'pictures'})),{'ZX9.png'})
 def test_unseen_empty_and_english_logic(self):
  self.seed([('only.pdf',['ZX9 evidence']),('other.pdf',['R8042 evidence'])])
  self.assertEqual(self.names(self.ask('语义搜索ZX9 and R8042 PDF')),set())
  self.assertEqual(self.names(self.ask('语义搜索ZX9 or R8042 PDF')),{'only.pdf','other.pdf'})
  self.assertEqual(self.names(self.ask('语义搜索XY1234567有关PDF')),set())

 def test_content_evidence_uses_all_index_chunks_not_snippet(self):
  self.seed([('both.pdf',['ZX9 evidence','R8042 evidence','budget discussion']),('one.pdf',['ZX9 budget discussion'])])
  self.assertEqual(self.names(self.ask('找同时涉及ZX9与R8042的PDF',tool='search_file_content',args={'keyword':'budget'})),{'both.pdf'})
 def test_structured_filename_identifier_logic(self):
  for name in ['ENG90001.pdf','CHEM120.pdf','ENG90001-CHEM120.pdf','MATH120.pdf']:(self.desktop/name).write_text('fixture')
  state=app.Session()
  event=app.structured_search({'keyword':'ENG90001或CHEM120','scope':'desktop','extensions':['pdf']},state)
  self.assertEqual(self.names([event]),{'ENG90001.pdf','CHEM120.pdf','ENG90001-CHEM120.pdf'})
  event=app.structured_search({'keyword':'同时涉及ENG90001和CHEM120','scope':'desktop','extensions':['pdf']},state)
  self.assertEqual(self.names([event]),{'ENG90001-CHEM120.pdf'})
 def test_top_n_after_identifier_filter(self):
  for name in ['ZX9.pdf','R8042.pdf','other.pdf']:(self.desktop/name).write_text('fixture')
  event=self.ask('找ZX9或R8042的PDF',tool='find_files',args={'keyword':'other','limit':1})[0]
  self.assertEqual(event['total'],2);self.assertEqual(len(event['results']),1);self.assertTrue(self.names([event])<={'ZX9.pdf','R8042.pdf'})
