"""Follow-up projections only: never changes attendance records or hour balances."""
import datetime as dt
import hashlib
import json
import re

TZ = dt.timezone(dt.timedelta(hours=8))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def clean(value):
    if isinstance(value, dict):
        return '\n'.join(filter(None, [clean(value.get('title')), clean(value.get('text') or value.get('content')), clean(value.get('items') or value.get('points'))]))
    if isinstance(value, list):
        return '\n\n'.join(filter(None, (clean(v) for v in value)))
    if not isinstance(value, (str, int, float)):
        return ''
    return str(value).strip()


def clock(value):
    m = re.match(r'^(\d{1,2}):(\d{2})', str(value or '').strip())
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        return ''
    return f'{int(m[1]):02d}:{int(m[2]):02d}'


def end_time(day, source):
    explicit = source.get('actualEnd') or source.get('endTime')
    if explicit:
        try:
            value = dt.datetime.fromisoformat(explicit if 'T' in explicit else day + 'T' + explicit)
            return value.replace(tzinfo=TZ) if value.tzinfo is None else value.astimezone(TZ)
        except (ValueError, TypeError):
            return None
    span = str(source.get('time') or source.get('startTime') or '')
    start = clock(span)
    if not start:
        return None
    base = dt.datetime.fromisoformat(day + 'T' + start).replace(tzinfo=TZ)
    parts = re.split(r'[-~～–—]', span)
    finish = clock(parts[-1]) if len(parts) > 1 else ''
    if finish:
        value = dt.datetime.fromisoformat(day + 'T' + finish).replace(tzinfo=TZ)
        return value if value > base else None
    try:
        duration = float(source.get('duration') or 0)
    except (ValueError, TypeError):
        return None
    # Accounting "hours" is often a lesson count, not elapsed duration.
    return base + dt.timedelta(hours=duration) if 0 < duration <= 12 else None


def key(sid, course, day, start):
    return digest([sid, course, day, start])[:32]


