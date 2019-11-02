import datetime
from dateutil.parser import parse


def output(entries, write):
    entries = entries[:]
    entries.reverse()

    snappedRows = {}
    for uri, created, creator, msg, row in entries:
        t = parse(created)
        if 15 < t.minute < 45:
            snap = t.replace(minute=30, second=0, microsecond=0)
        else:
            minute = t.minute
            snap = t.replace(minute=0, second=0, microsecond=0)
            if minute > 30:
                snap = snap + datetime.timedelta(hours=1)
        snappedRows.setdefault(snap, []).append((t, msg, row))

    write('<table>')
    write('<tr>')
    write(''.join('<th>%s</th>' % h for h in [
        'study date',
        'study time',
        '',
        'diary record time',
        '0=asleep, 1=awake',
        '0=off, 1=on',
        '1=working hours',
        '',
        '1=non-troublesome dyskinesia',
        '1=troublesome dyskinesia',
        '1=non-troublesome tremor',
        '1=troublesome tremor',
        'med taken',
        'raw input']))
    write('</tr>')
    t = parse(entries[0][1])
    t = t.replace(minute=0 if t.minute < 30 else 30, second=0, microsecond=0)
    endTime = parse(entries[-1][1])

    def writeTd(s): return write('<td>%s</td>' % s)

    while t <= endTime:
        t = t + datetime.timedelta(minutes=30)
        write('<tr>')
        writeTd(t.date().isoformat())
        writeTd(t.time().strftime('%H:%M'))
        writeTd('')

        matchRows = snappedRows.get(t, [])

        stateWords = set()
        for msg in [r[1] for r in matchRows]:
            if msg.lower() in ['on', 'nttr', 'trtr', 'ok', 'ntdys']:
                stateWords.add(msg.lower())

        writeTd(''.join([r[0].strftime('%Y-%m-%d %a %H:%M')
                         for r in matchRows[:1]]))

        writeTd('?')  # awake
        writeTd((1 if ('on' in stateWords or 'ok' in stateWords)
                 else (0 if stateWords else '')))
        writeTd('')  # working
        writeTd('')  # spacer
        writeTd((1 if 'ntdys' in stateWords else (0 if stateWords else '')))
        writeTd((1 if 'trdys' in stateWords else (0 if stateWords else '')))
        writeTd((1 if 'nttr' in stateWords else (0 if stateWords else '')))
        writeTd((1 if 'trtr' in stateWords else (0 if stateWords else '')))
        writeTd('; '.join(r[1] for r in matchRows if r[1].startswith('[si]')))
        writeTd('; '.join('%s %s' % (r[0].strftime('%H:%M'), r[1])
                          for r in matchRows if not r[1].startswith('[si]')))

        write('</tr>')
    write('</table>')
