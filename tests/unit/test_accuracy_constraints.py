import tempfile,unittest,time,json,os
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace as NS
from array import array
import app,settings,search_intent,file_tools,semantic_search

class HardConstraintTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=settings.PROJECT/'acceptance/accuracy-motion');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.desktop=self.root/'Desktop';self.downloads=self.root/'Downloads';self.desktop.mkdir();self.downloads.mkdir()
  for name in ['SearchAnimeCell.swift','fx-share-card.jpg','anime.png','animation.jpg','动漫第一集.mp4','anime_episode.mkv','animation_project.blend','animation_project.mp4','unrelated.mp4','cartoon.mov','学习课程.mp4']:(self.desktop/name).write_text('sample')
  (self.downloads/'anime_other.webm').write_text('sample')
  self.patches=[patch.object(settings,'SEARCH_ROOTS',{'desktop':self.desktop,'downloads':self.downloads,'workspace':self.root/'Workspace'}),patch.object(settings,'SEMANTIC_DB',self.root/'index.sqlite3'),patch.object(settings,'PREFERENCES_PATH',self.root/'prefs.json')]
  for p in self.patches:p.start();self.addCleanup(p.stop)
 def ask(self,q,fast=True,scope='all'):
  if fast:
   with patch.object(app,'chat',side_effect=AssertionError('fast should not invoke model')):return app.answer(q,app.Session(),scope,fast=True)[1]
  responses=iter([NS(message=NS(content='',tool_calls=[NS(function=NS(name='find_files',arguments={'keyword':'anime','scope':'all'}))])),NS(message=NS(content='ok',tool_calls=[]))])
  with patch.object(app,'chat',side_effect=lambda **kw:next(responses)):return app.answer(q,app.Session(),scope)[1]
 def test_video_fast_and_llm_preserve_type(self):
  for fast in [True,False]:
   result=self.ask('查找一下动漫有关的视频',fast)[0]
   self.assertEqual({x['name'] for x in result['results']},{'动漫第一集.mp4','anime_episode.mkv','cartoon.mov','anime_other.webm'})
   self.assertTrue(all('.'+x['extension'] in settings.VIDEO_EXTENSIONS for x in result['results']))
 def test_image_and_generic_files_differ(self):
  image=self.ask('找动漫图片')[0]['results'];self.assertEqual({x['name'] for x in image},{'anime.png'})
  generic=self.ask('找动漫相关文件')[0]['results'];self.assertIn('SearchAnimeCell.swift',{x['name'] for x in generic})
 def test_desktop_video_both_hard_boundaries(self):
  result=self.ask('找桌面上的动漫视频')[0]['results']
  self.assertEqual(len(result),3);self.assertTrue(all(Path(x['path']).is_relative_to(self.desktop) for x in result))
 def test_empty_is_empty_no_type_relaxation(self):
  for p in self.root.rglob('*'):
   if p.suffix.lower() in settings.VIDEO_EXTENSIONS:p.unlink()
  self.assertEqual(self.ask('找动漫视频')[0]['total'],0)
 def test_filter_before_limit_and_sort(self):
  for i in range(25):(self.desktop/f'anime_source_{i}.swift').write_text('fixture')
  result=self.ask('找动漫视频')[0]
  self.assertEqual(result['total'],4);self.assertEqual(len(result['results']),4)
  self.assertTrue(result['results'][0]['score']>=result['results'][-1]['score'])
 def test_non_dictionary_theme_still_kept(self):
  self.assertEqual([x['name'] for x in self.ask('查找学习有关的视频')[0]['results']],['学习课程.mp4'])
 def test_category_directory_recalls_title_without_topic_word(self):
  folder=self.desktop/'animation'/'作品名称'/'12';folder.mkdir(parents=True)
  video=folder/'episode12.mp4';video.write_text('fixture')
  (folder/'player.swift').write_text('fixture');(folder/'poster.jpg').write_text('fixture')
  project=self.desktop/'software'/'animation';project.mkdir(parents=True);(project/'demo.mp4').write_text('fixture')
  for fast in [True,False]:
   result=self.ask('查找一下动漫有关的视频',fast)[0]['results'];names={x['name'] for x in result}
   self.assertIn('episode12.mp4',names)
   self.assertNotIn('animation_project.mp4',names);self.assertNotIn('demo.mp4',names)
   self.assertNotIn('player.swift',names);self.assertNotIn('poster.jpg',names)
   self.assertTrue(all('.'+x['extension'] in settings.VIDEO_EXTENSIONS for x in result))
  selected=file_tools.find_files('动漫',scope='folder:'+str(folder),extensions=['mp4'],topic='动漫')
  self.assertEqual([x['path'] for x in selected['results']],[str(video)])
 def test_category_directory_does_not_cross_explicit_scope(self):
  folder=self.downloads/'animation';folder.mkdir();(folder/'episode.mp4').write_text('fixture')
  result=self.ask('找桌面上的动漫视频')[0]['results']
  self.assertNotIn('episode.mp4',{x['name'] for x in result})
 def test_explicit_suffix_narrows_category(self):
  self.assertEqual(search_intent.Constraints('找动漫MP4视频','all').types,{'.mp4'})
 def test_folder_name_is_not_extra_type(self):
  self.assertEqual(search_intent.Constraints('在“图片”文件夹找视频','all').types,settings.VIDEO_EXTENSIONS)
 def test_model_metadata_cannot_weaken_explicit_bounds(self):
  c=search_intent.Constraints('找最近一周大于2MB小于5MB的视频','all')
  result=c.metadata({'min_size':0,'max_size':99*1024**2,'modified_after':'2000-01-01'})
  self.assertEqual(result['min_size'],2*1024**2+1);self.assertEqual(result['max_size'],5*1024**2-1)
  self.assertGreater(file_tools.parse_date(result['modified_after']),time.time()-8*86400)
 def test_last_card_validation_rejects_rogue_extension(self):
  item=file_tools.file_info(self.desktop/'SearchAnimeCell.swift')
  with self.assertRaises(ValueError):search_intent.Constraints('找动漫视频','all').check_event({'results':[item]})
 def test_semantic_metadata_filtered_before_top_n(self):
  p1=self.desktop/'old.txt';p1.write_text('old');os.utime(p1,(1,1));p2=self.desktop/'new.txt';p2.write_text('new')
  with semantic_search.connect() as db:
   for p in [p1,p2]:
    st=p.stat();db.execute('INSERT INTO documents(path,mtime_ns,size,model,indexed_at) VALUES(?,?,?,?,?)',(str(p),st.st_mtime_ns,st.st_size,settings.EMBED_MODEL,1));db.execute('INSERT INTO chunks(path,position,text,vector,model) VALUES(?,?,?,?,?)',(str(p),0,'theme',array('f',[1,0]).tobytes(),settings.EMBED_MODEL))
   db.commit()
  with patch.object(semantic_search,'embedding_vectors',return_value=[array('f',[1,0])]):data=semantic_search.search_semantic('theme',extensions=['txt'],limit=1,hard_filters={'modified_after':'2020-01-01'})
  self.assertEqual(data['total'],1);self.assertEqual(data['results'][0]['name'],'new.txt')

 def test_type_words_in_explicit_path_are_not_file_categories(self):
  self.assertFalse(search_intent.Constraints('在 /Users/example/Desktop/videos 查找动漫相关文件','all').types)

 def test_image_topic_rejects_weak_fuzzy_names_and_keeps_category(self):
  for name in ['information.png','04-confirmation.png','04-confirmation.jpg']:(self.desktop/name).write_text('fixture')
  folder=self.desktop/'animation'/'作品';folder.mkdir(parents=True);(folder/'角色.png').write_text('fixture')
  for fast in [True,False]:
   names={x['name'] for x in self.ask('找一下动漫有关的图片',fast)[0]['results']}
   self.assertEqual(names,{'anime.png','角色.png'})
  restricted=self.ask('在桌面的“animation”文件夹找动漫图片',False)[0]['results']
  self.assertEqual([x['name'] for x in restricted],['角色.png'])

 def seed_semantic_accuracy(self):
  rows=[(self.desktop/'9321.pdf','PDF file formats and data access',[1,0]),
        (self.desktop/'COMP9900.pdf','Team project planning and delivery',[.9,.43589]),
        (self.downloads/'mechanisms.txt','No participant gains by misreporting preferences',[0,1])]
  with semantic_search.connect() as db:
   for p,text,vector in rows:
    p.write_text('isolated fixture');st=p.stat()
    db.execute('INSERT INTO documents(path,mtime_ns,size,model,indexed_at) VALUES(?,?,?,?,?)',(str(p),st.st_mtime_ns,st.st_size,settings.EMBED_MODEL,1))
    db.execute('INSERT INTO chunks(path,position,text,vector,model) VALUES(?,?,?,?,?)',(str(p),0,text,array('f',vector).tobytes(),settings.EMBED_MODEL))
   db.commit()

 def test_course_type_words_removed_and_identifier_precedes_vector(self):
  self.seed_semantic_accuracy()
  with patch.object(semantic_search,'embedding_vectors',return_value=[array('f',[1,0])]) as embed:
   events=self.ask('语义搜索9900有关的pdf');result=events[0]['results']
   self.assertEqual([x['name'] for x in result],['COMP9900.pdf']);self.assertEqual(embed.call_args.args[0][0].split('\nQuery: ',1)[-1],'9900')
   self.assertIn('9900',result[0]['evidence_source'])
  c=search_intent.Constraints('语义搜索COMP9900相关PDF','all');self.assertEqual(c.semantic('project PDF'),'comp9900 project')

 def test_concept_semantics_empty_and_cache_scope_are_preserved(self):
  self.seed_semantic_accuracy()
  def embed(queries):return [array('f',[-1,0] if queries[0].split('\nQuery: ',1)[-1]=='完全无关主题' else [0,1])]
  with patch.object(semantic_search,'embedding_vectors',side_effect=embed):
   for query in ['strategyproofness','策略无关性']:
    first=self.ask('语义搜索'+query,scope='downloads')[0]['results']
    self.assertEqual([x['name'] for x in first],['mechanisms.txt'])
    self.assertEqual(first[0]['evidence_source'],'语义相似')
    self.assertEqual(self.ask('语义搜索'+query,scope='desktop')[0]['results'],[])
   self.assertEqual(self.ask('语义搜索完全无关主题')[0]['results'],[])
  with patch.object(semantic_search,'embedding_vectors',return_value=[array('f',[1,0])]):
   self.assertEqual([x['name'] for x in self.ask('语义搜索9900有关的pdf')[0]['results']],['COMP9900.pdf'])

 def test_identifier_excerpt_is_centered_and_course_path_beats_incidental_mention(self):
  self.seed_semantic_accuracy();p=self.desktop/'statement.pdf';p.write_text('fixture');st=p.stat()
  text='Generic discussion. '*50+' COMP9900 completed.'
  with semantic_search.connect() as db:
   db.execute('INSERT INTO documents(path,mtime_ns,size,model,indexed_at) VALUES(?,?,?,?,?)',(str(p),st.st_mtime_ns,st.st_size,settings.EMBED_MODEL,1))
   db.execute('INSERT INTO chunks(path,position,text,vector,model) VALUES(?,?,?,?,?)',(str(p),0,text,array('f',[1,0]).tobytes(),settings.EMBED_MODEL));db.commit()
  with patch.object(semantic_search,'embedding_vectors',return_value=[array('f',[1,0])]):
   found=self.ask('语义搜索9900有关的pdf')[0]['results']
   self.assertEqual(found[0]['name'],'COMP9900.pdf')
   self.assertIn('9900',next(x for x in found if x['name']=='statement.pdf')['excerpt'])
   self.assertTrue(all(x['name']!='9321.pdf' for x in found))
  self.assertFalse(file_tools.path_identifier_evidence(self.desktop/'test-9900'/'fixtures'/'sample.pdf',['9900']))
  self.assertTrue(file_tools.path_identifier_evidence(self.desktop/'9900'/'lecture.pdf',['9900']))

 def test_reused_session_does_not_mix_topic_type_or_scope(self):
  s=app.Session()
  with patch.object(app,'chat',side_effect=AssertionError('no model needed')):
   for q,scope,suffix in [('找动漫图片','desktop','png'),('找动漫视频','downloads','webm'),('找学习视频','desktop','mp4')]:
    _,events=app.answer(q,s,scope,fast=True)
    self.assertEqual({x['extension'] for x in events[0]['results']},{suffix})
    self.assertEqual(s.current_filtered_results,s.last_search_results)
    self.assertTrue(all(Path(x['path']).is_relative_to(getattr(self,scope)) for x in s.current_filtered_results))