def project(students, states, today=None, start=None, finish=None):
    now = today or dt.datetime.now(TZ)
    first = start or (now.date() - dt.timedelta(days=14)).isoformat()
    last = finish or now.date().isoformat()
    match_first = (dt.date.fromisoformat(first) - dt.timedelta(days=1)).isoformat()
    match_last = (dt.date.fromisoformat(last) + dt.timedelta(days=1)).isoformat()
    rows = []
    for student in students:
        sid, name, data = student['id'], student['name'], student['data']
        slots = {}

        def slot(course, day, time, source=None):
            k = key(sid, course, day, time)
            if k not in slots:
                slots[k] = dict(id=k, studentId=sid, studentName=name, course=course,
                                date=day, time=time, records=[], schedules=[], ambiguous=False)
            if source:
                slots[k]['schedules'].append(source)
            return slots[k]

        for schedule in data.get('schedule') or []:
            if not isinstance(schedule, dict) or not schedule.get('courseName'):
                continue
            try:
                dow = int(schedule.get('dayOfWeek')) % 7
            except (TypeError, ValueError):
                continue
            day = dt.date.fromisoformat(match_first)
            while day.isoformat() <= last:
                ds = day.isoformat()
                if (day.weekday() + 1) % 7 == dow and ds >= str(schedule.get('firstLesson') or first):
                    slot(schedule['courseName'], ds, clock(schedule.get('startTime') or schedule.get('time')), schedule)
                day += dt.timedelta(days=1)
        for schedule in data.get('oneTimeClasses') or []:
            if isinstance(schedule, dict) and match_first <= str(schedule.get('date') or '') <= last:
                slot(schedule.get('courseName') or '未命名课程', schedule['date'], clock(schedule.get('startTime') or schedule.get('time')), schedule)
        linked_days = set()
        for saved in states.values():
            actual_session = saved.get('data', {}).get('actualSession') or {}
            if actual_session.get('studentId') == sid and first <= actual_session.get('date', '') <= last:
                planned = actual_session.get('plannedDate')
                if planned and actual_session.get('course'):
                    slot(actual_session['course'], planned, actual_session.get('plannedTime',''),
                         {'time': actual_session.get('plannedTime','')})
                    linked_days.add(planned)
                    linked_days.add((dt.date.fromisoformat(planned) + dt.timedelta(days=1)).isoformat())
        plans = {}
        for origin, saved in states.items():
            plan = saved.get('data', {}).get('plannedMakeup')
            if plan and (plan.get('studentId') == sid or sid in (plan.get('studentIds') or [])) and first <= plan.get('date','') <= last:
                target = slot(plan['course'], plan['date'], plan['time'], dict(startTime=plan['time'], duration=plan['duration']))
                plans[target['id']] = origin
        for index, record in enumerate(data.get('records') or []):
            if not isinstance(record, dict) or not (match_first <= str(record.get('date') or '') <= match_last or record.get('date') in linked_days):
                continue
            if record.get('status') in ('draft', 'deleted', 'cancelled'):
                continue
            course, day = record.get('courseName') or '未命名课程', record['date']
            time = clock(record.get('time') or record.get('startTime'))
            possible = [r for r in slots.values() if r['course'] == course and r['date'] == day]
            if not time and len(possible) == 1:
                time = possible[0]['time']
            item = slots.get(key(sid, course, day, time))
            if item is None and time:
                previous_day = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
                candidate = slots.get(key(sid, course, previous_day, time))
                conflict = any(isinstance(request, dict) and request.get('date') == previous_day and
                               (request.get('courseName') or request.get('course')) in (None, '', course)
                               for request in data.get('leaveRequests') or [])
                saved_attendance = states.get(candidate['id'], {}).get('data', {}).get('attendance') if candidate else None
                if (candidate and candidate['schedules'] and not candidate['records'] and not conflict
                        and saved_attendance not in ('leave', 'makeup_pending', 'cancelled')):
                    item = candidate
            if item is None and time:
                # A saved record can carry its creation time instead of the lesson's
                # scheduled time. Link it only when one unambiguous same-day slot exists.
                candidates = [r for r in possible if r['schedules'] and not r['records'] and
                              states.get(r['id'], {}).get('data', {}).get('attendance') not in
                              ('leave', 'makeup_pending', 'cancelled')]
                if len(candidates) == 1:
                    item = candidates[0]
            if item is None:
                if not first <= day <= last:
                    continue
                item = slot(course, day, time)
            item['ambiguous'] |= not time and len(possible) > 1
            item['records'].append(dict(record, _index=index))
        for item in slots.values():
            sidate = item['date']
            state_row = states.get(item['id'], {})
            meta = state_row.get('data', {})
            records = item.pop('records')
            schedules = item.pop('schedules')
            same_day = [r for r in slots.values() if r['date'] == sidate]
            approved, uncertain, related_requests = [], [], []
            for leave in data.get('leaveRequests') or []:
                if not isinstance(leave, dict) or leave.get('date') != sidate or leave.get('status') in ('rejected', 'cancelled'):
                    continue
                if leave.get('type') in ('message', 'homework', 'feedback'):
                    continue
                lc = leave.get('courseName') or leave.get('course')
                lt = clock(leave.get('startTime') or leave.get('time'))
                if lc and lc != item['course'] or lt and lt != item['time']:
                    continue
                related_requests.append(leave)
                explicit_leave = leave.get('type') == 'leave' or leave.get('isLeave') is True
                if explicit_leave and leave.get('status') == 'approved' and lc and (lt or len(same_day) == 1):
                    approved.append(leave)
                elif explicit_leave:
                    uncertain.append(leave)
            actual = [end_time(sidate, r) for r in records]
            actual = [v for v in actual if v]
            scheduled = [end_time(sidate, s) for s in schedules]
            scheduled = [v for v in scheduled if v]
            finish_at = max(actual) if actual else (scheduled[0] if len(set(scheduled)) == 1 else None)
            if meta.get('actualEnd'):
                finish_at = end_time(sidate, meta)
            actual_session = meta.get('actualSession') or {}
            display_date = actual_session.get('date') or sidate
            source_time = ((schedules[0].get('time') or schedules[0].get('startTime')) if schedules else
                           (records[0].get('time') or records[0].get('startTime')) if records else item['time'])
            display_time = actual_session.get('time') or source_time or item['time']
            if not first <= display_date <= last:
                continue
            if actual_session:
                finish_at = end_time(display_date, {'time': display_time})
            upcoming = bool(display_date > now.date().isoformat() or finish_at and finish_at > now)
            auto = 'attended' if records else 'unknown'
            if approved:
                auto = 'unknown' if records else 'makeup_pending'
            if uncertain or item['ambiguous']:
                auto = 'unknown'
            attendance = meta.get('attendance', auto)
            if upcoming and attendance != 'cancelled': attendance = 'scheduled'
            content = '\n\n'.join(filter(None, [clean(r.get('keyKnowledge')) for r in records]))
            homework = '\n\n'.join(filter(None, [clean(r.get('homework')) for r in records]))
            image_values = [image for r in records for image in (r.get('homeworkImages') or [])]
            images = len(image_values)
            no_homework = bool(meta.get('noHomework'))
            if records and not approved and not uncertain and attendance == 'unknown':
                attendance = 'attended'
            missing = []
            if attendance == 'attended':
                if not content:
                    missing.append('content')
                if not homework and not images and not no_homework:
                    missing.append('homework')
            elapsed = max(0, (now - finish_at).total_seconds() / 3600) if finish_at else None
            # A saved lesson record is evidence of attendance. Missing timing should
            # affect elapsed reminders, not send the teacher back to attendance review.
            needs_check = attendance == 'unknown' and not records
            bucket = 'check' if needs_check else ('48' if elapsed is not None and elapsed >= 48 else '24' if elapsed is not None and elapsed >= 24 else 'within24')
            publication = digest([display_date, display_time, content, homework, image_values, no_homework])
            content_done = bool(content)
            homework_done = bool(homework or images or no_homework)
            item.update(date=display_date, time=display_time, plannedDate=sidate,
                        plannedTime=item['time'], content=content, homework=homework, homeworkImages=images,
                        noHomework=no_homework, recordCount=len(records),
                        recordIndices=[r['_index'] for r in records],
                        title=' / '.join(dict.fromkeys(r.get('lessonTitle') for r in records if r.get('lessonTitle'))),
                        attendance=attendance, needsCheck=needs_check, missing=missing, upcoming=upcoming, actualSession=actual_session,
                        contentDone=content_done, homeworkDone=homework_done,
                        leaveRequestCount=len(related_requests),
                        leaveRequests=[dict(id=x.get('id',''),type=x.get('type',''),status=x.get('status',''),handled=bool(x.get('handled') or x.get('status') in ('approved','accepted','resolved','rejected','cancelled')),reason=clean(x.get('reason') or x.get('message')),source=x.get('source','parent')) for x in related_requests],
                        bucket=bucket, elapsedHours=round(elapsed, 1) if elapsed is not None else None,
                        endedAt=finish_at.isoformat() if finish_at else '',
                        sent=bool(meta.get('sentAt')) and meta.get('sentPublication') == publication,
                        notificationChanged=bool(meta.get('sentAt')) and meta.get('sentPublication') != publication,
                        sentAt=meta.get('sentAt', ''), copiedAt=meta.get('copiedAt', ''),
                        publication=publication, stateVersion=state_row.get('version', 0),
                        sourceVersion=digest([records, schedules, approved, uncertain]),
                        makeupOrigin=meta.get('makeupOrigin', plans.get(item['id'], '')), makeupTarget=meta.get('makeupTarget', ''),
                        plannedMakeup=meta.get('plannedMakeup'),
                        warning=('请假与上课记录同时存在，请核对实际出勤。' if approved and records else
                                 '同日课程信息不明确，请核对出勤。' if uncertain or item['ambiguous'] else ''))
            item['ready'] = attendance == 'attended' and content_done and homework_done and not needs_check
            item['nextAction'] = ('处理请假/留言' if item['leaveRequestCount'] and not all(x.get('handled') for x in item['leaveRequests']) else
                                  '补充课堂内容' if not content_done and attendance == 'attended' else
                                  '补充家庭作业' if not homework_done and attendance == 'attended' else
                                  '发送家长通知' if item['ready'] and not item['sent'] else
                                  '查看家长反馈' if item['sent'] else '已完成')
            item['sessionId'] = key('', item['course'], item['date'], item['time'])
            rows.append(item)
    by_id = {r['id']: r for r in rows}
    for row in rows:
        plan = row.get('plannedMakeup')
        target = by_id.get(plan['target']) if plan else None
        if target and target['recordCount'] and target['attendance'] == 'attended' and not target['needsCheck']:
            row['makeupTarget'] = target['id']
        target = by_id.get(row.get('makeupTarget'))
        if target and target['recordCount'] and target['attendance'] == 'attended' and not target['needsCheck']:
            target['absorbedInto'] = row['id']
            target['makeupOrigin'] = row['id']
            row['makeupSession'] = {
                'id': target['id'], 'date': target['date'], 'time': target['time'],
                'title': target['title'], 'content': target['content'], 'homework': target['homework'],
                'homeworkImages': target['homeworkImages'], 'noHomework': target['noHomework'],
                'recordCount': target['recordCount'],
            }
            row.update(content=target['content'], homework=target['homework'],
                       homeworkImages=target['homeworkImages'], noHomework=target['noHomework'],
                       contentDone=target['contentDone'], homeworkDone=target['homeworkDone'],
                       recordCount=target['recordCount'], recordIndices=target['recordIndices'],
                       title=target['title'], needsCheck=False, missing=target['missing'],
                       publication=target['publication'])
            row['ready'] = target['ready']
            row['nextAction'] = target['nextAction']
    return sorted(rows, key=lambda r: (r['date'], r['time'], r['course'], r['studentName']), reverse=True)


