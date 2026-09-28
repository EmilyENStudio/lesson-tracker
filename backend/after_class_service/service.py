"""Authenticated follow-up service; source student APIs remain read-only."""
import datetime as dt
import json
import os
import re
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from model import TZ, project, summary, end_time, key, stage
from common import Problem, string

ORIGIN = os.environ.get('FOLLOWUP_AUTH_ORIGIN', 'http://127.0.0.1:5000')


def remote(path, token):
    try:
        with urlopen(Request(ORIGIN + path, headers={'Authorization': 'Bearer ' + token}), timeout=8) as response:
            return json.load(response)
    except HTTPError as exc:
        raise Problem(401 if exc.code in (401,403) else 503, '登录已过期' if exc.code in (401,403) else '上课记录服务暂不可用，请稍后刷新') from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise Problem(503, '上课记录服务暂不可用，请稍后刷新') from exc


def remote_post(path, token, payload):
    raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    try:
        request = Request(ORIGIN + path, data=raw, method='POST', headers={
            'Authorization': 'Bearer ' + token,
            'Content-Type': 'application/json; charset=utf-8',
        })
        with urlopen(request, timeout=8) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            message = json.load(exc).get('error')
        except Exception:
            message = ''
        raise Problem(exc.code if 400 <= exc.code < 500 else 503,
                      message or ('登录已过期' if exc.code in (401, 403) else '请假记录保存失败，请刷新后重试')) from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise Problem(503, '请假记录服务暂不可用，请稍后刷新') from exc


