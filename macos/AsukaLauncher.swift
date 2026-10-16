import AppKit
import EventKit
import CryptoKit


// CALENDAR_RANGE_BEGIN
struct CalendarRange {
    static func interval(now:Date, offset:Int, period:String, calendar:Calendar) -> (Date,Date)? {
        guard (0...7).contains(offset), ["day","morning","afternoon","evening"].contains(period),
              let start=calendar.date(byAdding:.day,value:offset,to:calendar.startOfDay(for:now)),
              let tomorrow=calendar.date(byAdding:.day,value:1,to:start) else { return nil }
        let hours=period == "morning" ? (0,12) : period == "afternoon" ? (12,18) : period == "evening" ? (18,24) : (0,24)
        let from=hours.0 == 0 ? start : calendar.date(bySettingHour:hours.0,minute:0,second:0,of:start)!
        let end=hours.1 == 24 ? tomorrow : calendar.date(bySettingHour:hours.1,minute:0,second:0,of:start)!
        return (from,end)
    }
    static func interval(date:String,period:String,calendar:Calendar)->(Date,Date)? {
        let formatter=DateFormatter();formatter.locale=Locale(identifier:"en_US_POSIX");formatter.calendar=calendar;formatter.timeZone=calendar.timeZone;formatter.dateFormat="yyyy-MM-dd";formatter.isLenient=false
        guard date.count==10,let day=formatter.date(from:date),formatter.string(from:day)==date else{return nil}
        return interval(now:day,offset:0,period:period,calendar:calendar)
    }

}
// CALENDAR_RANGE_END
final class CalendarReadBridge {
    let root:URL
    let store=EKEventStore()
    var timer:Timer?
    var processing=Set<String>()
    var wasGranted=false
    var requestingPermission=false
    init(_ project:URL) {
        root=project.appendingPathComponent(".runtime/calendar-ipc",isDirectory:true)
        try? FileManager.default.createDirectory(at:root,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
        wasGranted=FileManager.default.fileExists(atPath:root.appendingPathComponent("previously-allowed").path)
        timer=Timer.scheduledTimer(withTimeInterval:0.3,repeats:true){[weak self] _ in self?.tick()}
        DispatchQueue.main.async { [weak self] in self?.tick() }
    }
    func save(_ object:[String:Any],_ file:URL) {
        guard let data=try? JSONSerialization.data(withJSONObject:object) else{return}
        try? data.write(to:file,options:.atomic);try? FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:file.path)
    }
    func tick() {
        save(["pid":ProcessInfo.processInfo.processIdentifier,"bundle":Bundle.main.bundleIdentifier ?? "","version":5],root.appendingPathComponent("ready.json"))
        // Read only the explicit mailbox. Directory enumeration may block macOS launch.
        let mailbox=root.appendingPathComponent("queue.json")
        let ids=(try? Data(contentsOf:mailbox)).flatMap {try? JSONSerialization.jsonObject(with:$0)} as? [String] ?? []
        let names=ids.suffix(256).filter{$0.count==32 && $0.allSatisfy({$0.isHexDigit})}.map{$0+".request.json"}
        for name in names where name.hasSuffix(".request.json") {
            let file=root.appendingPathComponent(name)
            let id=String(file.lastPathComponent.dropLast(".request.json".count))
            guard id.count==32,id.allSatisfy({$0.isHexDigit}),!processing.contains(id),
                  let data=try? Data(contentsOf:file),data.count<8192,
                  let q=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any],q["id"] as? String==id,
                  let created=q["created"] as? Double,abs(Date().timeIntervalSince1970-created)<30 else{continue}
            let output=root.appendingPathComponent(id+".response.json")
            guard !FileManager.default.fileExists(atPath:output.path) else{continue}
            processing.insert(id);respond(id,q,output);processing.remove(id)
        }
    }
    func respond(_ id:String,_ q:[String:Any],_ file:URL) {
        let status=EKEventStore.authorizationStatus(for:.event)
        if status == .notDetermined {
            save(["id":id,"status":"authorization_required"],file)
            guard !requestingPermission else{return};requestingPermission=true
            if #available(macOS 14.0,*) {
                store.requestFullAccessToEvents { [weak self] granted,_ in
                    DispatchQueue.main.async {self?.requestingPermission=false;if granted {self?.rememberAllowed()} }
                }
            } else { store.requestAccess(to:.event) { [weak self] granted,_ in
                DispatchQueue.main.async {self?.requestingPermission=false;if granted {self?.rememberAllowed()} }
            }}
            return
        }
        if status == .denied {save(["id":id,"status":wasGranted ? "revoked":"denied"],file);return}
        if status == .restricted {save(["id":id,"status":"restricted"],file);return}
        if #available(macOS 14.0,*),status == .writeOnly {save(["id":id,"status":"write_only"],file);return}
        let allowed:Bool
        if #available(macOS 14.0,*) { allowed=status == .fullAccess } else { allowed=status == .authorized }
        guard allowed else {save(["id":id,"status":"read_error"],file);return}
        rememberAllowed()
        if let action=q["action"] as? String,action.hasPrefix("update") {updateAction(id,q,file);return}
        if let action=q["action"] as? String,action != "read" {createAction(id,q,file);return}
        let interval:(Date,Date)?
        if let date=q["date"] as? String,let period=q["period"] as? String {
            interval=CalendarRange.interval(date:date,period:period,calendar:.autoupdatingCurrent)
        } else if let offset=q["day_offset"] as? Int,let period=q["period"] as? String {
            interval=CalendarRange.interval(now:Date(),offset:offset,period:period,calendar:.autoupdatingCurrent)
        } else { interval=nil }
        guard let interval=interval else {save(["id":id,"status":"read_error","message":"日历查询日期无效"],file);return}
        // A fresh store/query per request reads the current local database; no chat-summary cache.
        let reader=EKEventStore(),formatter=ISO8601DateFormatter();formatter.timeZone = .autoupdatingCurrent
        let predicate=reader.predicateForEvents(withStart:interval.0,end:interval.1,calendars:nil)
        let events=reader.events(matching:predicate).filter{$0.startDate < interval.1 && $0.endDate > interval.0}.sorted{ $0.startDate == $1.startDate ? ($0.title ?? "") < ($1.title ?? "") : $0.startDate < $1.startDate }
        let rows:[[String:Any]]=events.prefix(5000).map{["title":String(($0.title ?? "无标题").prefix(200)),"start":formatter.string(from:$0.startDate),"end":formatter.string(from:$0.endDate),"all_day":$0.isAllDay,"event_id":$0.eventIdentifier ?? $0.calendarItemIdentifier,"calendar_id":$0.calendar.calendarIdentifier,"calendar_title":$0.calendar.title]}
        save(["id":id,"status":"ok","timezone":TimeZone.autoupdatingCurrent.identifier,"start":formatter.string(from:interval.0),"end":formatter.string(from:interval.1),"events":rows,"total":events.count,"partial":events.count>5000,"calendars_scope":"all_authorized"],file)
    }
    // Creation is available only through a confirmed local IPC operation. No alarms/recurrence.
    func createAction(_ id:String,_ q:[String:Any],_ file:URL) {
        func reply(_ row:[String:Any]) {var result=row;result["id"]=id;save(result,file)}
        let formatter=ISO8601DateFormatter()
        guard let action=q["action"] as? String,["catalog","create","reconcile"].contains(action),
              let data=q["event"] as? [String:Any],let title=data["title"] as? String,!title.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty,title.count<=200,
              let a=data["start"] as? String,let b=data["end"] as? String,
              let start=formatter.date(from:a),let end=formatter.date(from:b),end>start,
              let tz=data["timezone"] as? String,let zone=TimeZone(identifier:tz),
              let allDay=data["all_day"] as? Bool,
              let alarms=data["alarms"] as? [Any],alarms.isEmpty else {reply(["status":"invalid","message":"日程参数无效，未写入"]);return}
        formatter.timeZone=zone
        let calendars=store.calendars(for:.event).filter{$0.allowsContentModifications}
        let predicate=store.predicateForEvents(withStart:start.addingTimeInterval(-86400),end:end.addingTimeInterval(86400),calendars:nil)
        let candidates=store.events(matching:predicate)
        let normalizedTitle=title.trimmingCharacters(in:.whitespacesAndNewlines).lowercased()
        let similar=candidates.filter { event in
            let other=(event.title ?? "").trimmingCharacters(in:.whitespacesAndNewlines).lowercased()
            let related=other==normalizedTitle || (min(other.count,normalizedTitle.count)>=2 && (other.contains(normalizedTitle) || normalizedTitle.contains(other)))
            return related && abs(event.startDate.timeIntervalSince(start))<86400
        }
        if action == "catalog" {
            let rows:[[String:Any]]=calendars.map{["id":$0.calendarIdentifier,"title":$0.title,"default":$0.calendarIdentifier==store.defaultCalendarForNewEvents?.calendarIdentifier]}
            let duplicates:[[String:Any]]=similar.prefix(20).map{["title":$0.title ?? "","start":formatter.string(from:$0.startDate),"end":formatter.string(from:$0.endDate)]}
            reply(["status":"ok","calendars":rows,"duplicates":duplicates]);return
        }
        guard let op=q["operation_id"] as? String,op.count==32,op.allSatisfy({$0.isHexDigit}),
              let cid=q["calendar_id"] as? String else {reply(["status":"invalid"]);return}
        let journal=root.appendingPathComponent("writes",isDirectory:true)
        do {try FileManager.default.createDirectory(at:journal,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])}
        catch {reply(["status":"write_error","message":"无法保存防重复记录，未写入"]);return}
        let record=journal.appendingPathComponent(op+".json"), marker=URL(string:"local-file-chat://calendar/"+op)!
        let old=(try? Data(contentsOf:record)).flatMap{try? JSONSerialization.jsonObject(with:$0)} as? [String:Any]
        func verified(_ event:EKEvent)->[String:Any] {
            guard event.title==title,abs(event.startDate.timeIntervalSince(start))<1,abs(event.endDate.timeIntervalSince(end))<1,event.isAllDay==allDay,event.calendar.calendarIdentifier==cid,(event.alarms ?? []).isEmpty,(event.recurrenceRules ?? []).isEmpty,
                  let eid=event.eventIdentifier,!eid.isEmpty else {return ["status":"uncertain","message":"读回内容与确认预览不一致，请在系统日历核对，不会自动重写"]}
            var returned=data;returned["start"]=formatter.string(from:event.startDate);returned["end"]=formatter.string(from:event.endDate)
            return ["status":"verified","event_id":eid,"event":returned]
        }
        if let previous=old {
            if let eid=previous["event_id"] as? String,let event=store.event(withIdentifier:eid) {reply(verified(event));return}
            if let existing=candidates.first(where:{$0.url==marker}) {let result=verified(existing);save(result,record);reply(result);return}
            if previous["status"] as? String == "write_error" {reply(previous);return}
            reply(["status":"uncertain","message":"原操作结果尚不能核对；不会再次写入，请检查系统日历"]);return
        }
        if action == "reconcile" {
            if let event=candidates.first(where:{$0.url==marker}) {let result=verified(event);save(result,record);reply(result)}
            else {reply(["status":"uncertain","message":"未找到原操作记录；不会重新写入"])}
            return
        }
        guard let calendar=calendars.first(where:{$0.calendarIdentifier==cid}) else {reply(["status":"write_error","message":"目标日历已不可写，请重新预览"]);return}
        if !allDay && start <= Date() {reply(["status":"write_error","message":"开始时间已过去，请重新预览"]);return}
        if !similar.isEmpty && q["accept_duplicate"] as? Bool != true {reply(["status":"duplicate_confirmation_required","message":"发现可能重复事件，请重新预览并明确选择"]);return}
        // Fail closed unless the reservation is on disk before EventKit.save.
        do {
            let reservation=try JSONSerialization.data(withJSONObject:["status":"writing","event":data,"calendar_id":cid])
            try reservation.write(to:record,options:.atomic)
            try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:record.path)
        } catch {reply(["status":"write_error","message":"防重复记录未保存，未写入"]);return}
        let event=EKEvent(eventStore:store);event.title=title;event.startDate=start;event.endDate=end;event.isAllDay=allDay;event.timeZone=zone;event.calendar=calendar;event.url=marker;event.alarms=[];event.recurrenceRules=nil
        do {
            try store.save(event,span:.thisEvent,commit:true)
            guard let eid=event.eventIdentifier,let readback=store.event(withIdentifier:eid) else {
                let result:[String:Any]=["status":"uncertain","message":"保存已返回但读回尚不可用，请核对原操作，不会重写"];save(result,record);reply(result);return
            }
            let result=verified(readback);save(result,record);reply(result)
        } catch {
            // Some store failures may have committed. Keep the operation reserved, reconcile only.
            let result:[String:Any]=["status":"uncertain","message":"系统保存未明确完成，请核对原操作，不会自动重写","error_domain":(error as NSError).domain,"error_code":(error as NSError).code]
            save(result,record);reply(result)
        }
    }
    // Single-event update only. A fresh EventKit store per action and a durable
    // reservation prevent delete/recreate, blind retries and stale confirmations.
    func updateAction(_ id:String,_ q:[String:Any],_ file:URL) {
        func reply(_ row:[String:Any]) {var r=row;r["id"]=id;save(r,file)}
        let reader=EKEventStore(),formatter=ISO8601DateFormatter();formatter.timeZone = .autoupdatingCurrent
        guard let action=q["action"] as? String,let eid=q["event_id"] as? String,!eid.isEmpty,eid.count<2048 else {reply(["status":"invalid"]);return}
        func hash(_ value:[String:Any])->String {
            guard let bytes=try? JSONSerialization.data(withJSONObject:value,options:[.sortedKeys]) else{return ""}
            return SHA256.hash(data:bytes).map{String(format:"%02x",$0)}.joined()
        }
        func preserved(_ event:EKEvent)->String {
            let alarms:[[String:Any]]=(event.alarms ?? []).map { alarm in
                ["offset":alarm.relativeOffset,"absolute":alarm.absoluteDate?.timeIntervalSince1970 ?? -1,"proximity":alarm.proximity.rawValue,"location":alarm.structuredLocation?.title ?? "","radius":alarm.structuredLocation?.radius ?? 0]
            }
            return hash(["notes":event.notes ?? "","location":event.location ?? "","url":event.url?.absoluteString ?? "","timezone":event.timeZone?.identifier ?? "","alarms":alarms,"calendar_id":event.calendar.calendarIdentifier,"all_day":event.isAllDay,"availability":event.availability.rawValue])
        }
        func row(_ event:EKEvent)->[String:Any] {
            let stable=preserved(event)
            let version=hash(["preserved":stable,"title":event.title ?? "","start":event.startDate.timeIntervalSince1970,"end":event.endDate.timeIntervalSince1970,"modified":event.lastModifiedDate?.timeIntervalSince1970 ?? -1,"recurring":!(event.recurrenceRules ?? []).isEmpty || event.isDetached,"attendees":!(event.attendees ?? []).isEmpty || event.organizer != nil,"writable":event.calendar.allowsContentModifications])
            return ["event_id":event.eventIdentifier ?? "","title":event.title ?? "无标题","start":formatter.string(from:event.startDate),"end":formatter.string(from:event.endDate),"timezone":TimeZone.autoupdatingCurrent.identifier,"calendar_id":event.calendar.calendarIdentifier,"calendar_title":event.calendar.title,"all_day":event.isAllDay,"writable":event.calendar.allowsContentModifications,"recurring":!(event.recurrenceRules ?? []).isEmpty || event.isDetached,"has_attendees":!(event.attendees ?? []).isEmpty || event.organizer != nil,"version":version,"preserved_version":stable]
        }
        func suitable(_ e:EKEvent)->Bool {e.calendar.allowsContentModifications && !e.isAllDay && (e.recurrenceRules ?? []).isEmpty && !e.isDetached && (e.attendees ?? []).isEmpty && e.organizer == nil}
        let target=q["event"] as? [String:Any] ?? [:]
        let start=(target["start"] as? String).flatMap{formatter.date(from:$0)},end=(target["end"] as? String).flatMap{formatter.date(from:$0)}
        func conflicts()->([[String:Any]],String) {
            guard let a=start,let b=end,b>a else{return ([],hash(["conflicts":[]]))}
            let predicate=reader.predicateForEvents(withStart:a,end:b,calendars:nil)
            let others=reader.events(matching:predicate).filter{ $0.eventIdentifier != eid && $0.startDate<b && $0.endDate>a }.sorted{let a=($0.eventIdentifier ?? "")+String($0.startDate.timeIntervalSince1970)+String($0.endDate.timeIntervalSince1970);let b=($1.eventIdentifier ?? "")+String($1.startDate.timeIntervalSince1970)+String($1.endDate.timeIntervalSince1970);return a<b}
            let rows:[[String:Any]]=others.map { e in
                var r=row(e);r["advisory"]=e.isAllDay;r["overlap_start"]=formatter.string(from:max(a,e.startDate));r["overlap_end"]=formatter.string(from:min(b,e.endDate));return r
            }
            return (rows,hash(["conflicts":rows.map{["id":$0["event_id"]!,"version":$0["version"]!]}]))
        }
        if action == "update_check" {
            guard let original=reader.event(withIdentifier:eid) else{reply(["status":"not_found","message":"原事件已删除或身份不可读，请重新查询"]);return}
            let (other,version)=conflicts();reply(["status":"ok","event":row(original),"conflicts":other,"conflict_version":version]);return
        }
        guard ["update","update_reconcile"].contains(action),let a=start,let b=end,b>a,
              let op=q["operation_id"] as? String,op.count==32,op.allSatisfy({$0.isHexDigit}),
              let changes=q["changes"] as? [String:Any],Set(changes.keys).isSubset(of:Set(["title","start","end"])),
              let expected=q["expected_version"] as? String,let expectedConflicts=q["expected_conflict_version"] as? String else {reply(["status":"invalid","message":"修改参数无效"]);return}
        let journal=root.appendingPathComponent("updates",isDirectory:true),record=journal.appendingPathComponent(op+".json")
        do {try FileManager.default.createDirectory(at:journal,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])}
        catch {reply(["status":"write_error","message":"防重复记录不可写，未修改"]);return}
        let exists=FileManager.default.fileExists(atPath:record.path)
        let old=(try? Data(contentsOf:record)).flatMap{try? JSONSerialization.jsonObject(with:$0)} as? [String:Any]
        func verify(_ event:EKEvent)->[String:Any] {
            let r=row(event)
            guard event.eventIdentifier==eid,event.title==target["title"] as? String,abs(event.startDate.timeIntervalSince(a))<1,abs(event.endDate.timeIntervalSince(b))<1,r["preserved_version"] as? String == target["preserved_version"] as? String else{return ["status":"uncertain","message":"原事件身份、字段或保留字段读回不一致，不会再次写入"]}
            return ["status":"verified","event_id":eid,"event":r]
        }
        if exists || action == "update_reconcile" {
            guard let old=old,old["event_id"] as? String==eid,old["expected_version"] as? String==expected else {reply(["status":"uncertain","message":"原操作记录缺失或不匹配，不会再次修改"]);return}
            if let event=reader.event(withIdentifier:eid) {reply(verify(event))}
            else {reply(["status":"uncertain","message":"原事件无法核对，不会重写"])}
            return
        }
        guard let event=reader.event(withIdentifier:eid) else{reply(["status":"stale","message":"原事件已删除，请重新查询"]);return}
        guard suitable(event) else{reply(["status":"write_error","message":"只读、全天、重复或含与会者的事件本轮不支持修改"]);return}
        let (_,currentConflicts)=conflicts()
        guard row(event)["version"] as? String==expected,currentConflicts==expectedConflicts else {reply(["status":"stale","message":"保存前原事件或冲突发生变化，请重新预览"]);return}
        guard a>Date(),let title=target["title"] as? String,!title.isEmpty,title.count<=200 else{reply(["status":"invalid","message":"时间已过或标题无效，未修改"]);return}
        // Persist before EventKit.save; if the response is lost, reconcile the
        // same identity/target only. Never call save again for this operation.
        do {
            let bytes=try JSONSerialization.data(withJSONObject:["status":"writing","event_id":eid,"expected_version":expected,"event":target])
            try bytes.write(to:record,options:.atomic);try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:record.path)
        } catch {reply(["status":"write_error","message":"防重复记录未保存，未修改"]);return}
        if changes["title"] != nil {event.title=title}
        if changes["start"] != nil {event.startDate=a}
        if changes["end"] != nil {event.endDate=b}
        // Do not assign calendar, notes, location, URL, timeZone or alarms.
        do {
            try reader.save(event,span:.thisEvent,commit:true)
            let verifier=EKEventStore()
            guard let actual=verifier.event(withIdentifier:eid) else{reply(["status":"uncertain","message":"已保存但尚未读回，勿重复修改"]);return}
            reply(verify(actual))
        } catch {reply(["status":"uncertain","message":"EventKit保存未明确完成，只核对原操作，不自动重试","error_domain":(error as NSError).domain,"error_code":(error as NSError).code])}
    }

    func rememberAllowed() {
        wasGranted=true
        let file=root.appendingPathComponent("previously-allowed")
        if !FileManager.default.fileExists(atPath:file.path) {try? Data().write(to:file);try? FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:file.path)}
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var worker: Process?
    private var calendarBridge: CalendarReadBridge?
    private var stopObserver: NSObjectProtocol?
    func applicationDidFinishLaunching(_ notification: Notification) {
        if let path=Bundle.main.object(forInfoDictionaryKey:"AsukaProjectPath") as? String {calendarBridge=CalendarReadBridge(URL(fileURLWithPath:path))}
        stopObserver=DistributedNotificationCenter.default().addObserver(forName:NSNotification.Name("local.filechat.asuka.launcher.stop"),object:nil,queue:.main){_ in NSApp.terminate(nil)}
        launch()
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if worker?.isRunning != true { launch() }
        return false
    }
    private func failure(_ message: String) {
        let alert=NSAlert(); alert.messageText="明日香助手启动失败"
        alert.informativeText=message;alert.alertStyle = .warning
        NSApp.activate(ignoringOtherApps:true);alert.runModal();NSApp.terminate(nil)
    }
    private func launch() {
        guard let project=Bundle.main.object(forInfoDictionaryKey:"AsukaProjectPath") as? String else { failure("入口缺少项目位置，请用项目脚本重新生成。");return }
        let root=URL(fileURLWithPath:project), python=root.appendingPathComponent(".venv/bin/python")
        let script=root.appendingPathComponent("macos/desktop_entry.py"), runtime=root.appendingPathComponent(".runtime",isDirectory:true)
        guard FileManager.default.fileExists(atPath:python.path), FileManager.default.fileExists(atPath:script.path) else { failure("找不到项目虚拟环境或启动程序。项目位置：\(project)");return }
        do {
            try FileManager.default.createDirectory(at:runtime,withIntermediateDirectories:true)
            let job=Process();job.executableURL=python;job.arguments=[script.path];job.currentDirectoryURL=root
            var environment=ProcessInfo.processInfo.environment;environment["TMPDIR"]=runtime.path+"/";job.environment=environment
            // The Python bootstrap writes only the existing command's output
            // to a private project log, not credentials or environment dumps.
            job.standardInput=FileHandle.nullDevice;job.standardOutput=FileHandle.nullDevice;job.standardError=FileHandle.nullDevice
            job.terminationHandler={ process in DispatchQueue.main.async {
                if process.terminationStatus==0 { /* Keep the existing app identity for read-only EventKit requests. */ }
                else { self.failure("请查看项目内 .runtime/asuka-app.log。不会结束后端、Ollama 或其他程序。") }
            }}
            worker=job;try job.run()
        } catch { failure("无法执行项目启动程序，请检查目录权限和虚拟环境。") }
    }
}
let application=NSApplication.shared
let delegate=AppDelegate();application.delegate=delegate
application.setActivationPolicy(.accessory);application.run()
