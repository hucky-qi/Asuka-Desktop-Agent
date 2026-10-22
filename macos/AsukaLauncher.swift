import AppKit
import EventKit
import CryptoKit
import UserNotifications
import Speech
import AVFoundation


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
        timer=Timer.scheduledTimer(withTimeInterval:0.3,repeats:true){[weak self] _ in self?.pollMailbox()}
        DispatchQueue.main.async { [weak self] in self?.pollMailbox() }
    }
    func save(_ object:[String:Any],_ file:URL) {
        guard let data=try? JSONSerialization.data(withJSONObject:object) else{return}
        try? data.write(to:file,options:.atomic);try? FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:file.path)
    }
    private let mailboxQueue=DispatchQueue(label:"local.filechat.calendar-mailbox")
    private var mailboxReading=false
    func pollMailbox(){
        guard !mailboxReading else{return};mailboxReading=true
        mailboxQueue.async{[weak self] in guard let self else{return};self.tick();DispatchQueue.main.async{self.mailboxReading=false}}
    }
    func tick() {
        save(["pid":ProcessInfo.processInfo.processIdentifier,"bundle":Bundle.main.bundleIdentifier ?? "","version":6],root.appendingPathComponent("ready.json"))
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
        if q["action"] as? String == "permission_status" {
            let state:String
            if status == .notDetermined {state="authorization_required"}
            else if status == .denied {state="denied"}
            else if status == .restricted {state="restricted"}
            else if #available(macOS 14.0,*),status == .writeOnly {state="write_only"}
            else {state="allowed"}
            save(["id":id,"status":state,"source":"EventKit.authorizationStatus","previously_allowed_marker":wasGranted,"bundle":Bundle.main.bundleIdentifier ?? ""],file);return
        }
        if q["action"] as? String == "reminder_check",status == .notDetermined {save(["id":id,"status":"authorization_required"],file);return}
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
        if let action=q["action"] as? String,(action.hasPrefix("update") || action.hasPrefix("delete") || action == "reminder_check") {updateAction(id,q,file);return}
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
            let local=event.calendar.type == .local && event.calendar.source?.sourceType == .local && !event.calendar.isSubscribed
            let version=hash(["preserved":stable,"title":event.title ?? "","start":event.startDate.timeIntervalSince1970,"end":event.endDate.timeIntervalSince1970,"modified":event.lastModifiedDate?.timeIntervalSince1970 ?? -1,"recurring":!(event.recurrenceRules ?? []).isEmpty || event.isDetached,"attendees":!(event.attendees ?? []).isEmpty || event.organizer != nil,"writable":event.calendar.allowsContentModifications])
            return ["reminder_supported":local && !event.isAllDay && (event.recurrenceRules ?? []).isEmpty && !event.isDetached && (event.attendees ?? []).isEmpty && event.organizer == nil,"shared":local ? false as Any : NSNull() as Any,"calendar_type":event.calendar.type.rawValue,"event_id":event.eventIdentifier ?? "","title":event.title ?? "无标题","start":formatter.string(from:event.startDate),"end":formatter.string(from:event.endDate),"timezone":TimeZone.autoupdatingCurrent.identifier,"calendar_id":event.calendar.calendarIdentifier,"calendar_title":event.calendar.title,"all_day":event.isAllDay,"writable":event.calendar.allowsContentModifications,"recurring":!(event.recurrenceRules ?? []).isEmpty || event.isDetached,"has_attendees":!(event.attendees ?? []).isEmpty || event.organizer != nil,"version":version,"preserved_version":stable,"delete_supported":event.calendar.type == .local && !event.isAllDay && event.calendar.allowsContentModifications && (event.recurrenceRules ?? []).isEmpty && !event.isDetached && (event.attendees ?? []).isEmpty && event.organizer == nil]
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
        if action == "update_check" || action == "reminder_check" {
            guard let original=reader.event(withIdentifier:eid) else{reply(["status":"not_found","message":"原事件已删除或身份不可读，请重新查询"]);return}
            guard original.eventIdentifier==eid else{reply(["status":"not_found","message":"事件身份返回不一致，已阻止操作"]);return}
            let (other,version)=conflicts();reply(["status":"ok","event":row(original),"conflicts":other,"conflict_version":version]);return
        }
        if action == "delete" || action == "delete_reconcile" {
            guard let op=q["operation_id"] as? String,op.count==32,op.allSatisfy({$0.isHexDigit}),
                  let expected=q["expected_version"] as? String,let cid=q["calendar_id"] as? String else {reply(["status":"invalid"]);return}
            let directory=root.appendingPathComponent("deletes",isDirectory:true),record=directory.appendingPathComponent(op+".json")
            do {try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])}
            catch {reply(["status":"write_error","message":"无法保存删除防重复记录，未删除"]);return}
            let exists=FileManager.default.fileExists(atPath:record.path)
            let previous=(try? Data(contentsOf:record)).flatMap{try? JSONSerialization.jsonObject(with:$0)} as? [String:Any]
            if exists || action == "delete_reconcile" {
                guard let old=previous,old["event_id"] as? String==eid,old["calendar_id"] as? String==cid,old["expected_version"] as? String==expected else {reply(["status":"uncertain","message":"原删除记录不匹配，不会重试"]);return}
                if reader.event(withIdentifier:eid) == nil {
                    let result:[String:Any] = ["status":"verified","event_id":eid,"deleted":true]
                    save(result.merging(["calendar_id":cid,"expected_version":expected]){a,_ in a},record);reply(result)
                } else {reply(["status":"uncertain","message":"原事件仍可读取；不会自动再次删除，请人工核对"])}
                return
            }
            guard let event=reader.event(withIdentifier:eid),event.eventIdentifier==eid,event.calendar.calendarIdentifier==cid else {reply(["status":"stale","message":"原事件已删除或身份不一致，未删除"]);return}
            // EventKit does not reliably expose all sharing metadata. Initial scope is local-only.
            guard suitable(event),event.calendar.type == .local else {reply(["status":"write_error","message":"删除仅支持本机本地普通单次可写日历；云端、共享、全天、重复或会议暂不支持"]);return}
            guard row(event)["version"] as? String==expected else {reply(["status":"stale","message":"删除前事件已变化，请重新预览"]);return}
            do {
                let reservation=try JSONSerialization.data(withJSONObject:["status":"writing","event_id":eid,"calendar_id":cid,"expected_version":expected])
                try reservation.write(to:record,options:.atomic)
                try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:record.path)
            } catch {reply(["status":"write_error","message":"防重复记录未保存，未删除"]);return}
            do {
                try reader.remove(event,span:.thisEvent,commit:true)
                let verifier=EKEventStore()
                if verifier.event(withIdentifier:eid) == nil {
                    let result:[String:Any]=["status":"verified","event_id":eid,"calendar_id":cid,"expected_version":expected,"deleted":true]
                    save(result,record);reply(result)
                } else {reply(["status":"uncertain","message":"系统删除已返回但原事件仍可读，只能核对，不重复删除"])}
            } catch {reply(["status":"uncertain","message":"系统删除未明确完成；只核对原操作，不自动重试"])}
            return
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
        guard event.eventIdentifier==eid,event.calendar.calendarIdentifier==target["calendar_id"] as? String,suitable(event) else{reply(["status":"write_error","message":"只读、全天、重复或含与会者的事件本轮不支持修改"]);return}
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

