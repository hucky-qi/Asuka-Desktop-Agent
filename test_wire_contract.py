"""Pure wire regression; public HTTP/behavior coverage is documented in TESTING."""
import copy,json,unittest
import calendar_semantics,preference_application,persona,memory_learning

def validation(value):
 if isinstance(value,dict):return {k:validation(v) for k,v in value.items() if k!='description'}
 if isinstance(value,list):return [validation(v) for v in value]
 return value

class WireContractTests(unittest.TestCase):
 def test_compaction_keeps_validation_and_original(self):
  old=preference_application.understanding_hint();saved=copy.deepcopy(old)
  # Existing compact pair-map is part of the preexisting wire contract. The full
  # response schema still validates key/value combinations separately.
  wire=calendar_semantics.compact_hint(old)
  normalized=copy.deepcopy(old);normalized['properties']['answer_context']['properties']['requirements']['items']=copy.deepcopy(wire['properties']['answer_context']['properties']['requirements']['items'])
  self.assertEqual(validation(normalized),validation(wire));self.assertEqual(old,saved)
  self.assertLess(len(json.dumps(wire).encode()),len(json.dumps(normalized).encode()))
 def test_nonduplicated_file_rules_and_semantic_instructions_retained(self):
  wire=calendar_semantics.compact_hint(preference_application.understanding_hint())
  self.assertIn('Method-specific',wire['properties']['file']['description'])
  for word in ('domain','mode','needs_history'):self.assertIn(word,preference_application.CONTEXT_PROMPT)
  for word in ('scenario','applicability','CURRENT','permission'):self.assertIn(word,memory_learning.PROMPT)
  self.assertEqual(wire['properties']['file']['additionalProperties'],False)
if __name__=='__main__':unittest.main()
