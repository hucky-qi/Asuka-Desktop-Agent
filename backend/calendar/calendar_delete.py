"""One explicitly confirmed card-bound deletion; existing calendar journal and bridge."""
import time,uuid,threading
import backend.calendar.calendar_update as update
import backend.native_context as native_context

def supported(e):
    update.allowed(e)
    if e.get('all_day') or e.get('shared') or e.get('delete_supported') is not True:
        raise ValueError('删除暂仅支持本机本地日历中的普通单次可写事件；云端、共享、全天或含与会者事件暂不支持')

def public(p):
    return {'id':p['id'],'count':1,'original':update.edit_public({'id':p['id'],'original':p['original'],'changes':{}})['original'],'expires_at':p['expires_at']}

def preview(selected,sid,epoch):
    e=selected['original'];supported(e)
    p={'id':uuid.uuid4().hex,'operation':'delete','original':e,'state':'pending','session_id':sid,'epoch':epoch,'expires_at':time.time()+300}
    with update.LOCK:update.save(p)
    return public(p)

def action(body,c,sid,cancel=False):
    with update.LOCK:
        p=update.load(body.get('preview_id'),sid)
        if p.get('operation')!='delete':raise ValueError('此令牌不是删除操作，拒绝执行')
        if cancel:
            if p['state'] in ('checking','writing','uncertain'):return {'status':'writing','message':'删除核对或执行已开始，不能宣称已取消'}
            if p['state']=='pending':p['state']='cancelled';update.save(p)
            return {'status':p['state']}
        if body.get('confirm') is not True or body.get('action')!='delete':raise ValueError('必须明确点击确认删除，此操作不能由聊天或修改确认代替')
        if c.epoch!=p['epoch']:raise ValueError('删除预览已失效')
        if p['state']=='verified':return p['result']
        if p['state']=='pending' and time.time()>p['expires_at']:p['state']='cancelled';update.save(p);raise ValueError('删除预览已过期，请重新查询选择')
        if p['id'] in update.INFLIGHT:return {'status':'writing','message':'本次删除正在核对，请勿重复确认'}
        if p['state'] not in ('pending','uncertain','writing'):raise ValueError('删除预览已取消或失效')
        reconcile=p['state'] in ('uncertain','writing');p['state']='writing' if reconcile else 'checking';update.save(p);update.INFLIGHT.add(p['id'])
    try:
        if not reconcile:
            latest=update.inspect(p['original']['event_id'],p['original'],threading.Event())['event'];supported(latest)
            with update.LOCK:
                if c.epoch!=p['epoch'] or update.load(p['id'],sid)['state']=='cancelled':raise ValueError('操作失效，没有删除')
                if latest['version']!=p['original']['version']:
                    p['state']='cancelled';update.save(p)
                    selected={'original':latest}
                    return {'status':'needs_confirmation','message':'原事件已变化，请核对最新信息后重新确认','calendar_delete_preview':preview(selected,sid,c.epoch)}
                p['state']='writing';update.save(p)
        result=update.bridge({'action':'delete_reconcile' if reconcile else 'delete','event_id':p['original']['event_id'],'calendar_id':p['original']['calendar_id'],'expected_version':p['original']['version'],'operation_id':p['id']},threading.Event())
        if result.get('status')=='verified':
            if result.get('event_id')!=p['original']['event_id'] or result.get('deleted') is not True:result={'status':'uncertain','message':'删除读回身份不一致，只能核对原操作，不重试删除'}
        elif result.get('status') in ('timeout','unavailable'):result={'status':'uncertain','message':'删除结果不确定；再次确认仅核对原操作，不重新删除'}
    except ValueError as error:result={'status':'stale','message':str(error)}
    except Exception:result={'status':'uncertain','message':'删除结果未能核对，不盲目重试'}
    finally:
        with update.LOCK:update.INFLIGHT.discard(p['id'])
    with update.LOCK:p['state']=result.get('status','uncertain');p['result']=result;update.save(p)
    return result
