import unittest,re,subprocess,tempfile,shutil,sys
from pathlib import Path
P=Path(__file__).resolve().parents[2]
class NativeTimeTests(unittest.TestCase):
 @unittest.skipUnless(sys.platform=='darwin' and shutil.which('swiftc'),'macOS Swift only')
 def test_zero_minute_native_grace_matches_backend(self):
  for name in ['AsukaLauncher.swift','desktop_pet.swift']:
   text=(P/'macos'/name).read_text();function=re.search(r'private func reminderTimeValid.*?\n}',text,re.S).group()
   code='import Foundation\n'+function+'\nlet t=Date(timeIntervalSince1970:2000)\nprecondition(reminderTimeValid(t,0,t))\nprecondition(reminderTimeValid(t,0,t.addingTimeInterval(60)))\nprecondition(!reminderTimeValid(t,0,t.addingTimeInterval(61)))\nprecondition(!reminderTimeValid(t,10,t))\nprecondition(reminderTimeValid(t,10,t.addingTimeInterval(-1)))\n'
   with tempfile.TemporaryDirectory() as d:
    root=Path(d);(root/'test.swift').write_text(code);subprocess.run(['swiftc',str(root/'test.swift'),'-o',str(root/'test')],check=True,capture_output=True,timeout=60);subprocess.run([str(root/'test')],check=True,capture_output=True,timeout=3)
 @unittest.skipUnless(sys.platform=='darwin' and shutil.which('swiftc'),'macOS Swift only')
 def test_new_notification_round_does_not_reset_old_ledger(self):
  source=(P/'macos/AsukaLauncher.swift').read_text();a=source.index('            if test || r["synthetic"]');b=source.index('            center.getNotificationSettings',a);block=source[a:b]
  code='''import Foundation
let root=URL(fileURLWithPath:CommandLine.arguments[1]);let fm=FileManager.default
func save(_ d:[String:Any],_ p:URL){try! JSONSerialization.data(withJSONObject:d).write(to:p,options:.atomic)}
let round=String(repeating:"a",count:32),legacy=root.appendingPathComponent("delivery-test-budget-shared-reminder.json")
save(["used":3,"closed":true],legacy)
let active=root.appendingPathComponent("delivery-test-active.json"),ledger=root.appendingPathComponent("delivery-test-"+round+".json")
save(["round":round],active);save(["round":round,"limit":2,"used":0,"closed":false,"expires":Date().timeIntervalSince1970+60],ledger)
let before=try! Data(contentsOf:legacy);var rejected=0,accepted=0
func reply(_ d:[String:Any]){rejected+=1}
let test=true,r=[String:Any]()
for _ in 0..<3 {
'''+block+'''
 accepted+=1
}
precondition(accepted==2 && rejected==1)
precondition((try! JSONSerialization.jsonObject(with:Data(contentsOf:ledger)) as! [String:Any])["used"] as! Int==2)
precondition(try! Data(contentsOf:legacy)==before)
'''
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'test.swift').write_text(code);subprocess.run(['swiftc',str(root/'test.swift'),'-o',str(root/'test')],check=True,capture_output=True,timeout=60);subprocess.run([str(root/'test'),str(root)],check=True,capture_output=True,timeout=3)

 @unittest.skipUnless(sys.platform=='darwin' and shutil.which('swiftc'),'macOS Swift only')
 def test_startup_does_not_accept_stale_or_foreign_receipt(self):
  source=(P/'macos/AsukaLauncher.swift').read_text();part=source.split('// STARTUP_RECEIPT_BEGIN')[1].split('// STARTUP_RECEIPT_END')[0]
  code='import Foundation\n'+part+\
   '\nlet now=Date(timeIntervalSince1970:100)\nprecondition(StartupReceipt.entered(["entry_pid":11,"at":100.0,"status":"starting"],since:now,pid:11))\nprecondition(!StartupReceipt.entered(["entry_pid":11,"at":99.0,"status":"ready"],since:now,pid:11))\nprecondition(!StartupReceipt.entered(["entry_pid":12,"at":101.0,"status":"ready"],since:now,pid:11))\nprecondition(!StartupReceipt.entered(["entry_pid":11,"at":101.0,"status":"other"],since:now,pid:11))\n'
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'test.swift').write_text(code);subprocess.run(['swiftc',str(root/'test.swift'),'-o',str(root/'test')],check=True,capture_output=True,timeout=60);subprocess.run([str(root/'test')],check=True,capture_output=True,timeout=3)



 @unittest.skipUnless(sys.platform=='darwin' and shutil.which('swiftc'),'macOS Swift only')
 def test_empty_snapshot_reaches_returned_chat_and_receipts(self):
  source=(P/'macos/desktop_pet.swift').read_text()
  start=source.index('    private func pollSharedReminders()');end=source.index('    private func reminderMessage',start)
  polling=source[start:end].replace('private func pollSharedReminders','func pollSharedReminders')
  code='''import Foundation
final class Web {
 var snapshots=[[String:Any]]()
 func evaluateJavaScript(_ script:String,completionHandler:((Any?,Error?)->Void)?=nil){
  if script.hasPrefix("!!"){completionHandler?(true,nil);return}
  let prefix="window.dispatchEvent(new CustomEvent('native-reminder-snapshot',{detail:"
  let json=String(script.dropFirst(prefix.count).dropLast(3))
  if let data=json.data(using:.utf8),let value=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any]{snapshots.append(value)}
  completionHandler?(nil,nil)
 }
}
final class Driver {
 var bubbleMode=true,reminderReadPending=false,reminderPresented=false
 var reminderRows=[[String:Any]](),reminderReceiptIDs=Set<String>(),reminderSnapshotHash:Data?
 let reminderIO=DispatchQueue(label:"isolated-reminder-test"),reminderRoot:URL
 var webView:Web?;var creates=0
 init(_ root:URL){reminderRoot=root}
 func ensureChatWindow(){creates+=1;if webView==nil{webView=Web()}}
 func trustedReminder(_ r:[String:Any])->Bool{return true} // isolated trusted native snapshot fixture
'''+polling+'''
}
let root=URL(fileURLWithPath:CommandLine.arguments[1]);try! FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
func save(_ object:[String:Any],_ name:String){try! JSONSerialization.data(withJSONObject:object).write(to:root.appendingPathComponent(name),options:.atomic)}
func pulse(_ d:Driver){d.pollSharedReminders();let until=Date().addingTimeInterval(2);while d.reminderReadPending && Date()<until{RunLoop.current.run(until:Date().addingTimeInterval(0.002))};precondition(!d.reminderReadPending)}
func snapshot(_ rows:[[String:Any]]){save(["protocol":2,"at":Date().timeIntervalSince1970,"rows":rows,"focus":""],"shared-presentation.json")}
let untouched=Driver(root);snapshot([]);pulse(untouched);precondition(untouched.creates==0,"no reminder must not create chat window")
let driver=Driver(root);snapshot([["id":"isolated-A"]]);pulse(driver);precondition(driver.webView!.snapshots.count==1)
snapshot([]);pulse(driver);precondition(driver.webView!.snapshots.count==2,"returned chat must receive terminal empty snapshot")
precondition((driver.webView!.snapshots.last!["rows"] as! [[String:Any]]).isEmpty)
let op=String(repeating:"b",count:32);driver.reminderReceiptIDs.insert(op);save(["status":"saved","action":"dismiss"],op+".action.result.json")
pulse(driver);let receipts=driver.webView!.snapshots.last!["receipts"] as! [String:Any];precondition(receipts[op] != nil,"empty snapshot must deliver final receipt")
'''
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'test.swift').write_text(code);subprocess.run(['swiftc',str(root/'test.swift'),'-o',str(root/'test')],check=True,capture_output=True,timeout=60);result=subprocess.run([str(root/'test'),str(root/'ipc')],capture_output=True,timeout=8);self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace')[:900])



 @unittest.skipUnless(sys.platform=='darwin' and shutil.which('swiftc'),'macOS Swift only')
 def test_expired_action_returns_failure_without_calendar_operation(self):
  source=(P/'macos/desktop_pet.swift').read_text();start=source.index('    private func reminderMessage');end=source.index('\n    }',start)+6
  method=source[start:end];method=method[method.index('        guard kind=="reminder_action"'):];method=method.rsplit('}',1)[0]
  code='''import Foundation
final class Driver {
 let reminderRoot:URL,reminderIO=DispatchQueue(label:"isolated-action")
 var reminderReceiptIDs=Set<String>(),reminderRows=[[String:Any]]()
 init(_ root:URL){reminderRoot=root}
 func pollSharedReminders(){}
 func trustedReminder(_ row:[String:Any])->Bool{return false}
 func handle(_ data:[String:Any],_ kind:String){
'''+method+'''
 }
}
let root=URL(fileURLWithPath:CommandLine.arguments[1]);try! FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
let driver=Driver(root),op=String(repeating:"a",count:32)
driver.reminderRows=[["id":String(repeating:"b",count:64),"version":1,"delivery_id":String(repeating:"b",count:32)]]
driver.handle(["id":String(repeating:"c",count:64),"version":1,"delivery_id":String(repeating:"c",count:32),"operation_id":op,"action":"dismiss"],"reminder_action")
driver.reminderIO.sync{}
let receipt=root.appendingPathComponent(op+".action.result.json")
precondition(FileManager.default.fileExists(atPath:receipt.path),"stale click must resolve failure, not leave saving forever")
let result=try! JSONSerialization.jsonObject(with:Data(contentsOf:receipt)) as! [String:Any]
precondition(result["status"] as? String=="failed")
precondition(!FileManager.default.fileExists(atPath:root.appendingPathComponent(op+".action.json").path),"no backend/calendar write")
precondition(driver.reminderRows.count==1,"other reminder preserved")
'''
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'test.swift').write_text(code);subprocess.run(['swiftc',str(root/'test.swift'),'-o',str(root/'test')],check=True,capture_output=True,timeout=60);result=subprocess.run([str(root/'test'),str(root/'ipc')],capture_output=True,timeout=8);self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace')[:900])

if __name__=='__main__':unittest.main()
