import AppKit
// Format/size conversion only: original artwork is not repainted or stretched.
let source=CommandLine.arguments[1], output=CommandLine.arguments[2]
guard let image=NSImage(contentsOfFile:source) else { fatalError("无法读取图标原图") }
let size=NSSize(width:1024,height:1024)
let icon=NSImage(size:size);icon.lockFocus()
NSColor.clear.setFill();NSRect(origin:.zero,size:size).fill()
let inset:CGFloat=68, area=NSRect(x:inset,y:inset,width:1024-inset*2,height:1024-inset*2)
let ratio=min(area.width/image.size.width,area.height/image.size.height)
let fitted=NSSize(width:image.size.width*ratio,height:image.size.height*ratio)
let rect=NSRect(x:(1024-fitted.width)/2,y:(1024-fitted.height)/2,width:fitted.width,height:fitted.height)
let path=NSBezierPath(roundedRect:rect,xRadius:174,yRadius:174);path.addClip()
image.draw(in:rect,from:.zero,operation:.sourceOver,fraction:1)
icon.unlockFocus()
guard let data=icon.tiffRepresentation,let bitmap=NSBitmapImageRep(data:data),let png=bitmap.representation(using:.png,properties:[:]) else { fatalError("无法转换图标") }
try png.write(to:URL(fileURLWithPath:output))
