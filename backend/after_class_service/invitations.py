"""Invitation state and confirmed single sessions; never writes student JSON."""
import copy
import datetime as dt
import json
import uuid

from model import TZ, clock, end_time, key
from common import Problem, string


def stamp():
    return dt.datetime.now(TZ)


def identifier(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise Problem(400, '请求编号不正确')


def read(db):
    invites = [dict(json.loads(r['data']), id=r['id'], version=r['version']) for r in db.execute('SELECT * FROM invitations ORDER BY rowid DESC')]
    replies = {(r['invitation_id'], r['student_id']): dict(json.loads(r['data']), version=r['version']) for r in db.execute('SELECT * FROM invitation_replies')}
    return invites, replies


def listing(store, user):
    with store.db() as db:
        invites, replies = read(db)
    teacher = user['role'] == 'teacher'
    rows = []
    for inv in invites:
        ids = inv['studentIds'] if teacher else [user['username']]
        if not teacher and (user['username'] not in inv['studentIds'] or inv['status'] == 'draft'):
            continue
        row = {k: inv[k] for k in ('id','version','status','course','date','time','duration','kind','note','deadline','created')}
        row['responses'] = [dict(studentId=sid, **replies.get((inv['id'], sid), {'status':'pending','version':0,'message':''})) for sid in ids]
        row['studentIds'] = ids
        row['expired'] = stamp() > dt.datetime.fromisoformat(inv['deadline'])
        row['chargeLabel'] = '完成上课并保存课次记录后，按现有规则扣 1 次课；确认排课不扣课时'
        rows.append(row)
    schedules=confirmed(store)
    if not teacher: schedules={user['username']:schedules.get(user['username'],[])}
    return {'invitations': rows, 'schedules': schedules}


def confirmed(store):
    with store.db() as db:
        invites, replies = read(db)
    result = {}
    for inv in invites:
        if inv['status'] != 'published':
            continue
        for sid in inv['studentIds']:
            if replies.get((inv['id'], sid), {}).get('status') == 'accepted':
                result.setdefault(sid, []).append(dict(id='invitation-'+inv['id']+'-'+sid,
                    date=inv['date'], startTime=inv['time'], duration=inv['duration'], courseName=inv['course'],
                    invitationId=inv['id'], kind=inv['kind']))
    states,_,_=store.snapshot()
    for origin,saved in states.items():
        plan=saved['data'].get('plannedMakeup') or {}
        if plan.get('studentId') and not plan.get('invitationId'):
            result.setdefault(plan['studentId'],[]).append(dict(id='plan-'+origin,date=plan['date'],startTime=plan['time'],duration=plan['duration'],courseName=plan['course'],kind='makeup',makeupOrigin=origin))
    return result


def enrich(store, students):
    extra = confirmed(store)
    result = copy.deepcopy(students)
    for s in result:
        old = s['data'].get('oneTimeClasses') or []
        s['data']['oneTimeClasses'] = old + [x for x in extra.get(s['id'],[]) if not any(y.get('id')==x['id'] for y in old)]
    return result


def interval(inv):
    start = dt.datetime.fromisoformat(inv['date']+'T'+inv['time']).replace(tzinfo=TZ)
    return start, start + dt.timedelta(hours=inv['duration'])


def overlaps(a, b):
    sa, ea = interval(a)
    sb, eb = interval(b)
    return sa < eb and sb < ea


def validate_slot(body, students):
    course = string(body.get('course'), 160)
    day = string(body.get('date'),10)
    time = string(body.get('time'),5)
    kind = body.get('kind')
    try:
        duration = float(body.get('duration',0))
        beginning = dt.datetime.fromisoformat(day+'T'+time).replace(tzinfo=TZ)
        deadline = dt.datetime.fromisoformat(body.get('deadline','')).replace(tzinfo=TZ)
    except (ValueError,TypeError):
        raise Problem(400,'请填写有效的日期、时间及确认截止时间')
    if clock(time)!=time or not 0<duration<=8 or beginning<=stamp() or beginning>stamp()+dt.timedelta(days=365):
        raise Problem(400,'请选择未来一年内的上课时间，时长须在8小时以内')
    if (beginning+dt.timedelta(hours=duration)).date()!=beginning.date():
        raise Problem(400,'课程必须在当天结束')
    if deadline<=stamp() or deadline>beginning:
        raise Problem(400,'确认截止时间须在现在之后、上课之前')
    ids = body.get('studentIds')
    if not isinstance(ids,list) or not ids or len(ids)>100 or any(not isinstance(x,str) for x in ids):
        raise Problem(400,'请选择学员')
    ids = list(dict.fromkeys(ids))
    roster = {s['id']:s for s in students}
    if any(sid not in roster for sid in ids) or kind not in ('extra','makeup'):
        raise Problem(400,'学员或课程类型不正确')
    for sid in ids:
        data = roster[sid]['data']
        courses = {x.get('courseName') for f in ('records','payments','schedule','oneTimeClasses') for x in (data.get(f) or []) if isinstance(x,dict)}
        if course not in courses:
            raise Problem(400,'所选学员尚未关联该课程，请先核对课程名称')
    return dict(course=course,date=day,time=time,duration=duration,kind=kind,studentIds=ids,
                note=string(body.get('note',''),2000),deadline=deadline.isoformat())


def conflicts(inv, students, others, replies, only_student=None):
    """Check fixed/one-time slots and teacher reservations before committing."""
    start, _ = interval(inv)
    for s in students:
        if only_student and s['id']!=only_student:
            continue
        for f in ('schedule','oneTimeClasses'):
            for slot in s['data'].get(f) or []:
                if not isinstance(slot,dict) or slot.get('status') in ('cancelled','deleted'):
                    continue
                if f=='schedule':
                    try: matches=int(slot.get('dayOfWeek'))%7 == (start.weekday()+1)%7
                    except (ValueError,TypeError): matches=False
                    if not matches or inv['date'] < str(slot.get('firstLesson') or inv['date']) or inv['date'] > str(slot.get('endDate') or inv['date']):
                        continue
                elif slot.get('date')!=inv['date']:
                    continue
                t=clock(slot.get('startTime') or slot.get('time'))
                end=end_time(inv['date'],slot)
                if not t or not end:
                    if s['id'] in inv['studentIds']: raise Problem(409,'当天已有排课时间不完整，请先核对')
                    continue
                other=dict(date=inv['date'],time=t,duration=(end-dt.datetime.fromisoformat(inv['date']+'T'+t).replace(tzinfo=TZ)).total_seconds()/3600)
                if overlaps(inv,other):
                    raise Problem(409,'与已有排课时间冲突，请选择其他时间')
    for other in others:
        if other['id']==inv['id'] or other['status']!='published': continue
        active=[sid for sid in other['studentIds'] if replies.get((other['id'],sid),{}).get('status','pending') in ('pending','accepted')]
        if not active or (only_student and only_student not in active): continue
        if stamp()>dt.datetime.fromisoformat(other['deadline']) and not any(replies.get((other['id'],sid),{}).get('status')=='accepted' for sid in active): continue
        if overlaps(inv,other): raise Problem(409,'与另一项排课邀请冲突，请先取消或调整原邀请')


def mutate(store,user,token,body,loader):
    action=body.get('action')
    teacher=user['role']=='teacher'
    if not teacher and action!='invite_reply': raise Problem(403,'仅教师可操作')
    iid=identifier(body.get('id'))
    students=loader(user,token)
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        invites,replies=read(db)
        inv=next((x for x in invites if x['id']==iid),None)
        if action=='invite_create':
            new=validate_slot(body,students)
            if inv:
                if any(inv.get(k)!=v for k,v in new.items()): raise Problem(409,'同一请求编号的内容不同')
                return {'ok':True,'id':iid}
            new.update(status='draft',created=stamp().isoformat(),origins={})
            if new['kind']=='makeup':
                origins=body.get('origins') or {}
                from model import project
                states={r['id']:dict(version=r['version'],data=json.loads(r['data'])) for r in db.execute('SELECT * FROM states')}
                rows=project(students,states,start=(stamp().date()-dt.timedelta(days=90)).isoformat())
                for sid in new['studentIds']:
                    origin=next((r for r in rows if r['id']==origins.get(sid)),None)
                    if not origin or origin['studentId']!=sid or origin['course']!=new['course'] or origin['attendance'] not in ('leave','makeup_pending') or origin['makeupTarget']:
                        raise Problem(400,'补课须关联该学员同课程的请假待补课课次')
                new['origins']=origins
            db.execute('INSERT INTO invitations VALUES(?,?,?)',(iid,1,json.dumps(new,ensure_ascii=False)))
        else:
            if not inv: raise Problem(404,'未找到邀请')
            if not teacher and user['username'] not in inv['studentIds']: raise Problem(403,'无法操作其他学员的邀请')
            if inv['version']!=body.get('version'): raise Problem(409,'邀请已更新，请刷新后核对')
            if action=='invite_publish':
                if inv['status']=='published': return {'ok':True}
                if inv['status']!='draft' or stamp()>dt.datetime.fromisoformat(inv['deadline']): raise Problem(409,'邀请已取消或已过截止时间')
                conflicts(inv,students,invites,replies)
                inv['status']='published'
                inv['publishedAt']=stamp().isoformat()
            elif action=='invite_cancel':
                if inv['status']=='cancelled': return {'ok':True}
                if stamp()>=interval(inv)[0]: raise Problem(409,'已开始的课程请在上课记录中核对处理')
                inv['status']='cancelled'
                inv['cancelledAt']=stamp().isoformat()
            elif action=='invite_reply':
                if teacher: raise Problem(403,'请由学员账号确认，教师不能代替家长确认')
                sid=user['username']
                if inv['status']!='published': raise Problem(409,'邀请已取消或尚未发布')
                if stamp()>dt.datetime.fromisoformat(inv['deadline']): raise Problem(409,'已超过确认时间，请联系老师')
                status=body.get('status')
                if status not in ('accepted','declined','alternative'): raise Problem(400,'请选择确认结果')
                message=string(body.get('message',''),2000)
                if status=='alternative' and not message: raise Problem(400,'请填写方便的时间')
                prior=replies.get((iid,sid),{'version':0})
                if prior.get('status')==status and prior.get('message','')==message: return {'ok':True}
                if prior['version']!=body.get('replyVersion'): raise Problem(409,'确认状态已改变，请刷新')
                if status=='accepted': conflicts(inv,students,invites,replies,only_student=sid)
                data=dict(status=status,message=message,updatedAt=stamp().isoformat())
                db.execute('INSERT OR REPLACE INTO invitation_replies VALUES(?,?,?,?)',(iid,sid,prior['version']+1,json.dumps(data,ensure_ascii=False)))
                if status=='accepted' and inv['kind']=='makeup':
                    origin=inv['origins'][sid]
                    row=db.execute('SELECT * FROM states WHERE id=?',(origin,)).fetchone()
                    meta=json.loads(row['data']) if row else {}
                    meta.update(attendance='makeup_pending',plannedMakeup=dict(studentId=sid,course=inv['course'],date=inv['date'],time=inv['time'],duration=inv['duration'],target=key(sid,inv['course'],inv['date'],inv['time']),invitationId=iid))
                    db.execute('INSERT OR REPLACE INTO states VALUES(?,?,?)',(origin,(row['version'] if row else 0)+1,json.dumps(meta,ensure_ascii=False)))
                db.execute('INSERT INTO audit(actor,action,target,created) VALUES(?,?,?,?)',(user['username'],action,iid,stamp().isoformat()))
                return {'ok':True}
            else: raise Problem(400,'无效操作')
            version=inv.pop('version')
            inv.pop('id')
            db.execute('UPDATE invitations SET version=?,data=? WHERE id=?',(version+1,json.dumps(inv,ensure_ascii=False),iid))
        db.execute('INSERT INTO audit(actor,action,target,created) VALUES(?,?,?,?)',(user['username'],action,iid,stamp().isoformat()))
    return {'ok':True,'id':iid}


def active_states(store, states):
    states=copy.deepcopy(states)
    active={(sid,x['invitationId']) for sid,slots in confirmed(store).items() for x in slots if x.get('invitationId')}
    for saved in states.values():
        plan=saved['data'].get('plannedMakeup') or {}
        if plan.get('invitationId') and (plan.get('studentId'),plan['invitationId']) not in active:
            saved['data'].pop('plannedMakeup',None)
    return states