// Local user notifications: original app identity only. No chat/model/tool callbacks.
final class ReminderNotificationBridge:NSObject,UNUserNotificationCenterDelegate {
    let root:URL,plans:URL
    let center=UNUserNotificationCenter.current()
    var timer:Timer?,seen=Set<String>()
    private let permissionLock=NSLock()
    private var permissionPending=false
    private var statusItem:NSStatusItem?
    private var pendingRows=[String:[String:Any]](),notificationFocus=""
    private var motionObserver:NSObjectProtocol?
    func endPermission(){permissionLock.lock();permissionPending=false;permissionLock.unlock()}
    init(_ project:URL) {
        root=project.appendingPathComponent(".runtime/reminder-ipc",isDirectory:true)
        plans=project.appendingPathComponent(".runtime/calendar-reminders/plans.json")
        super.init();center.delegate=self
        motionObserver=DistributedNotificationCenter.default().addObserver(forName:NSNotification.Name("local.filechat.reminder.position"),object:nil,queue:.main){[weak self] _ in self?.refreshReminderBubble()}
        let token=(UUID().uuidString+UUID().uuidString).replacingOccurrences(of:"-",with:"").lowercased()
        try? FileManager.default.createDirectory(at:root,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
        save(["token":token,"pid":ProcessInfo.processInfo.processIdentifier],root.appendingPathComponent("test-capability.json"))
        ensureStatusItem()
        try? FileManager.default.createDirectory(at:root,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
        if let raw=try? Data(contentsOf:root.appendingPathComponent("pending-presentation.json")),let d=(try? JSONSerialization.jsonObject(with:raw)) as? [String:[String:Any]] {pendingRows=d}
        timer=Timer.scheduledTimer(withTimeInterval:0.5,repeats:true){[weak self] _ in self?.tick()}
    }
    func ensureStatusItem(){
        guard statusItem == nil else{return}
        let item=NSStatusBar.system.statusItem(withLength:22)
        var image:NSImage?
        if let name=Bundle.main.object(forInfoDictionaryKey:"CFBundleIconFile") as? String,let root=Bundle.main.resourceURL {image=NSImage(contentsOf:root.appendingPathComponent(name.hasSuffix(".icns") ? name:name+".icns"))}
        if image == nil {image=NSImage(named:NSImage.applicationIconName)}
        if let image {image.size=NSSize(width:18,height:18);image.isTemplate=false;item.button?.image=image}
        else {item.button?.title="明日香"}
        item.button?.toolTip="明日香小助手"
        let menu=NSMenu();let test=NSMenuItem(title:"测试提醒（不访问日历）",action:#selector(openTest),keyEquivalent:"");test.target=self;menu.addItem(test);item.menu=menu;statusItem=item
    }
    @objc func openTest(){
        guard let raw=try? Data(contentsOf:root.appendingPathComponent("test-capability.json")),let d=(try? JSONSerialization.jsonObject(with:raw)) as? [String:Any],let token=d["token"] as? String,
              let url=URL(string:"http://127.0.0.1:8765/reminder-notifications#"+token) else{return}
        NSWorkspace.shared.open(url)
    }
    func save(_ d:[String:Any],_ url:URL) {
        if let raw=try? JSONSerialization.data(withJSONObject:d) {try? raw.write(to:url,options:.atomic);try? FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:url.path)}
    }
    func current(_ r:[String:Any])->Bool {
        guard let id=r["id"] as? String,let version=r["version"] as? Int,let delivery=r["delivery_id"] as? String,
              let bytes=try? Data(contentsOf:r["synthetic"] as? Bool==true ? root.deletingLastPathComponent().appendingPathComponent("reminder-tests/plans.json"):plans),let store=(try? JSONSerialization.jsonObject(with:bytes)) as? [String:Any],
              let items=store["items"] as? [String:[String:Any]],let row=items[id],row["version"] as? Int==version,row["delivery_id"] as? String==delivery else{return false}
        if let text=row["start"] as? String,let start=ISO8601DateFormatter().date(from:text),start<=Date(){return false}
        return ["claimed","system_accepted","system_visible","user_viewed"].contains(row["status"] as? String ?? "")
    }
    func tick() {
        refreshReminderBubble()
        save(["bundle":Bundle.main.bundleIdentifier ?? "","protocol":1,"pid":ProcessInfo.processInfo.processIdentifier,"menu_created":statusItem != nil,"menu_image_attached":statusItem?.button?.image != nil,"menu_visible":statusItem?.isVisible ?? false],root.appendingPathComponent("ready.json"))
        let ids=(try? Data(contentsOf:root.appendingPathComponent("queue.json"))).flatMap{try? JSONSerialization.jsonObject(with:$0)} as? [String] ?? []
        for id in ids.suffix(256) where id.count==32 && id.allSatisfy({$0.isHexDigit}) {
            let file=root.appendingPathComponent(id+".request.json")
            guard let raw=try? Data(contentsOf:file),raw.count<8192,let q=(try? JSONSerialization.jsonObject(with:raw)) as? [String:Any],
                  let id=q["id"] as? String,id.count==32,id.allSatisfy({$0.isHexDigit}),!seen.contains(id),
                  let at=q["at"] as? Double,abs(Date().timeIntervalSince1970-at)<30,let action=q["action"] as? String else{continue}
            seen.insert(id);if seen.count>512{seen=Set(seen.suffix(256))}
            let response=root.appendingPathComponent(id+".response.json")
            func reply(_ d:[String:Any]){var d=d;d["id"]=id;self.save(d,response)}
            if action=="authorize" {
                guard q["explicit_user_action"] as? Bool==true else{reply(["status":"refused"]);continue}
                permissionLock.lock()
                if permissionPending {permissionLock.unlock();reply(["status":"authorization_pending"]);continue}
                permissionPending=true;permissionLock.unlock()
                center.getNotificationSettings { state in
                    guard state.authorizationStatus == .notDetermined else{self.endPermission();reply(["status":state.authorizationStatus == .authorized ? "authorized":"denied"]);return}
                    self.center.requestAuthorization(options:[.alert,.sound]) { granted,error in self.endPermission();reply(["status":granted ? "authorized":"denied","error":error != nil]) }
                };continue
            }
            if action=="cancel",let delivery=q["delivery_id"] as? String {
                center.removePendingNotificationRequests(withIdentifiers:[delivery]);center.removeDeliveredNotifications(withIdentifiers:[delivery]);if delivery.count==32 && delivery.allSatisfy({$0.isHexDigit}) {self.save(["delivery_id":delivery,"status":"cancelled"],root.appendingPathComponent(delivery+".delivery.json"))};reply(["status":"cancel_requested"]);continue
            }
            if action=="status" {center.getNotificationSettings { state in reply(["status":state.authorizationStatus == .authorized ? "authorized":state.authorizationStatus == .notDetermined ? "authorization_required":"denied"]) };continue}
            if action=="visible" {center.getDeliveredNotifications { rows in reply(["status":"ok","delivery_ids":rows.map{$0.request.identifier}]) };continue}
            guard action=="send" || action=="test" else{reply(["status":"unsupported"]);continue}
            let test=action=="test"
            let r=q["reminder"] as? [String:Any] ?? [:]
            guard test ? q["explicit_user_action"] as? Bool==true : current(r),let delivery=(test ? q["delivery_id"] : r["delivery_id"]) as? String,delivery.count==32,delivery.allSatisfy({$0.isHexDigit}) else{reply(["status":"stale"]);continue}
            let journal=root.appendingPathComponent(delivery+".delivery.json")
            if let old=try? Data(contentsOf:journal),let d=(try? JSONSerialization.jsonObject(with:old)) as? [String:Any] {reply(["status":d["status"] as? String ?? "unknown"]);continue}
            if test,let old=try? Data(contentsOf:root.appendingPathComponent("last-test.json")),let d=(try? JSONSerialization.jsonObject(with:old)) as? [String:Double],Date().timeIntervalSince1970-(d["at"] ?? 0)<60 {reply(["status":"test_throttled"]);continue}
            if test {self.save(["at":Date().timeIntervalSince1970],self.root.appendingPathComponent("last-test.json"))}
            if test || r["synthetic"] as? Bool==true {
                let budget=root.appendingPathComponent("delivery-test-budget-shared-reminder.json")
                let raw=try? Data(contentsOf:budget)
                if FileManager.default.fileExists(atPath:budget.path) && raw.flatMap({try? JSONSerialization.jsonObject(with:$0)}) as? [String:Any] == nil {reply(["status":"test_budget_unreadable"]);continue}
                var d=(raw.flatMap{try? JSONSerialization.jsonObject(with:$0)} as? [String:Any]) ?? ["used":0]
                let used=d["used"] as? Int ?? 3
                if d["closed"] as? Bool==true || used>=3 {reply(["status":"test_budget_exhausted"]);continue}
                d["used"]=used+1;save(d,budget)
            }
            center.getNotificationSettings { state in
                guard state.authorizationStatus == .authorized else {reply(["status":state.authorizationStatus == .notDetermined ? "authorization_required":"denied"]);return}
                if !test && !self.current(r){reply(["status":"stale"]);return}
                self.save(["status":"unknown","delivery_id":delivery],journal) // Reserve BEFORE calling system; no blind retry after crash.
                let content=UNMutableNotificationContent()
                content.title=test ? "明日香助手 · 测试通知" : (r["catchup"] as? Bool==true ? "补提醒 · ":"提醒 · ")+String((r["title"] as? String ?? "日程").prefix(160))
                content.body=test ? "这是明确标注的功能测试，不是真实日程。" : String((r["start"] as? String ?? "").prefix(60))+" · 距开始约\(r["minutes_until_start"] as? Int ?? 0)分钟"
                content.sound = .default
                content.userInfo=test ? ["test":true,"delivery_id":delivery] : ["reminder_id":r["id"] ?? "","version":r["version"] ?? 0,"delivery_id":delivery,"synthetic":r["synthetic"] as? Bool ?? false]
                self.center.add(UNNotificationRequest(identifier:delivery,content:content,trigger:nil)) { error in
                    let status=error==nil ? "system_accepted":"failed"
                    self.save(["status":status,"delivery_id":delivery],journal)
                    if error==nil && !test {DispatchQueue.main.async{var row=r;row["presentation_at"]=Date().timeIntervalSince1970;self.pendingRows[delivery]=row;self.persistPresentation();self.refreshReminderBubble()}}
                    reply(["status":status])
                }
            }
        }
    }
    func userNotificationCenter(_ center:UNUserNotificationCenter,willPresent notification:UNNotification,withCompletionHandler completionHandler:@escaping (UNNotificationPresentationOptions)->Void){completionHandler([.banner,.sound])}
    func userNotificationCenter(_ center:UNUserNotificationCenter,didReceive response:UNNotificationResponse,withCompletionHandler completionHandler:@escaping ()->Void) {
        let info=response.notification.request.content.userInfo
        if info["test"] as? Bool==true,let delivery=info["delivery_id"] as? String,delivery.count==32,delivery.allSatisfy({$0.isHexDigit}),
           let raw=try? Data(contentsOf:root.appendingPathComponent(delivery+".delivery.json")),let d=(try? JSONSerialization.jsonObject(with:raw)) as? [String:Any],d["status"] as? String=="system_accepted" {
            save(["delivery_id":delivery,"status":"user_viewed_test"],root.appendingPathComponent(delivery+".click.json"))
            // Legacy isolated notification records viewing only; no separate window.
        }
        if let id=info["reminder_id"] as? String,let v=info["version"] as? Int,let delivery=info["delivery_id"] as? String,id.count==64,delivery.count==32 {
            let r:[String:Any]=["id":id,"version":v,"delivery_id":delivery,"synthetic":info["synthetic"] as? Bool ?? false]
            if current(r){save(r,root.appendingPathComponent(delivery+".click.json"))
                DispatchQueue.main.async {self.notificationFocus=delivery+":"+UUID().uuidString;self.refreshReminderBubble()}
            }
        }
        completionHandler()
    }
    func persistPresentation(){save(pendingRows,root.appendingPathComponent("pending-presentation.json"))}
    func refreshReminderBubble(){
        for (id,r) in pendingRows where !current(r){pendingRows.removeValue(forKey:id)}
        persistPresentation()
        // Publication only. The original pet owns the ONE chat WKWebView/window.
        let rows=pendingRows.values.sorted{($0["presentation_at"] as? Double ?? 0)<($1["presentation_at"] as? Double ?? 0)}
        save(["protocol":2,"at":Date().timeIntervalSince1970,"pid":ProcessInfo.processInfo.processIdentifier,"rows":rows,"focus":notificationFocus],root.appendingPathComponent("shared-presentation.json"))
    }

}

// Explicit local recording only. No online recognizer, no transcript persistence.
@available(macOS 26.0, *)
final class LocalSpeechBridge {
    let root:URL
    var timer:Timer?,recorder:AVAudioRecorder?,job:Task<Void,Never>?
    var token="",state="idle",message="",text="",available=false,started=Date(),seen=""
    var temporary:URL?
    init(_ project:URL) {
        root=project.appendingPathComponent(".runtime/speech-ipc")
        try? FileManager.default.createDirectory(at:root,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
        // Clean only our own abandoned recordings, never user audio.
        for f in (try? FileManager.default.contentsOfDirectory(at:root,includingPropertiesForKeys:nil)) ?? [] where f.lastPathComponent.hasPrefix("capture-") && f.pathExtension=="caf" {try? FileManager.default.removeItem(at:f)}
        job=Task { @MainActor in
            do {
                guard SpeechTranscriber.isAvailable else {self.message="系统不支持中文本地转写。";return}
                let locale=Locale(identifier:"zh_CN")
                guard await SpeechTranscriber.installedLocales.contains(where:{$0.identifier=="zh_CN" || $0.identifier=="zh-CN"}) else {self.message="中文本地语音资产尚未安装。";return}
                _ = try await AssetInventory.reserve(locale:locale)
                let m=SpeechTranscriber(locale:locale,preset:.transcription)
                if let request=try await AssetInventory.assetInstallationRequest(supporting:[m]) {try await request.downloadAndInstall()}
                self.available = await AssetInventory.status(forModules:[m]) == .installed
            } catch {self.message="中文本地语音资产准备失败；不会转为云端。"}
        }
        timer=Timer.scheduledTimer(withTimeInterval:0.15,repeats:true){[weak self] _ in self?.tick()}
    }
    func save(_ d:[String:Any],_ url:URL){if let data=try? JSONSerialization.data(withJSONObject:d){try? data.write(to:url,options:.atomic);try? FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:url.path)}}
    func clearAudio(){recorder?.stop();recorder=nil;if let f=temporary{try? FileManager.default.removeItem(at:f)};temporary=nil}
    func cancel(){token="";job?.cancel();clearAudio();state="cancelled";text=""}
    func tick(){
        for f in (try? FileManager.default.contentsOfDirectory(at:root,includingPropertiesForKeys:[.contentModificationDateKey])) ?? [] where f.lastPathComponent.hasSuffix(".response.json") {
            if let at=(try? f.resourceValues(forKeys:[.contentModificationDateKey]))?.contentModificationDate,Date().timeIntervalSince(at)>5 {try? FileManager.default.removeItem(at:f)}
        }
        if ["requesting","recording","transcribing"].contains(state) && Date().timeIntervalSince(started)>150 {cancel();state="error";message="录音或转写超时，请重新录入。"}
        save(["pid":ProcessInfo.processInfo.processIdentifier,"bundle":Bundle.main.bundleIdentifier ?? "","protocol":1,"available":available,"message":message],root.appendingPathComponent("ready.json"))
        let file=root.appendingPathComponent("command.json")
        guard let data=try? Data(contentsOf:file),data.count<1024,let q=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any],let id=q["id"] as? String,id.count==32,id.allSatisfy({$0.isHexDigit}),id != seen,let at=q["at"] as? Double,abs(Date().timeIntervalSince1970-at)<4,let action=q["action"] as? String,let t=q["token"] as? String,t.count==32,t.allSatisfy({$0.isHexDigit}) else{return}
        seen=id;try? FileManager.default.removeItem(at:file)
        if action=="start" && available && !["recording","transcribing","requesting"].contains(state) {
            token=t;text="";message="";state="requesting";started=Date()
            AVCaptureDevice.requestAccess(for:.audio){[weak self] granted in DispatchQueue.main.async {
                guard let self,self.token==t,self.state=="requesting" else{return}
                guard granted else{self.state="error";self.message="麦克风未获允许；请到系统设置→隐私与安全性→麦克风允许原助手，再重新录音。";return}
                do {
                    let url=self.root.appendingPathComponent("capture-"+t+".caf");self.temporary=url
                    let r=try AVAudioRecorder(url:url,settings:[AVFormatIDKey:kAudioFormatLinearPCM,AVSampleRateKey:16000,AVNumberOfChannelsKey:1,AVLinearPCMBitDepthKey:16,AVLinearPCMIsFloatKey:false])
                    self.recorder=r
                    guard r.record(forDuration:120) else{throw NSError(domain:"MicUnavailable",code:1)}
                    self.state="recording"
                } catch {self.clearAudio();self.state="error";self.message="无法打开输入设备，请检查麦克风后重试。"}
            }}
        } else if t==token {
            if action=="cancel"{cancel()}
            else if action=="stop" && state=="recording"{transcribe(t)}
        } else {save(["status":"cancelled"],root.appendingPathComponent(id+".response.json"));return}
        var result:[String:Any]=["status":state,"message":message]
        if state=="complete"{result["text"]=text;text="";state="idle";token=""}
        save(result,root.appendingPathComponent(id+".response.json"))
    }
    func transcribe(_ t:String){
        recorder?.stop();recorder=nil;state="transcribing";started=Date()
        guard let file=temporary else{state="error";return}
        job=Task { @MainActor in
            let module=SpeechTranscriber(locale:Locale(identifier:"zh_CN"),preset:.transcription)
            let analyzer=SpeechAnalyzer(modules:[module])
            let collector=Task {() throws -> String in var result="";for try await row in module.results {try Task.checkCancellation();result += String(row.text.characters)};return result}
            do {
                try await withTaskCancellationHandler(operation:{
                    let input=try AVAudioFile(forReading:file)
                    try await analyzer.start(inputAudioFile:input,finishAfterFile:true)
                    let result=try await collector.value
                    try Task.checkCancellation()
                    if self.token==t{self.text=result.count<=6000 ? result:"";self.state=result.count>6000 ? "error":result.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty ? "error":"complete";self.message=self.state=="error" ? "没有识别到清晰语音，请重新录音。":""}
                },onCancel:{collector.cancel();Task{await analyzer.cancelAndFinishNow()}})
            } catch {if self.token==t{self.state="error";self.message="本地转写未完成，请重录或继续文字输入。"}}
            if self.token==t{self.clearAudio()}
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var worker: Process?
    private var calendarBridge: CalendarReadBridge?
    private var reminderBridge: ReminderNotificationBridge?
    private var speechBridge: AnyObject?
    private var stopObserver: NSObjectProtocol?
    func applicationDidFinishLaunching(_ notification: Notification) {
        if let path=Bundle.main.object(forInfoDictionaryKey:"AsukaProjectPath") as? String {if #available(macOS 26.0,*) {speechBridge=LocalSpeechBridge(URL(fileURLWithPath:path))};calendarBridge=CalendarReadBridge(URL(fileURLWithPath:path));reminderBridge=ReminderNotificationBridge(URL(fileURLWithPath:path))}
        stopObserver=DistributedNotificationCenter.default().addObserver(forName:NSNotification.Name("local.filechat.asuka.launcher.stop"),object:nil,queue:.main){_ in NSApp.terminate(nil)}
        launch()
    }
    func applicationWillTerminate(_ notification:Notification) {if #available(macOS 26.0,*) {(speechBridge as? LocalSpeechBridge)?.cancel()}}
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        reminderBridge?.ensureStatusItem()
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

extension URL {func withSuffixResult()->URL {URL(fileURLWithPath:deletingPathExtension().path+".result.json")}}
