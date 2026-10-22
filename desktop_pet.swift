import AppKit
import WebKit

// EDITING_MENU_BEGIN
/// Standard nil-target actions follow AppKit's responder chain into WKWebView.
/// No application clipboard access or JavaScript editing shortcuts.
enum AssistantEditingMenu {
    static func make() -> NSMenu {
        let root = NSMenu(title: "明日香助手")
        let application = NSMenuItem()
        application.submenu = NSMenu(title: "明日香助手")
        root.addItem(application)
        let editing = NSMenu(title: "编辑")
        editing.autoenablesItems = true
        let definitions: [(String, String, String, NSEvent.ModifierFlags)] = [
            ("撤销", "undo:", "z", .command),
            ("重做", "redo:", "z", [.command, .shift]),
            ("剪切", "cut:", "x", .command),
            ("复制", "copy:", "c", .command),
            ("粘贴", "paste:", "v", .command),
            ("全选", "selectAll:", "a", .command)
        ]
        for (title, selector, key, modifiers) in definitions {
            let item = NSMenuItem(title: title, action: NSSelectorFromString(selector), keyEquivalent: key)
            item.target = nil
            item.keyEquivalentModifierMask = modifiers
            editing.addItem(item)
        }
        let edit = NSMenuItem(title: "编辑", action: nil, keyEquivalent: "")
        edit.submenu = editing
        root.addItem(edit)
        return root
    }
}
// EDITING_MENU_END

// PANEL_VISIBILITY_BEGIN
struct PanelVisibility {
    var requested = false
    var localID = ""
    var localVersion = 0
    var pending = false
    var ignored = Set<String>()
    var rows: [[String: Any]] = []
    var busy: Bool { pending || rows.contains { !ignored.contains($0["request_id"] as? String ?? "") } }
    var visible: Bool { requested && !busy }
    var mode: String {
        let active = rows.filter { !ignored.contains($0["request_id"] as? String ?? "") }
        return active.contains { $0["phase"] as? String == "external_search" } ? "wifi" : busy ? "dots" : "none"
    }
    mutating func begin(_ id: String, _ version: Int) -> Bool {
        guard version > localVersion else { return false }
        if !localID.isEmpty { ignored.insert(localID) }
        localID = id; localVersion = version; pending = true
        if ignored.count > 64 { ignored = Set<String>() }
        return true
    }
    mutating func answerReady(_ id: String, _ version: Int) -> Bool {
        guard end(id, version) else { return false }
        requested = true
        return true
    }
    mutating func reset(_ version: Int) {
        guard version >= localVersion else { return }
        if !localID.isEmpty { ignored.insert(localID) }
        localVersion = version; pending = false
    }
    mutating func end(_ id: String, _ version: Int) -> Bool {
        guard pending, id == localID, version == localVersion else { return false }
        pending = false; ignored.insert(id); return true
    }
}
// PANEL_VISIBILITY_END

// PET_GEOMETRY_BEGIN
struct PetGeometry {
    static func body(_ cells: [Int], _ cols: Int, _ rows: Int, _ frame: NSRect) -> NSRect {
        guard cols > 0, rows > 0, cells.count == cols*rows else { return frame }
        let occupied = cells.indices.filter { cells[$0] != 0 }
        guard !occupied.isEmpty else { return frame }
        let xs = occupied.map { $0 % cols }, ys = occupied.map { $0 / cols }
        return NSRect(x: frame.minX + CGFloat(xs.min()!)/CGFloat(cols)*frame.width,
                      y: frame.minY + CGFloat(ys.min()!)/CGFloat(rows)*frame.height,
                      width: CGFloat(xs.max()!-xs.min()!+1)/CGFloat(cols)*frame.width,
                      height: CGFloat(ys.max()!-ys.min()!+1)/CGFloat(rows)*frame.height)
    }
    static func accepts(_ point: NSPoint, _ cells: [Int], _ cols: Int, _ rows: Int, _ frame: NSRect) -> Bool {
        guard cols > 0, rows > 0, cells.count == cols*rows, frame.width > 0, frame.height > 0, frame.contains(point) else { return false }
        let x = Int((point.x-frame.minX)/frame.width*CGFloat(cols))
        let y = Int((point.y-frame.minY)/frame.height*CGFloat(rows))
        return x >= 0 && x < cols && y >= 0 && y < rows && cells[y*cols+x] != 0
    }
    static func origin(_ point: NSPoint, _ screen: NSRect, _ body: NSRect) -> NSPoint {
        let loX=screen.minX-body.minX+4, hiX=screen.maxX-body.maxX-4
        let loY=screen.minY-body.minY+8, hiY=screen.maxY-body.maxY-4
        return NSPoint(x: loX <= hiX ? max(loX,min(point.x,hiX)) : (loX+hiX)/2,
                       y: loY <= hiY ? max(loY,min(point.y,hiY)) : (loY+hiY)/2)
    }
}
// PET_GEOMETRY_END

final class PetView: NSView {
    private let chatURL: URL
    private let toggleChat: () -> Void
    private let stopWaiting: (() -> Void)?
    private let resetChat: (() -> Void)?
    private let isWaiting: (() -> Bool)?
    private let visibleBounds: (() -> NSRect?)?
    private let paintBlue: Bool
    private let fallback: (() -> Void)?
    private var pointerStart: NSPoint?
    private var windowStart: NSPoint?
    private var didDrag = false
    var trackingDrag: Bool { pointerStart != nil }
    private func trace(_ message: String) {
        if ProcessInfo.processInfo.environment["LOCAL_FILE_CHAT_PET_DIAGNOSTICS"] == "1" {
            print(message)
            fflush(stdout)
        }
    }