def source(user, token):
    if user['role'] == 'teacher':
        roster = remote('/api/students/list', token)
        if isinstance(roster, dict):
            roster = roster.get('students', [])
        roster = [s for s in roster if s.get('id') not in ('teacher', user['username'])]
    else:
        roster = [dict(id=user['username'], name=user.get('name') or user['username'])]
    def load(s):
        data = remote('/api/students/' + quote(str(s['id']), safe=''), token)
        return dict(id=str(s['id']), name=data.get('name') or s.get('name') or str(s['id']), data=data)
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(load, roster))


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS states(id TEXT PRIMARY KEY, version INTEGER NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS contacts(student_id TEXT PRIMARY KEY, nickname TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS feedback(id TEXT PRIMARY KEY, item_id TEXT NOT NULL, student_id TEXT NOT NULL,
                  version INTEGER NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
                  target TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS invitations(id TEXT PRIMARY KEY, version INTEGER NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS invitation_replies(invitation_id TEXT NOT NULL, student_id TEXT NOT NULL,
                  version INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(invitation_id,student_id));
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def snapshot(self):
        with self.db() as db:
            states = {r['id']: dict(version=r['version'], data=json.loads(r['data'])) for r in db.execute('SELECT * FROM states')}
            contacts = {r['student_id']:r['nickname'] for r in db.execute('SELECT * FROM contacts')}
            feedback = [dict(json.loads(r['data']), id=r['id'], itemId=r['item_id'], studentId=r['student_id'],version=r['version']) for r in db.execute('SELECT * FROM feedback ORDER BY rowid DESC')]
        return states, contacts, feedback

    def save(self, item, state, actor, action):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT version FROM states WHERE id=?', (item['id'],)).fetchone()
            version = row['version'] if row else 0
            if version != item['stateVersion']:
                raise Problem(409, '状态已被更新，请刷新后重试')
            db.execute('INSERT OR REPLACE INTO states VALUES(?,?,?)', (item['id'],version+1,json.dumps(state,ensure_ascii=False)))
            db.execute('INSERT INTO audit(actor,action,target,created) VALUES(?,?,?,?)',(actor,action,item['id'],dt.datetime.now(TZ).isoformat()))

    def save_many(self, entries, actor, action):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for item, _state in entries:
                row = db.execute('SELECT version FROM states WHERE id=?', (item['id'],)).fetchone()
                version = row['version'] if row else 0
                if version != item['stateVersion']:
                    raise Problem(409, '同课补课状态已被更新，请刷新后重试')
            for item, state in entries:
                row = db.execute('SELECT version FROM states WHERE id=?', (item['id'],)).fetchone()
                version = row['version'] if row else 0
                db.execute('INSERT OR REPLACE INTO states VALUES(?,?,?)', (item['id'],version+1,json.dumps(state,ensure_ascii=False)))
                db.execute('INSERT INTO audit(actor,action,target,created) VALUES(?,?,?,?)',(actor,action,item['id'],dt.datetime.now(TZ).isoformat()))


def date_range(query):
    now = dt.datetime.now(TZ)
    first = query.get('from', [(now.date()-dt.timedelta(days=14)).isoformat()])[0]
    last = query.get('to', [now.date().isoformat()])[0]
    try:
        span = (dt.date.fromisoformat(last)-dt.date.fromisoformat(first)).days
    except (ValueError, TypeError):
        raise Problem(400,'日期不正确')
    if span < 0 or span > 90 or last > (now.date()+dt.timedelta(days=365)).isoformat():
        raise Problem(400,'请选择跨度不超过90天的日期范围')
    return first, last


def dashboard(store, user, token, query, loader=source):
    first, last = date_range(query)
    students = loader(user,token)
    from invitations import enrich
    students = enrich(store, students)
    states, contacts, feedback = store.snapshot()
    from invitations import active_states
    states = active_states(store, states)
    rows = project(students,states,start=first,finish=last)
    allowed = {s['id'] for s in students}
    feedback = [f for f in feedback if f['studentId'] in allowed]
    if user['role'] != 'teacher':
        rows = [r for r in rows if r['recordCount'] and r['attendance']=='attended']
        # Parent response has no internal attendance decisions or contact directory.
        rows = [{k:r[k] for k in ('id','studentId','studentName','course','date','time','title','content','homework','homeworkImages','noHomework')} for r in rows]
        feedback = [f for f in feedback if f.get('source')=='website']
        return dict(rows=rows,feedback=feedback,fromDate=first,toDate=last), students, states
    feedback_counts = {}
    open_counts = {}
    for entry in feedback:
        feedback_counts[entry['itemId']] = feedback_counts.get(entry['itemId'], 0) + 1
        if entry.get('status') != 'resolved':
            open_counts[entry['itemId']] = open_counts.get(entry['itemId'], 0) + 1
    for row in rows:
        row['feedbackCount'] = feedback_counts.get(row['id'], 0)
        row['openFeedbackCount'] = open_counts.get(row['id'], 0)
        row['stage'] = stage(row)
    visible_rows = [row for row in rows if not row.get('absorbedInto')]
    messages = []
    for student in students:
        for request in student['data'].get('leaveRequests') or []:
            if not isinstance(request, dict):
                continue
            day = str(request.get('date') or '')
            if not first <= day <= last or request.get('status') in ('rejected', 'cancelled'):
                continue
            messages.append(dict(
                studentId=student['id'], studentName=student['name'],
                date=day, course=request.get('courseName') or request.get('course') or '',
                time=request.get('startTime') or request.get('time') or '',
                type=request.get('type') or ('leave' if request.get('isLeave') else 'message'),
                status=request.get('status') or 'pending', handled=bool(request.get('handled') or request.get('status') in ('approved','accepted','resolved','rejected','cancelled')),
                reason=request.get('reason') or request.get('message') or '',
                source=request.get('source') or 'parent'))
    return dict(rows=visible_rows,counts=summary(visible_rows),contacts=contacts,feedback=feedback,messages=messages,
                 students=[dict(id=s['id'],name=s['name'],courses=sorted({x.get('courseName') for field in ('schedule','payments','records','oneTimeClasses') for x in (s['data'].get(field) or []) if isinstance(x,dict) and x.get('courseName')})) for s in students],fromDate=first,toDate=last), students, states


def student_cancellations(store, user, token, query, loader=source):
    """Return only this student's cancelled occurrences for the student home schedule."""
    first, last = date_range(query)
    students = loader(user, token)
    from invitations import enrich, active_states
    students = enrich(store, students)
    states, _contacts, _feedback = store.snapshot()
    rows = project(students, active_states(store, states), start=first, finish=last)
    return dict(cancelled=[dict(date=r.get('date'), time=r.get('time'), course=r.get('course'))
                           for r in rows if r.get('attendance') == 'cancelled'])


def scheduler_report(store, user, token, loader=source):
    if user['role'] != 'teacher':
        raise Problem(403, '仅教师可访问智能排课')
    students = loader(user, token)
    states, _, _ = store.snapshot()
    from invitations import active_states, read
    states = active_states(store, states)
    today = dt.datetime.now(TZ).date()
    rows = project(students, states, start=(today - dt.timedelta(days=90)).isoformat(), finish=today.isoformat())
    with store.db() as db:
        invitations, _ = read(db)
    from scheduler import build
    return build(students, states, rows, invitations)


def mutate(store,user,token,body,loader=source):
    action = body.get('action')
    if user['role'] != 'teacher' and action != 'feedback':
        raise Problem(403,'仅教师可操作')
    query={'from':[body.get('from') or (dt.datetime.now(TZ).date()-dt.timedelta(days=14)).isoformat()], 'to':[body.get('to') or dt.datetime.now(TZ).date().isoformat()]}
    report,students,states = dashboard(store,user,token,query,loader)
    now = dt.datetime.now(TZ).isoformat()
    actor = user['username']
    if action == 'contact':
        sid=body.get('studentId')
        if sid not in {s['id'] for s in students}: raise Problem(404,'未找到学员')
        nickname=string(body.get('nickname',''),160)
        with store.db() as db: db.execute('INSERT OR REPLACE INTO contacts VALUES(?,?)',(sid,nickname))
        return {'ok':True}
    if action in ('reply','reply_sent'):
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM feedback WHERE id=?',(body.get('feedbackId'),)).fetchone()
            if not row: raise Problem(404,'未找到反馈')
            if row['version'] != body.get('version'): raise Problem(409,'反馈已变化，请刷新')
            data=json.loads(row['data'])
            if action=='reply':
                data['reply']=string(body.get('reply',''),6000)
                if not data['reply']: raise Problem(400,'请填写回复')
                data['status']='resolved' if data['source']=='website' else 'reply_pending'
                data['replyAt']=now
            else:
                if data.get('source')!='wechat' or not data.get('reply'): raise Problem(400,'请先保存微信回复草稿')
                data.update(status='resolved',replySentAt=now)
            db.execute('UPDATE feedback SET version=version+1,data=? WHERE id=?',(json.dumps(data,ensure_ascii=False),row['id']))
        return {'ok':True}
    item=next((r for r in report['rows'] if r['id']==body.get('itemId')),None)
    if not item: raise Problem(404,'课次已变化或不在当前日期范围内，请刷新')
    if action=='feedback':
        request_id=string(body.get('requestId',''),64)
        try: uuid.UUID(request_id)
        except ValueError: raise Problem(400,'无效请求编号')
        message=string(body.get('message',''),6000)
        category=body.get('category')
        if not message or category not in ('已完成未提交','不会做','提交失败','申请延期','其他'): raise Problem(400,'请填写反馈内容并选择分类')
        data=dict(message=message,category=category,source='wechat' if user['role']=='teacher' else 'website',
                  course=item['course'],date=item['date'],studentName=item['studentName'],created=now,status='open',reply='')
        with store.db() as db:
            prior=db.execute('SELECT student_id,item_id,data FROM feedback WHERE id=?',(request_id,)).fetchone()
            if prior and (prior['student_id']!=item['studentId'] or prior['item_id']!=item['id']): raise Problem(409,'请求编号已占用')
            if prior and any(json.loads(prior['data']).get(k)!=data[k] for k in ('message','category')): raise Problem(409,'这次反馈已保存且内容不同，请刷新核对后再新增反馈')
            if not prior: db.execute('INSERT INTO feedback VALUES(?,?,?,?,?)',(request_id,item['id'],item['studentId'],1,json.dumps(data,ensure_ascii=False)))
        return {'ok':True}
    if item['sourceVersion']!=body.get('sourceVersion') or item['stateVersion']!=body.get('version'):
        raise Problem(409,'上课记录或跟进状态已更新，请刷新后重试')
    state=dict(states.get(item['id'],{}).get('data',{}))
    group_updates=[]
    if action=='teacher_leave':
        if item['recordCount']:
            raise Problem(409,'该学员本课次已有上课记录，请先核对是否实际出勤')
        reason=string(body.get('reason',''),1000) or '家长通过微信请假'
        duration=body.get('duration')
        remote_post('/api/students/' + quote(str(item['studentId']), safe='') + '/leave', token, {
            'date': item['plannedDate'], 'courseName': item['course'],
            'startTime': item['plannedTime'] or item['time'], 'duration': duration,
            'reason': reason,
        })
        state.update(attendance='makeup_pending', leaveSource='teacher_wechat', leaveRecordedAt=now)
    elif action=='attendance':
        value=body.get('attendance')
        if value not in ('attended','makeup_pending','leave','cancelled','unknown'): raise Problem(400,'无效出勤状态')
        actual=body.get('actualEnd','')
        if actual:
            end=end_time(item['date'],{'actualEnd':string(actual,40)})
            if not end or end.date().isoformat()!=item['date'] or end>dt.datetime.now(TZ): raise Problem(400,'请填写该课次当天、已经结束的实际下课时间')
            state['actualEnd']=end.isoformat()
        if value=='attended' and not item['recordCount']: raise Problem(400,'请先保存真实上课记录，再标记已上课')
        if value=='attended' and item.get('makeupOrigin') and not item['recordCount']: raise Problem(400,'请先保存真实补课记录，按现有规则计入课时后再跟进')
        state['attendance']=value
    elif action=='clear_session_time':
        state.pop('actualSession',None)
    elif action=='unlink_makeup':
        state.pop('makeupTarget',None)
        state.pop('plannedMakeup',None)
        state.pop('actualSession',None)
        state['attendance']='makeup_pending'
    elif action=='delete_makeup_class':
        if not state.get('plannedMakeup'):
            raise Problem(404,'未找到可删除的计划补课班次')
        group_id=(state.get('plannedMakeup') or {}).get('groupId')
        state.pop('makeupTarget',None)
        state.pop('plannedMakeup',None)
        state.pop('actualSession',None)
        state['attendance']='makeup_pending'
        if group_id:
            for peer in report['rows']:
                plan=peer.get('plannedMakeup') or {}
                if peer['id'] != item['id'] and plan.get('groupId') == group_id:
                    peer_state=dict(states.get(peer['id'],{}).get('data',{}))
                    peer_state.pop('makeupTarget',None); peer_state.pop('plannedMakeup',None); peer_state.pop('actualSession',None)
                    peer_state['attendance']='makeup_pending'
                    group_updates.append((peer,peer_state))
    elif action=='cancel_class':
        class_rows=[r for r in report['rows'] if r['course']==item['course'] and r['date']==item['date'] and r['time']==item['time'] and r['sessionId']==item['sessionId']]
        if any(r['recordCount'] for r in class_rows):
            raise Problem(409,'该班次已有上课记录，不能取消，请先核对出勤')
        state.pop('actualSession',None)
        state['attendance']='cancelled'
        state['cancelledAt']=now
        state['cancelReason']=string(body.get('reason',''),200) or '教师取消本次班次'
        for peer in class_rows:
            if peer['id']==item['id']: continue
            peer_state=dict(states.get(peer['id'],{}).get('data',{}))
            peer_state.pop('actualSession',None)
            peer_state['attendance']='cancelled'
            peer_state['cancelledAt']=now
            peer_state['cancelReason']=state['cancelReason']
            group_updates.append((peer,peer_state))
    elif action=='session_time':
        day=string(body.get('date',''),10)
        time=string(body.get('time',''),11)
        try:
            chosen=dt.date.fromisoformat(day)
        except ValueError:
            raise Problem(400,'请选择有效的上课日期')
        if chosen > dt.datetime.now(TZ).date() or chosen < dt.date.fromisoformat(item['plannedDate']) - dt.timedelta(days=30):
            raise Problem(400,'实际课次日期必须是已发生的课程日期')
        end=end_time(day, {'time':time})
        if not re.fullmatch(r'\d{1,2}:\d{2}-\d{1,2}:\d{2}', time) or not end or end > dt.datetime.now(TZ):
            raise Problem(400,'请填写当天已结束的上课时间段，例如17:15-18:15')
        state['actualSession']={'date':day,'time':time,'studentId':item['studentId'],
                                'course':item['course'],'plannedDate':item['plannedDate'],
                                'plannedTime':item['plannedTime']}
    elif action=='no_homework':
        if item['attendance']!='attended': raise Problem(400,'请先核对出勤')
        state['noHomework']=body.get('value') is True
    elif action in ('copied','sent'):
        if not item['ready']: raise Problem(400,'请先补齐上课内容和家庭作业，或标明本次无作业')
        if action=='sent':
            if body.get('publication') != item['publication']: raise Problem(409,'通知内容已更新，请重新复制并核对')
            state.update(sentAt=now,sentPublication=item['publication'])
        else: state['copiedAt']=now
    elif action=='plan_makeup':
        day=string(body.get('date',''),10); time=clock_input=string(body.get('time',''),5)
        from model import clock
        if clock(time)!=time: raise Problem(400,'补课时间不正确')
        try:
            date=dt.date.fromisoformat(day); duration=float(body.get('duration',0))
        except (ValueError,TypeError): raise Problem(400,'补课时间不正确')
        if date<dt.datetime.now(TZ).date() or not 0<duration<=8: raise Problem(400,'请选择未来的补课时间及时长')
        target=key(item['studentId'],item['course'],day,time)
        if target==item['id']: raise Problem(400,'补课不能指向原课次')
        if item.get('makeupSession'): raise Problem(409,'请先解除已完成补课关联')
        from invitations import conflicts, read, enrich
        group_ids=[str(x) for x in (body.get('groupStudentIds') or []) if x]
        if item['studentId'] not in group_ids: group_ids.insert(0,item['studentId'])
        peer_rows=[]
        for peer in report['rows']:
            if peer['studentId'] in group_ids and peer['course']==item['course'] and peer['attendance'] in ('leave','makeup_pending') and not peer.get('makeupTarget'):
                peer_rows.append(peer)
        if len(peer_rows) != len(set(group_ids)):
            raise Problem(400,'同班补课只能选择同一课程下仍待补课的学员')
        group_id='makeup-'+uuid.uuid4().hex[:16]
        with store.db() as db:
            invitations, replies=read(db)
        candidate=dict(id='plan-'+item['id'],date=day,time=time,duration=duration,studentIds=group_ids)
        other_students=enrich(store,students)
        for student in other_students:
            student['data']['oneTimeClasses']=[x for x in student['data'].get('oneTimeClasses',[]) if not (student['id'] in group_ids and x.get('date')==day and (x.get('startTime') or x.get('time'))==time and (x.get('courseName') or x.get('course'))==item['course'] and x.get('kind')=='makeup')]
            synthetic=[]
            for peer in report['rows']:
                if peer['studentId'] != student['id']: continue
                plan=peer.get('plannedMakeup') or {}
                if plan and peer['studentId'] not in group_ids:
                    synthetic.append(dict(date=plan.get('date'),time=plan.get('time'),duration=plan.get('duration'),status='scheduled'))
            student['data']['oneTimeClasses']=list(student['data'].get('oneTimeClasses') or [])+synthetic
        for student in other_students:
            student['data']['oneTimeClasses']=[x for x in student['data'].get('oneTimeClasses',[]) if x.get('id')!='plan-'+item['id']]
        group_students=[student for student in other_students if student['id'] in group_ids]
        conflicts(candidate,group_students,invitations,replies)
        state.pop('actualSession',None)
        state.pop('makeupTarget',None)
        plan=dict(date=day,time=time,duration=duration,studentId=item['studentId'],studentIds=group_ids,groupId=group_id,course=item['course'],target=target)
        state.update(attendance='makeup_pending',plannedMakeup=plan)
        for peer in peer_rows:
            if peer['id']==item['id']: continue
            peer_state=dict(states.get(peer['id'],{}).get('data',{}))
            peer_state.pop('actualSession',None); peer_state.pop('makeupTarget',None)
            peer_state.update(attendance='makeup_pending',plannedMakeup=dict(plan,studentId=peer['studentId'],target=key(peer['studentId'],peer['course'],day,time)))
            group_updates.append((peer,peer_state))
    elif action=='link_makeup':
        target=next((r for r in report['rows'] if r['id']==body.get('target')),None)
        if not target or target['id']==item['id'] or target['studentId']!=item['studentId'] or target['course']!=item['course'] or target['attendance']!='attended' or not target['recordCount'] or target['needsCheck']:
            raise Problem(400,'请选择同一学员、同一课程已经完成的补课记录')
        state.update(attendance='makeup_pending',makeupTarget=target['id'])
    else: raise Problem(400,'无效操作')
    state['updatedAt']=now
    if group_updates:
        store.save_many([(item,state), *group_updates], actor, action)
    else:
        store.save(item,state,actor,action)
    return {'ok':True}


def handler(store, loader=source, verify=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def setup(self):
            super().setup()
            self.connection.settimeout(20)
        def respond(self,status,data):
            raw=json.dumps(data,ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers(); self.wfile.write(raw)
        def run_request(self):
            try:
                url=urlsplit(self.path)
                auth=self.headers.get('Authorization','')
                token=auth[7:] if auth.startswith('Bearer ') else ''
                if not token or len(token)>4096: raise Problem(401,'请先登录')
                user=verify(token) if verify else remote('/api/auth/me',token)
                if user.get('role') not in ('teacher','student') or not user.get('username'): raise Problem(403,'该账号不可访问')
                if user.get('readOnly') and self.command!='GET': raise Problem(403,'Demo account is read-only')
                if self.command=='GET' and url.path=='/api/after-class/invitations':
                    from invitations import listing
                    data=listing(store,user)
                elif self.command=='GET' and url.path=='/api/after-class/scheduler':
                    data=scheduler_report(store,user,token,loader)
                elif self.command=='GET' and url.path in ('/api/after-class/dashboard','/api/after-class/parent'):
                    if url.path.endswith('/dashboard') and user['role']!='teacher': raise Problem(403,'仅教师可访问')
                    data=dashboard(store,user,token,parse_qs(url.query),loader)[0]
                elif self.command=='GET' and url.path=='/api/after-class/student-cancellations':
                    if user['role']!='student': raise Problem(403,'仅学员可访问')
                    data=student_cancellations(store,user,token,parse_qs(url.query),loader)
                elif self.command=='POST' and url.path=='/api/after-class/action':
                    if self.headers.get('Content-Type','').split(';')[0]!='application/json': raise Problem(415,'请使用JSON')
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=32768: raise Problem(413,'请求过大')
                    body=json.loads(self.rfile.read(length))
                    if not isinstance(body,dict): raise Problem(400,'请求不正确')
                    if str(body.get('action','')).startswith('invite_'):
                        from invitations import mutate as invite_mutate
                        data=invite_mutate(store,user,token,body,loader)
                    else:
                        data=mutate(store,user,token,body,loader)
                else: raise Problem(404,'未找到接口')
                self.respond(200,data)
            except Problem as e: self.respond(e.status,{'error':e.message})
            except (ValueError,TypeError,UnicodeError): self.respond(400,{'error':'请求格式不正确'})
            except Exception: self.respond(503,{'error':'跟进服务暂不可用，请刷新后核实，不要重复提交'})
        do_GET=do_POST=run_request
    return Handler


if __name__=='__main__':
    os.umask(0o077)
    store=Store(os.environ.get('FOLLOWUP_DB','/var/lib/emily-after-class/followup.sqlite3'))
    ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('FOLLOWUP_PORT','5002'))),handler(store)).serve_forever()
