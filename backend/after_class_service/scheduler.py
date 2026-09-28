"""Explainable, read-only recommendation engine for holiday and personal make-up lessons."""
import datetime as dt
import json
from pathlib import Path

from model import TZ, clock, end_time

CALENDAR = Path(__file__).with_name('holiday-calendar.json')


def minutes(value):
    h, m = map(int, value.split(':'))
    return h * 60 + m


def stamp(total):
    return f'{total // 60:02d}:{total % 60:02d}'


def duration_hours(source):
    raw = source.get('duration')
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0
    # Existing data mixes hours and minutes.
    if value > 12:
        return value / 60
    if value > 0:
        return value
    span = str(source.get('startTime') or source.get('time') or '')
    parts = span.replace('～', '-').replace('~', '-').split('-')
    if len(parts) == 2 and clock(parts[0]) and clock(parts[1]):
        return (minutes(clock(parts[1])) - minutes(clock(parts[0]))) / 60
    return 1


def period(value):
    point = minutes(value)
    return '上午' if point < 12 * 60 else '下午' if point < 18 * 60 else '晚上'


def intervals(students, invitations):
    result = []
    for student in students:
        for source in (student['data'].get('schedule') or []):
            if not isinstance(source, dict) or not source.get('courseName'):
                continue
            try:
                weekday = int(source.get('dayOfWeek')) % 7
            except (TypeError, ValueError):
                continue
            result.append({'recurring': True, 'weekday': weekday, 'start': clock(source.get('startTime') or source.get('time')),
                           'duration': duration_hours(source), 'course': source['courseName']})
        for source in (student['data'].get('oneTimeClasses') or []):
            if isinstance(source, dict) and source.get('date') and clock(source.get('startTime') or source.get('time')):
                result.append({'date': source['date'], 'start': clock(source.get('startTime') or source.get('time')),
                               'duration': duration_hours(source), 'course': source.get('courseName', '')})
    for invite in invitations:
        if invite.get('status') in ('published', 'locked'):
            result.append({'date': invite['date'], 'start': invite['time'], 'duration': float(invite['duration']), 'course': invite['course']})
    return result


def conflict(day, start, duration, occupied, own=None):
    begin = minutes(start)
    finish = begin + round(duration * 60)
    weekday = (dt.date.fromisoformat(day).weekday() + 1) % 7
    for item in occupied:
        if item.get('recurring') and item.get('weekday') != weekday:
            continue
        if not item.get('recurring') and item.get('date') != day:
            continue
        if own and day == own['date'] and item.get('course') == own['course'] and item.get('start') == own['start']:
            continue
        other = minutes(item['start'])
        if begin < other + round(item['duration'] * 60) and other < finish:
            return True
    return False


def candidate_slots(task, days, occupied):
    original = task['originalTime']
    duration = task['duration']
    slots = []
    for day in days:
        windows = [(9 * 60, 17 * 60), (18 * 60 + 30, 20 * 60 + 40)]
        for low, high in windows:
            for point in range(low, high - round(duration * 60) + 1, 30):
                value = stamp(point)
                own = {'date': task['originDate'], 'course': task['course'], 'start': task['originalTime']}
                if conflict(day['date'], value, duration, occupied, own):
                    continue
                delta = abs(point - minutes(original))
                score = (30 if delta == 0 else 15 if delta <= 30 else 8 if delta <= 60 else 0)
                reasons = []
                if delta == 0:
                    reasons.append('与原上课时间完全一致')
                if period(value) == period(original):
                    score += 20
                    reasons.append('与原课程同为' + period(original))
                if dt.date.fromisoformat(day['date']).weekday() == dt.date.fromisoformat(task['originDate']).weekday():
                    score += 10
                    reasons.append('与原课程星期相同')
                # Short personal lessons belong at block edges, preserving long blocks.
                if task['kind'] == 'individual_makeup' and duration <= .75 and point not in (low, high - round(duration * 60)):
                    score -= 12
                else:
                    score += 5
                slots.append({'date': day['date'], 'time': value, 'duration': duration,
                              'score': score, 'reason': '；'.join(reasons[:2]) or '假期可用时段',
                              'holidayName': day['name'], 'status': 'RECOMMENDED' if score >= 35 else 'AVAILABLE'})
    slots.sort(key=lambda item: (-item['score'], item['date'], item['time']))
    return slots


def build(students, states, rows, invitations, now=None):
    today = (now or dt.datetime.now(TZ)).date()
    calendar = json.loads(CALENDAR.read_text(encoding='utf-8'))
    days = [day for day in calendar['days'] if day.get('student_daytime_available') and day['date'] >= today.isoformat()]
    occupied = intervals(students, invitations)
    tasks = []
    for row in rows:
        if row['attendance'] not in ('leave', 'makeup_pending') or row.get('makeupTarget'):
            continue
        original_duration = 1
        if row.get('plannedTime') and '-' in row['plannedTime']:
            start, end = row['plannedTime'].split('-', 1)
            if clock(start) and clock(end):
                original_duration = max(.25, (minutes(clock(end)) - minutes(clock(start))) / 60)
        personal = .5 if original_duration <= 1.5 else .75 if original_duration <= 2 else 1
        tasks.append({'id': 'individual:' + row['id'], 'kind': 'individual_makeup', 'label': row['studentName'] + ' · 个人补课',
                      'course': row['course'], 'studentIds': [row['studentId']], 'originIds': {row['studentId']: row['id']},
                      'originDate': row['plannedDate'], 'originalTime': row['plannedTime'][:5],
                      'originalDuration': original_duration, 'duration': personal})
    groups = {}
    for student in students:
        for schedule in student['data'].get('schedule') or []:
            if not isinstance(schedule, dict) or not schedule.get('courseName') or not clock(schedule.get('startTime') or schedule.get('time')):
                continue
            key = (schedule['courseName'], int(schedule.get('dayOfWeek', 0)) % 7, clock(schedule.get('startTime') or schedule.get('time')), duration_hours(schedule))
            groups.setdefault(key, []).append(student['id'])
    for (course, weekday, start, duration), student_ids in groups.items():
        for day in days:
            value = dt.date.fromisoformat(day['date'])
            if (value.weekday() + 1) % 7 != weekday:
                continue
            tasks.append({'id': 'class:' + course + ':' + day['date'] + ':' + start, 'kind': 'class_holiday_makeup',
                          'label': course + ' · 整班补课', 'course': course, 'studentIds': sorted(set(student_ids)),
                          'originIds': {}, 'originDate': day['date'], 'originalTime': start,
                          'originalDuration': duration, 'duration': duration})
    for task in tasks:
        candidates = candidate_slots(task, days, occupied)
        task['candidates'] = candidates[:12]
        task['candidateCount'] = len(candidates)
        task['schedulingDifficulty'] = max(0, 100 - min(90, len(candidates) * 3))
        task['priorityReason'] = '候选较少，建议优先安排' if len(candidates) <= 5 else '时间选择较灵活'
    tasks.sort(key=lambda task: (-task['schedulingDifficulty'], task['originDate'], task['course']))
    return {'region': calendar['region'], 'calendarUpdated': calendar['updated'], 'tasks': tasks,
            'allowedWindows': ['09:00-17:00', '18:30-20:40'],
            'statuses': ['AVAILABLE', 'RECOMMENDED', 'PENDING', 'LOCKED', 'UNAVAILABLE']}