    init(frame: NSRect, chatURL: URL, paintBlue: Bool = true, fallback: (() -> Void)? = nil, stopWaiting: (() -> Void)? = nil, resetChat: (() -> Void)? = nil, isWaiting: (() -> Bool)? = nil, visibleBounds: (() -> NSRect?)? = nil, toggleChat: @escaping () -> Void) {
        self.chatURL = chatURL
        self.toggleChat = toggleChat
        self.stopWaiting = stopWaiting; self.resetChat = resetChat; self.isWaiting = isWaiting
        self.visibleBounds = visibleBounds
        self.paintBlue = paintBlue
        self.fallback = fallback
        super.init(frame: frame)
        toolTip = "单击显示/隐藏聊天 · 拖动改变位置 · 右键打开网页或退出"
    }

    required init?(coder: NSCoder) {
        return nil
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        if !paintBlue { return }
        let body = NSBezierPath(ovalIn: bounds.insetBy(dx: 5, dy: 5))
        NSColor(calibratedRed: 0.14, green: 0.35, blue: 0.78, alpha: 0.98).setFill()
        body.fill()
        NSColor(calibratedRed: 0.57, green: 0.75, blue: 1.0, alpha: 1).setStroke()
        body.lineWidth = 2
        body.stroke()

        let leftEye = NSBezierPath(ovalIn: NSRect(x: 24, y: 43, width: 7, height: 9))
        let rightEye = NSBezierPath(ovalIn: NSRect(x: 49, y: 43, width: 7, height: 9))
        NSColor.white.setFill()
        leftEye.fill()
        rightEye.fill()

        let smile = NSBezierPath()
        smile.move(to: NSPoint(x: 28, y: 33))
        smile.curve(to: NSPoint(x: 52, y: 33),
                    controlPoint1: NSPoint(x: 34, y: 24),
                    controlPoint2: NSPoint(x: 46, y: 24))
        smile.lineWidth = 2.5
        smile.lineCapStyle = .round
        NSColor.white.setStroke()
        smile.stroke()
    }

    override func mouseDown(with event: NSEvent) {
        pointerStart = window?.convertPoint(toScreen: event.locationInWindow)
        windowStart = window?.frame.origin
        didDrag = false
        trace("Pet mouseDown \(String(describing: pointerStart))")
    }

    override func mouseDragged(with event: NSEvent) {
        guard let pointerStart, let windowStart, let window else { return }
        let current = window.convertPoint(toScreen: event.locationInWindow)
        let dx = current.x - pointerStart.x
        let dy = current.y - pointerStart.y
        if abs(dx) + abs(dy) > 4 { didDrag = true }
        trace("Pet mouseDragged \(dx),\(dy)")
        move(window, to: NSPoint(x: windowStart.x + dx, y: windowStart.y + dy))
    }

    override func mouseUp(with event: NSEvent) {
        // Some native/automated event sequences omit mouseDragged; use final
        // displacement as well, so releasing a drag never becomes a click.
        if let pointerStart, let windowStart, let window {
            let current = window.convertPoint(toScreen: event.locationInWindow)
            let dx = current.x - pointerStart.x
            let dy = current.y - pointerStart.y
            if abs(dx) + abs(dy) > 4 {
                didDrag = true
                move(window, to: NSPoint(x: windowStart.x + dx, y: windowStart.y + dy))
            }
        }
        trace("Pet mouseUp drag=\(didDrag) origin=\(String(describing: window?.frame.origin))")
        if !didDrag { toggleChat() }
        pointerStart = nil
        windowStart = nil
    }

    private func move(_ window: NSWindow, to origin: NSPoint) {
        let pointer = NSEvent.mouseLocation
        let screen = NSScreen.screens.first { $0.frame.contains(pointer) } ?? window.screen ?? NSScreen.main
        guard let visible = screen?.visibleFrame else { return }
        window.setFrameOrigin(PetGeometry.origin(origin, visible, visibleBounds?() ?? NSRect(origin: .zero, size: window.frame.size)))
    }

    @objc private func openChat() {
        NSWorkspace.shared.open(chatURL)
    }

    @objc private func quitPet() {
        DistributedNotificationCenter.default().postNotificationName(NSNotification.Name("local.filechat.asuka.launcher.stop"),object:nil)
        NSApplication.shared.terminate(nil)
    }

    @objc private func restoreBlue() { fallback?() }

    @objc private func stopTasks() { stopWaiting?() }
    @objc private func resetConversation() { resetChat?() }