def summary(rows):
    rows = [r for r in rows if not r.get('absorbedInto')]
    missing = [r for r in rows if r['attendance'] == 'attended' and r['missing'] and not r['needsCheck']]
    def counts(items):
        return dict(sessions=len({r['sessionId'] for r in items}), students=len({r['studentId'] for r in items}), tasks=len(items))
    return dict(missing=counts(missing), over24=counts([r for r in missing if r['bucket'] in ('24','48')]),
                over48=counts([r for r in missing if r['bucket']=='48']),
                within24=counts([r for r in missing if r['bucket']=='within24']),
                check=sum(r['needsCheck'] for r in rows),
                makeup=sum(r['attendance']=='makeup_pending' and not r['makeupTarget'] for r in rows),
                unsent=sum(r['ready'] and not r['sent'] for r in rows))


def stage(row):
    if row.get('upcoming'): return 'scheduled'
    if row['attendance'] in ('makeup_pending', 'leave') and not row.get('makeupTarget'):
        return 'makeup'
    if row['attendance'] == 'cancelled':
        return 'cancelled'
    if row['needsCheck'] or row['attendance'] == 'unknown':
        return 'check'
    if not row['contentDone']:
        return 'content'
    if not row['homeworkDone']:
        return 'homework'
    if not row['sent']:
        return 'notify_updated' if row.get('notificationChanged') else 'notify'
    if row.get('openFeedbackCount'):
        return 'feedback'
    return 'done' if row.get('feedbackCount') else 'awaiting_feedback'