    override func rightMouseDown(with event: NSEvent) {
        let menu = NSMenu()
        menu.autoenablesItems = false
        let open = NSMenuItem(title: "打开文件助手", action: #selector(openChat), keyEquivalent: "")
        open.target = self
        menu.addItem(open)
        if fallback != nil {
            let blue = NSMenuItem(title: "切回蓝色助手", action: #selector(restoreBlue), keyEquivalent: "")
            blue.target = self
            menu.addItem(blue)
        }
        let stop = NSMenuItem(title: "停止等待任务", action: #selector(stopTasks), keyEquivalent: "")
        stop.target = self; stop.isEnabled = isWaiting?() ?? false; menu.addItem(stop)
        let reset = NSMenuItem(title: "重置桌面对话", action: #selector(resetConversation), keyEquivalent: "")
        reset.target = self; reset.isEnabled = resetChat != nil; menu.addItem(reset)
        let quit = NSMenuItem(title: "退出悬浮助手", action: #selector(quitPet), keyEquivalent: "")
        quit.target = self
        menu.addItem(quit)
        DistributedNotificationCenter.default().postNotificationName(NSNotification.Name("local.filechat.reminder.menu.begin"),object:nil)
        defer {DistributedNotificationCenter.default().postNotificationName(NSNotification.Name("local.filechat.reminder.menu.end"),object:nil)}
        menu.popUp(positioning: nil, at: convert(event.locationInWindow, from: nil), in: self)
    }
}

final class BubblePanel: NSPanel {
    override var canBecomeKey: Bool { true }
}

final class PetDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    var panel: NSPanel?
    var chatWindow: NSWindow?
    var webView: WKWebView?
    var chatURL: URL?
    var petBrowser: WKWebView?
    var petURL: URL?
    var assistantState = "idle"
    var assistantStateGeneration = 0
    var chatBridgeScript: String?
    weak var hitLayer: PetView?
    var hitCanvas = NSRect.zero
    var hitMask: [Int] = []
    var hitCols = 0
    var hitRows = 0
    var hitTimer: Timer?
    var taskTimer: Timer?
    var taskPollInFlight = false
    var lastTaskSignature = ""
    var waitingMode = "none"
    var visibility = PanelVisibility()
    var presentationVersion = 0
    var panelPageID: String?
    var focusOnReveal = false

    func stopWaitingTasks() {
        if visibility.pending, let browser = webView {
            let args: [Any] = [visibility.localID, visibility.localVersion]
            if let data = try? JSONSerialization.data(withJSONObject: args), let json = String(data: data, encoding: .utf8) {
                browser.evaluateJavaScript("window.localFileChatPanel?.stopFor(..." + json + ")", completionHandler: nil)
            }
        }
        for row in visibility.rows {
            guard let id = row["request_id"] as? String, id != visibility.localID,
                  let channel = row["channel"] as? String, ["file","bubble"].contains(channel), let url = chatURL else { continue }
            var req = URLRequest(url: url.appendingPathComponent(channel == "file" ? "api/chat/cancel" : "api/bubble/cancel"), timeoutInterval: 5)
            req.httpMethod = "POST"; req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try? JSONSerialization.data(withJSONObject: ["request_id": id])
            URLSession.shared.dataTask(with: req).resume()
        }
    }

    private var reminderPresented=false,reminderHidden=false,reminderRestoreVisible=false,reminderForcedChat=false
    private var reminderSuppressCompletion=false
    private var reminderLatestIntent:Bool?
    private var reminderRows=[[String:Any]](),reminderReceiptIDs=Set<String>(),reminderReadPending=false
    private var reminderSnapshotHash:Data?
    private let reminderIO=DispatchQueue(label:"local.filechat.pet.reminder")
    private var reminderRoot:URL {URL(fileURLWithPath:CommandLine.arguments[0]).deletingLastPathComponent().appendingPathComponent(".runtime/reminder-ipc")}
    private func trustedReminder(_ r:[String:Any])->Bool {
        guard let id=r["id"] as? String,id.count==64,id.allSatisfy({$0.isHexDigit}),let delivery=r["delivery_id"] as? String,delivery.count==32,delivery.allSatisfy({$0.isHexDigit}),let v=r["version"] as? Int else{return false}
        let file=reminderRoot.deletingLastPathComponent().appendingPathComponent(r["synthetic"] as? Bool==true ? "reminder-tests/plans.json":"calendar-reminders/plans.json")
        guard let raw=try? Data(contentsOf:file),let d=(try? JSONSerialization.jsonObject(with:raw)) as? [String:Any],let items=d["items"] as? [String:[String:Any]],let row=items[id],row["version"] as? Int==v,row["delivery_id"] as? String==delivery,
              ["claimed","system_accepted","system_visible","user_viewed"].contains(row["status"] as? String ?? ""),let text=row["start"] as? String,let start=ISO8601DateFormatter().date(from:text),start>Date() else{return false}
        return true
    }
    private func pollSharedReminders(){
        guard bubbleMode,!reminderReadPending else{return};reminderReadPending=true
        let root=reminderRoot,ops=reminderReceiptIDs
        reminderIO.async { [weak self] in
            guard let self else{return};var d:[String:Any]=["protocol":2,"rows":[[String:Any]](),"focus":""]
            if let raw=try? Data(contentsOf:root.appendingPathComponent("shared-presentation.json")),raw.count<262144,let q=(try? JSONSerialization.jsonObject(with:raw)) as? [String:Any],let at=q["at"] as? Double,abs(Date().timeIntervalSince1970-at)<5,let rows=q["rows"] as? [[String:Any]],rows.count<=256 {d=q;d["rows"]=rows.filter{self.trustedReminder($0)}}
            var receipts=[String:Any]()
            for op in ops {if let raw=try? Data(contentsOf:root.appendingPathComponent(op+".action.result.json")),raw.count<8192,let r=try? JSONSerialization.jsonObject(with:raw){receipts[op]=r}}
            d["receipts"]=receipts
            DispatchQueue.main.async {
                self.reminderReadPending=false;self.reminderRows=d["rows"] as? [[String:Any]] ?? []
                if self.reminderRows.isEmpty && !self.reminderPresented{return}
                self.ensureChatWindow()
                // Timestamp is a liveness check, not a presentation change.
                var display=d;display.removeValue(forKey:"at");display.removeValue(forKey:"pid")
                guard let payload=try? JSONSerialization.data(withJSONObject:display,options:.sortedKeys),payload != self.reminderSnapshotHash,let json=String(data:payload,encoding:.utf8) else{return}
                self.webView?.evaluateJavaScript("!!window.localFileChatSharedReminderReady") { ready,error in
                    guard error==nil,ready as? Bool==true else{return}
                    self.webView?.evaluateJavaScript("window.dispatchEvent(new CustomEvent('native-reminder-snapshot',{detail:"+json+"}))") { _,error in if error==nil{self.reminderSnapshotHash=payload} }
                }
            }
        }
    }
    private func reminderMessage(_ data:[String:Any],_ kind:String){
        if kind=="reminder_show" {
            guard !reminderRows.isEmpty else{return}
            if !reminderPresented {reminderRestoreVisible=chatWindow?.isVisible ?? false;reminderLatestIntent=nil;reminderHidden=false}
            reminderPresented=true;focusOnReveal=false;reconcilePanel();return
        }
        if kind=="reminder_end" {
            guard reminderPresented else{return}
            let explicit=data["explicit"] as? Bool==true
            visibility.requested=explicit || (reminderLatestIntent ?? reminderRestoreVisible)
            reminderForcedChat=visibility.requested;reminderSuppressCompletion = !visibility.requested;reminderPresented=false;reminderHidden=false
            focusOnReveal=false;reconcilePanel();return
        }
        guard kind=="reminder_action" || kind=="reminder_reconcile",let op=data["operation_id"] as? String,op.count==32,op.allSatisfy({$0.isHexDigit}) else{return}
        if kind=="reminder_reconcile" {if reminderReceiptIDs.count<256{reminderReceiptIDs.insert(op)};pollSharedReminders();return}
        guard let id=data["id"] as? String,let delivery=data["delivery_id"] as? String,let version=data["version"] as? Int,let row=reminderRows.first(where:{$0["id"] as? String==id && $0["version"] as? Int==version && $0["delivery_id"] as? String==delivery}),["snooze","dismiss"].contains(data["action"] as? String ?? "") else{return}
        guard reminderReceiptIDs.count<256 || reminderReceiptIDs.contains(op) else{return}
        reminderReceiptIDs.insert(op);let root=reminderRoot
        reminderIO.async {
            guard self.trustedReminder(row) else{
                let failed:[String:Any]=["status":"failed","message":"提醒已变化，旧操作无效。"]
                if let raw=try? JSONSerialization.data(withJSONObject:failed){try? raw.write(to:root.appendingPathComponent(op+".action.result.json"),options:.atomic)}
                return
            }
            var q=data;q.removeValue(forKey:"page_id");q.removeValue(forKey:"kind")
            let file=root.appendingPathComponent(op+".action.json")
            // Never overwrite an existing operation, including after lost replies.
            guard !FileManager.default.fileExists(atPath:file.path),let raw=try? JSONSerialization.data(withJSONObject:q) else{return}
            let temporary=root.appendingPathComponent(op+".action.pending")
            do {try raw.write(to:temporary,options:.atomic);try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:temporary.path);try FileManager.default.linkItem(at:temporary,to:file);try FileManager.default.removeItem(at:temporary)} catch {return}
            try? FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:file.path)
        }
    }
    func reconcilePanel() {
        presentationVersion += 1
        let version = presentationVersion
        waitingMode = (reminderPresented || reminderForcedChat) ? "none":visibility.mode
        let wantsPanel = reminderPresented ? !reminderHidden : (reminderForcedChat || (!reminderSuppressCompletion && visibility.visible))
        if !wantsPanel { hideChatForState() }
        let state = visibility.busy ? (assistantState == "searching" ? "searching" : "generating") : (assistantState == "complete" ? "complete" : "idle")
        // PRESENTATION_ACK_BEGIN
        let script = """
        (() => {
          window.localFileChatLive2D?.setTaskState('\(state)','\(waitingMode)');
          const indicator=document.querySelector('#searchBubble');
          return {visible: !!indicator && !indicator.hidden};
        })()
        """
        // PRESENTATION_ACK_END
        let finish: (Any?, Error?) -> Void = { [weak self] result, error in
            guard let self, version == self.presentationVersion else { return }
            let indicatorVisible = (result as? [String: Any])?["visible"] as? Bool ?? false
            // Reveal only after the model page acknowledged removal of waiting.
            if wantsPanel && self.waitingMode == "none" && !indicatorVisible && error == nil && (self.petBrowser == nil || result is [String: Any]) {
                self.ensureChatWindow()
                self.positionBubble()
                self.refreshBubbleConfig()
                if self.focusOnReveal { self.activateChatWindow(); self.focusOnReveal = false }
                else { self.chatWindow?.orderFrontRegardless() }
            }
            let panelVisible = self.chatWindow?.isVisible ?? false
            let exclusive = !(panelVisible && indicatorVisible)
            if !exclusive { self.hideChatForState() }
            print("Panel presentation: version=\(version) panel=\(self.chatWindow?.isVisible ?? false) indicator=\(indicatorVisible) waiting=\(self.waitingMode) exclusive=\(exclusive)"); fflush(stdout)
        }
        if let browser = petBrowser { browser.evaluateJavaScript(script, completionHandler: finish) }
        else { finish(nil, nil) }
    }


    private var lastReminderPresentation=Date.distantPast
    func publishReminderPresentation(force:Bool=false){
        guard Date().timeIntervalSince(lastReminderPresentation)>(force ? 0.05:0.5),let panel,let screen=panel.screen ?? NSScreen.main else{return}
        lastReminderPresentation=Date();let r=panel.convertToScreen(visibleBody),b=screen.visibleFrame
        let d:[String:Any]=["at":Date().timeIntervalSince1970,"pid":ProcessInfo.processInfo.processIdentifier,"avatar":[r.minX,r.minY,r.width,r.height],"screen":[b.minX,b.minY,b.width,b.height],"chat_visible":chatWindow?.isVisible ?? false,"chat_rect":[chatWindow?.frame.minX ?? 0,chatWindow?.frame.minY ?? 0,chatWindow?.frame.width ?? 0,chatWindow?.frame.height ?? 0],"busy":visibility.busy]
        let root=URL(fileURLWithPath:CommandLine.arguments[0]).deletingLastPathComponent().appendingPathComponent(".runtime/reminder-ipc")
        if let raw=try? JSONSerialization.data(withJSONObject:d){try? raw.write(to:root.appendingPathComponent("presentation.json"),options:.atomic);DistributedNotificationCenter.default().postNotificationName(NSNotification.Name("local.filechat.reminder.position"),object:nil,userInfo:nil,deliverImmediately:true)}
    }
    func pollTaskStatus() {
        publishReminderPresentation();pollSharedReminders()
        guard !taskPollInFlight, let url = chatURL else { return }
        taskPollInFlight = true
        URLSession.shared.dataTask(with: URLRequest(url: url.appendingPathComponent("api/tasks/status"),
                                                   cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 2)) { [weak self] data, response, _ in
            DispatchQueue.main.async {
                guard let self else { return }
                self.taskPollInFlight = false
                guard let data, data.count < 65536, (response as? HTTPURLResponse)?.statusCode == 200,
                      let snapshot = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let state = snapshot["state"] as? String,
                      let waiting = snapshot["waiting"] as? String,
                      ["idle","searching","generating","complete"].contains(state),
                      ["none","dots","wifi"].contains(waiting) else {
                    self.lastTaskSignature = "" // Reapply even the same revision after reconnect.
                    if self.waitingMode != "none" { self.waitingMode = "none"; self.updateAssistantState("idle"); self.visibility.rows = []; self.reconcilePanel() }
                    return
                }
                let signature = "\(snapshot["instance"] ?? ""):\(snapshot["revision"] ?? 0):\(state):\(waiting)"
                if signature == self.lastTaskSignature { return }
                self.lastTaskSignature = signature
                self.visibility.rows = snapshot["tasks"] as? [[String: Any]] ?? []
                self.waitingMode = self.visibility.mode
                if self.visibility.busy { self.hideChatForState() }
                if state != self.assistantState { self.updateAssistantState(state) }
                self.reconcilePanel()
                print("Pet activity: state=\(state) waiting=\(waiting) active=\(snapshot["active_count"] ?? 0)"); fflush(stdout)
            }
        }.resume()
    }

    func applyWaitingState() {
        reconcilePanel()
    }
    func updateHitAcceptance() {
        guard let panel, let hitLayer else { return }
        if hitLayer.trackingDrag { panel.ignoresMouseEvents = false; return }
        let location = panel.convertPoint(fromScreen: NSEvent.mouseLocation)
        var accepts = false
        if hitCols > 0 && hitRows > 0 && hitMask.count == hitCols*hitRows {
            accepts = PetGeometry.accepts(location, hitMask, hitCols, hitRows, hitCanvas)
        } else {
            accepts = NSBezierPath(ovalIn: hitLayer.bounds.insetBy(dx: 5, dy: 5)).contains(location)
        }
        panel.ignoresMouseEvents = !accepts
    }
    var visibleBody: NSRect {
        let frame = hitCanvas.isEmpty ? NSRect(origin: .zero, size: panel?.frame.size ?? NSSize(width:80,height:80)) : hitCanvas
        return PetGeometry.body(hitMask,hitCols,hitRows,frame)
    }
    func positionWaiting() {
        guard let panel, let screen = panel.screen ?? NSScreen.main, let browser = petBrowser else { return }
        let shown=panel.frame.intersection(screen.visibleFrame)
        guard !shown.isNull else { return }
        let value:[String:Double] = ["left":(shown.minX-panel.frame.minX)/panel.frame.width,
            "right":(shown.maxX-panel.frame.minX)/panel.frame.width,
            "top":(panel.frame.maxY-shown.maxY)/panel.frame.height,
            "bottom":(panel.frame.maxY-shown.minY)/panel.frame.height]
        if let data=try? JSONSerialization.data(withJSONObject:value), let text=String(data:data,encoding:.utf8) {
            browser.evaluateJavaScript("window.localFileChatLive2D?.setVisibleViewport("+text+")",completionHandler:nil)
        }
    }
    let bubbleMode = CommandLine.arguments.contains("--bubble-chat")

    func positionBubble() {
        guard bubbleMode, let panel, let window = chatWindow else { return }
        let screen = panel.screen ?? NSScreen.screens.first(where: { $0.frame.contains(NSEvent.mouseLocation) }) ?? NSScreen.main
        guard let bounds = screen?.visibleFrame else { return }
        let size = window.frame.size
        let avatar=panel.convertToScreen(visibleBody)
        let right = avatar.maxX + 4
        let placeOnRight = right + size.width <= bounds.maxX
        let preferredX = placeOnRight ? right : avatar.minX - size.width - 4
        let x = max(bounds.minX, min(preferredX, bounds.maxX - size.width))
        let y = max(bounds.minY, min(avatar.maxY - 50, bounds.maxY - size.height))
        window.setFrameOrigin(NSPoint(x: x, y: y))
        let tailSide = placeOnRight ? "left" : "right"
        webView?.evaluateJavaScript("document.documentElement.dataset.tailSide='\(tailSide)'", completionHandler: nil)
    }

    func windowDidMove(_ notification: Notification) {
        if let moved = notification.object as? NSWindow, moved === panel { positionBubble(); positionWaiting();publishReminderPresentation(force:true) }
    }

    func windowDidResize(_ notification: Notification) {
        if let resized = notification.object as? NSWindow, resized === chatWindow { positionBubble();publishReminderPresentation(force:true) }
    }

    func updateAssistantState(_ state: String) {
        guard ["idle", "searching", "generating", "complete"].contains(state) else { return }
        assistantState = state
        assistantStateGeneration += 1
        let generation = assistantStateGeneration
        if state == "complete" {
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
                guard let self, self.assistantStateGeneration == generation else { return }
                self.updateAssistantState("idle")
            }
        }
        if ProcessInfo.processInfo.environment["LOCAL_FILE_CHAT_PET_DIAGNOSTICS"] == "1" {
            print("Pet assistant state: \(state)"); fflush(stdout)
        }
        // Only a validated enum crosses into the model page; no chat contents are copied.
        if visibility.busy { hideChatForState() }
    }

    func restoreBluePet() {
        guard let panel, let chatURL else { return }
        panel.hasShadow = true
        petBrowser?.stopLoading()
        petBrowser?.configuration.userContentController.removeScriptMessageHandler(forName: "live2d")
        petBrowser = nil
        hitMask = []; hitCols = 0; hitRows = 0; hitCanvas = .zero
        panel.setContentSize(NSSize(width: 80, height: 80))
        panel.contentView = PetView(frame: NSRect(x: 0, y: 0, width: 80, height: 80), chatURL: chatURL,
                                   stopWaiting: { [weak self] in self?.stopWaitingTasks() },
                                     resetChat: { [weak self] in self?.webView?.evaluateJavaScript("window.localFileChatPanel?.reset()", completionHandler: nil) },
                                     isWaiting: { [weak self] in self?.visibility.busy ?? false },
                                     visibleBounds: { [weak self] in self?.visibleBody },
                                     toggleChat: { [weak self] in self?.toggleChatWindow() })
        hitLayer = panel.contentView as? PetView
        print("Live2D 已回退原蓝色助手；聊天窗口保持同一实例。")
        fflush(stdout)
    }

    func attachLive2D(_ url: URL) {
        guard let panel, let chatURL else { return }
        // AppKit otherwise casts a shadow around every opaque character pixel.
        // The separate footShadow canvas supplies the only character shadow.
        panel.hasShadow = false
        let size = NSSize(width: 204, height: 224)
        panel.setContentSize(size)
        if let screen = NSScreen.main?.visibleFrame {
            panel.setFrameOrigin(NSPoint(x: min(panel.frame.minX, screen.maxX - size.width - 24),
                                         y: min(panel.frame.minY, screen.maxY - size.height)))
        }
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.userContentController.add(self, name: "live2d")
        let browser = WKWebView(frame: NSRect(origin: .zero, size: size), configuration: configuration)
        browser.setValue(false, forKey: "drawsBackground")
        browser.navigationDelegate = self
        browser.autoresizingMask = [.width, .height]
        let container = NSView(frame: browser.frame)
        container.addSubview(browser)
        // Native hit layer preserves the existing click/drag/right-click behavior.
        // The model page remains passive; it cannot intercept a drag as a click.
        let hitLayer = PetView(frame: browser.frame, chatURL: chatURL, paintBlue: false,
                               fallback: { [weak self] in self?.restoreBluePet() },
                               stopWaiting: { [weak self] in self?.stopWaitingTasks() },
                                     resetChat: { [weak self] in self?.webView?.evaluateJavaScript("window.localFileChatPanel?.reset()", completionHandler: nil) },
                                     isWaiting: { [weak self] in self?.visibility.busy ?? false },
                                     visibleBounds: { [weak self] in self?.visibleBody },
                                     toggleChat: { [weak self] in self?.toggleChatWindow() })
        hitLayer.autoresizingMask = [.width, .height]
        container.addSubview(hitLayer)
        self.hitLayer = hitLayer
        panel.contentView = container
        petBrowser = browser
        petURL = url
        browser.load(URLRequest(url: url, timeoutInterval: 15))
        DispatchQueue.main.asyncAfter(deadline: .now() + 25) { [weak self, weak browser] in
            guard let self, let browser, self.petBrowser === browser else { return }
            browser.evaluateJavaScript("document.querySelector('#model')?.dataset.diagnostics") { value, _ in
                if let text = value as? String, let data = text.data(using: .utf8),
                   let diagnostic = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   diagnostic["ready"] as? Bool == true { return }
                self.restoreBluePet()
            }
        }
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        if message.name == "bubblePanel", message.webView === webView, message.frameInfo.isMainFrame,
           let data = message.body as? [String: Any], let kind = data["kind"] as? String,
           let page = data["page_id"] as? String {
            if kind == "attached" && panelPageID == nil { panelPageID = page; return }
            guard page == panelPageID else{return}
            if kind.hasPrefix("reminder_"){reminderMessage(data,kind);return}
            guard let id = data["request_id"] as? String,
                  let version = data["version"] as? Int, id.count <= 64 else { return }
            if kind == "begin" { if visibility.begin(id, version) { focusOnReveal = false;reminderForcedChat=false;reminderSuppressCompletion=false } }
            else if kind == "reset" { visibility.reset(version) }
            else if kind == "ready" { if visibility.answerReady(id, version) { focusOnReveal = false } }
            else if kind == "error" { if visibility.answerReady(id, version) { focusOnReveal = false } }
            else if kind == "stopped" { _ = visibility.end(id, version) }
            reconcilePanel(); return
        }
        if message.name == "assistantState", message.webView === webView {
            // The backend aggregate is authoritative in both bubble and legacy
            // file-chat windows; a page cannot clear another page's task.
            return
        }
        guard message.webView === petBrowser, let data = message.body as? [String: Any] else { return }
        if data["type"] as? String == "hitMask" {
            if let cols = data["cols"] as? Int, let rows = data["rows"] as? Int,
               let cells = data["cells"] as? [Int], cols > 0, rows > 0,
               cols <= 100, rows <= 100, cells.count == cols*rows {
                let firstMask = hitMask.isEmpty
                hitCols = cols; hitRows = rows; hitMask = cells
                if let f=data["frame"] as? [String:Double], let size=panel?.frame.size,
                   let x=f["x"], let y=f["y"], let w=f["width"], let h=f["height"],
                   x >= 0, y >= 0, w > 0, h > 0, x+w <= 1.001, y+h <= 1.001 {
                    hitCanvas=NSRect(x:x*size.width,y:y*size.height,width:w*size.width,height:h*size.height)
                } else { hitCanvas=NSRect(origin:.zero,size:panel?.frame.size ?? .zero) }
                updateHitAcceptance();positionWaiting()
                if firstMask { print("Pet visible geometry: canvas=\(hitCanvas) body=\(visibleBody)");fflush(stdout) }
                if firstMask { print("Pet hit mask ready: \(cols)x\(rows), occupied=\(cells.filter { $0 != 0 }.count)"); fflush(stdout) }
            }
            return
        }
        print("Live2D WKWebView: \(data)")
        fflush(stdout)
        if data["type"] as? String == "error" { restoreBluePet() }
        if data["type"] as? String == "ready" {
            // Warm the existing hidden chat page so its local budget is current before first click.
            if bubbleMode { ensureChatWindow()
                for (name,value) in [("local.filechat.reminder.menu.begin",true),("local.filechat.reminder.menu.end",false)] {
                    _=DistributedNotificationCenter.default().addObserver(forName:NSNotification.Name(name),object:nil,queue:.main){[weak self] _ in self?.webView?.evaluateJavaScript("window.dispatchEvent(new CustomEvent('native-reminder-menu',{detail:"+(value ? "true":"false")+"}))",completionHandler:nil)}
                }
                for (name,value) in [(NSMenu.didBeginTrackingNotification,true),(NSMenu.didEndTrackingNotification,false)] {
                    NotificationCenter.default.addObserver(forName:name,object:nil,queue:.main){[weak self] _ in self?.webView?.evaluateJavaScript("window.dispatchEvent(new CustomEvent('native-reminder-menu',{detail:"+(value ? "true":"false")+"}))",completionHandler:nil)}
                }
            }
            updateAssistantState(assistantState); applyWaitingState()
        }
    }

    func toggleChatWindow() {
        if reminderPresented {reminderHidden.toggle();reminderLatestIntent = !reminderHidden;reminderSuppressCompletion=reminderHidden;focusOnReveal = !reminderHidden;reconcilePanel();return}
        reminderForcedChat=false;reminderSuppressCompletion=false
        visibility.requested.toggle()
        focusOnReveal = visibility.requested
        reconcilePanel()
    }

    private func activateChatWindow() {
        guard let window = chatWindow, let browser = webView else { return }
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
        // Preserve WebKit's inner first responder and DOM selection on reopen.
        if let view = window.firstResponder as? NSView,
           view === browser || view.isDescendant(of: browser) { return }
        window.makeFirstResponder(browser)
    }

    func ensureChatWindow() {
        if chatWindow != nil { return }
        guard let url = chatURL else { return }
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        if petURL != nil {
            configuration.userContentController.add(self, name: "assistantState")
        }
        configuration.userContentController.add(self, name: "bubblePanel")
        let browser = WKWebView(frame: .zero, configuration: configuration)
        browser.navigationDelegate = self
        browser.uiDelegate = self
        let rect = NSRect(x: 0, y: 0, width: bubbleMode ? 260 : 440, height: bubbleMode ? 190 : 620)
        let window: NSWindow = bubbleMode
            ? BubblePanel(contentRect: rect, styleMask: [.borderless, .resizable], backing: .buffered, defer: false)
            : NSWindow(contentRect: rect, styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = bubbleMode ? "明日香 · 本地闲聊" : "本地文件助手 · 聊天"
        window.minSize = bubbleMode ? NSSize(width: 240, height: 170) : NSSize(width: 360, height: 420)
        if bubbleMode {
            window.maxSize = NSSize(width: 360, height: 300)
            window.isOpaque = false; window.backgroundColor = .clear; window.hasShadow = true
            browser.setValue(false, forKey: "drawsBackground")
        }
        window.level = .floating
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.contentView = browser
        window.initialFirstResponder = browser
        window.center()
        chatWindow = window
        positionBubble()
        webView = browser
        // Fetch our small script from the existing loopback asset service. Native macOS
        // apps may lack Desktop file access; do not request broader system permissions.
        if let petURL, let bridgeURL = URL(string: "/chat-bridge.js", relativeTo: petURL) {
            URLSession.shared.dataTask(with: URLRequest(url: bridgeURL, timeoutInterval: 5)) { [weak self, weak browser] data, response, _ in
                guard let data, data.count < 65536, (response as? HTTPURLResponse)?.statusCode == 200,
                      let script = String(data: data, encoding: .utf8) else { return }
                DispatchQueue.main.async {
                    guard let self, let browser, self.webView === browser else { return }
                    self.chatBridgeScript = script
                    configuration.userContentController.addUserScript(WKUserScript(source: script,
                        injectionTime: .atDocumentEnd, forMainFrameOnly: true))
                    if !browser.isLoading { browser.evaluateJavaScript(script, completionHandler: nil) }
                }
            }.resume()
        }
        browser.load(URLRequest(url: url.appendingPathComponent(bubbleMode ? "bubble" : "compact"), timeoutInterval: 15))
    }

    func hideChatForState() {
        webView?.evaluateJavaScript("window.dispatchEvent(new Event('local-file-chat-hidden'))",completionHandler:nil)
        chatWindow?.orderOut(nil)
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if sender === chatWindow {if reminderPresented{reminderHidden=true;reminderLatestIntent=false};visibility.requested=false;reminderForcedChat=false;reminderSuppressCompletion=true}
        webView?.evaluateJavaScript("window.dispatchEvent(new Event('local-file-chat-hidden'))",completionHandler:nil)
        sender.orderOut(nil)
        return false
    }

    func refreshBubbleConfig() {
        guard bubbleMode, let browser = webView else { return }
        browser.callAsyncJavaScript("""
            await window.localFileChatPanel?.refreshConfig?.();
            return {enabled: !document.querySelector('#web').disabled,
                    checked: document.querySelector('#web').checked,
                    usage: document.querySelector('#usage').textContent};
            """, arguments: [:], in: nil, in: .page) { result in
                if case .success(let value) = result { print("Bubble local config: \(value)"); fflush(stdout) }
            }
    }

    func webView(_ browser: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        guard browser === webView else { return }
        // A new document has a new page ID and starts its request versions at zero.
        // ANSWER_NAVIGATION_BEGIN
        panelPageID = nil
        reminderSnapshotHash=nil
        if !visibility.localID.isEmpty { visibility.ignored.insert(visibility.localID) }
        visibility.localID = ""
        visibility.localVersion = 0
        visibility.pending = false
        // ANSWER_NAVIGATION_END
    }

    func webView(_ browser: WKWebView, didFinish navigation: WKNavigation!) {
        if browser === webView { positionBubble(); refreshBubbleConfig() }
        if browser === webView, let chatBridgeScript {
            browser.evaluateJavaScript(chatBridgeScript, completionHandler: nil)
        }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if webView === petBrowser { restoreBluePet(); return }
        showOffline(webView)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if webView === petBrowser { restoreBluePet(); return }
        showOffline(webView)
    }

    private func showOffline(_ browser: WKWebView) {
        guard let url = chatURL else { return }
        browser.loadHTMLString("""
        <meta name="viewport" content="width=device-width,initial-scale=1"><body style="font-family:-apple-system;background:#f7f9fd;color:#17469f;padding:24px"><h2>后端未连接</h2><p>请在当前项目运行 start.py 或 app.py，再点击重试。</p><a href="\(url.appendingPathComponent("compact").absoluteString)">重新连接</a></body>
        """, baseURL: url)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url,
           (url.host == "127.0.0.1" && url.port == chatURL?.port) ||
           (bubbleMode && navigationAction.navigationType == .linkActivated && ["https", "http"].contains(url.scheme ?? "") && url.host != "127.0.0.1") {
            NSWorkspace.shared.open(url)
        }
        return nil
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let url = navigationAction.request.url
        if webView === petBrowser {
            decisionHandler(url?.scheme == "http" && url?.host == "127.0.0.1" && url?.port == petURL?.port ? .allow : .cancel)
            return
        }
        if bubbleMode, navigationAction.navigationType == .linkActivated, let url,
           ["https", "http"].contains(url.scheme ?? ""), url.host != "127.0.0.1" {
            NSWorkspace.shared.open(url); decisionHandler(.cancel); return
        }
        decisionHandler(url?.absoluteString == "about:blank" ||
                        (url?.scheme == "http" && url?.host == "127.0.0.1" && url?.port == chatURL?.port) ? .allow : .cancel)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let address = CommandLine.arguments.dropFirst().first ?? "http://127.0.0.1:8765"
        guard let url = URL(string: address), url.scheme == "http", url.host == "127.0.0.1" else {
            NSApplication.shared.terminate(nil)
            return
        }
        chatURL = url
        taskTimer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in self?.pollTaskStatus() }
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 800, height: 600)
        let rect = NSRect(x: screen.maxX - 112, y: screen.minY + 100, width: 80, height: 80)
        let window = NSPanel(contentRect: rect,
                             styleMask: [.borderless, .nonactivatingPanel],
                             backing: .buffered, defer: false)
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = true
        window.level = .floating
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        window.contentView = PetView(frame: NSRect(x: 0, y: 0, width: 80, height: 80), chatURL: url,
                                     stopWaiting: { [weak self] in self?.stopWaitingTasks() },
                                     resetChat: { [weak self] in self?.webView?.evaluateJavaScript("window.localFileChatPanel?.reset()", completionHandler: nil) },
                                     isWaiting: { [weak self] in self?.visibility.busy ?? false },
                                     visibleBounds: { [weak self] in self?.visibleBody },
                                     toggleChat: { [weak self] in self?.toggleChatWindow() })
        hitLayer = window.contentView as? PetView
        hitTimer = Timer.scheduledTimer(withTimeInterval: 1.0/30.0, repeats: true) { [weak self] _ in self?.updateHitAcceptance() }
        window.orderFrontRegardless()
        panel = window
        window.delegate = self
        if CommandLine.arguments.count > 2, let modelURL = URL(string: CommandLine.arguments[2]),
           modelURL.scheme == "http", modelURL.host == "127.0.0.1" {
            attachLive2D(modelURL)
        } else {
            // The bootstrap waits for a real window/event-loop readiness marker.
            // No SDK/model is required for this native fallback.
            print("Native blue fallback ready")
            fflush(stdout)
        }
    }
}

let app = NSApplication.shared
let delegate = PetDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.mainMenu = AssistantEditingMenu.make()
app.run()
