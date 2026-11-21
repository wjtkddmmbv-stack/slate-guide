#!/usr/bin/env python3
"""Builds Slate's TV guide: merges free XMLTV files, keeps the next 36 hours, and splits the result into a small
channel index and packs of ~60 channels each, so a TV only downloads what it shows.

  python3 build.py <out_dir> <xmltv files (.xml or .xml.gz)...>

Output:
  meta.json        {"v": build time}                       (tiny; lets Slate check for a new guide)
  index.json       {"v", "base", "ch": [[id, [names], pack], ...]}
  p/<n>.json       {"<id>": [[start, minutes, title, description], ...]}   start = minutes after "base"
Times are UTC. Channels without programmes in the window are left out. Uses only the standard library."""
import calendar, gzip, json, os, re, sys, time
from xml.etree.ElementTree import iterparse

HOURS = 36
PACK = 60
DESC = 140
NOW = int(time.time())
BASE = (NOW // 3600 - 6) * 3600            # a little before now, so a programme already running is kept
END = NOW + HOURS * 3600


def when(s):
    s = (s or '').strip()
    try:
        t = calendar.timegm(time.strptime(s[:14], '%Y%m%d%H%M%S'))
    except ValueError:
        return 0
    off = s[14:].strip()
    if len(off) == 5 and off[0] in '+-':
        t -= (1 if off[0] == '+' else -1) * (int(off[1:3]) * 3600 + int(off[3:5]) * 60)
    return t


def read(path, chans, progs):
    f = gzip.open(path) if path.endswith('.gz') else open(path, 'rb')
    n = 0
    for _, el in iterparse(f):
        if el.tag == 'channel':
            cid = el.get('id')
            names = [d.text.strip() for d in el.findall('display-name') if d.text and d.text.strip()]
            if cid:
                old = chans.get(cid)
                chans[cid] = list(dict.fromkeys((old or []) + names))
            el.clear()
        elif el.tag == 'programme':
            st, sp = when(el.get('start')), when(el.get('stop'))
            if sp > NOW and st < END and sp > st:
                t = el.find('title')
                title = (t.text or '').strip() if t is not None else ''
                if title:
                    d = el.find('desc')
                    desc = re.sub(r'\s+', ' ', (d.text or '')).strip() if d is not None else ''
                    if len(desc) > DESC:
                        desc = desc[:DESC].rsplit(' ', 1)[0] + '…'
                    progs.setdefault(el.get('channel'), {})[st] = [(max(st, BASE) - BASE) // 60, (sp - max(st, BASE)) // 60, title, desc]
                    n += 1
            el.clear()
    return n


def main():
    out, files = sys.argv[1], sys.argv[2:]
    chans, progs = {}, {}
    for f in files:
        try:
            n = read(f, chans, progs)
            print(f'{os.path.basename(f)}: {n} programmes', flush=True)
        except Exception as e:                       # one broken source never stops the guide
            print(f'{os.path.basename(f)}: skipped ({e})', flush=True)
    ids = sorted((c for c in chans if progs.get(c)), key=lambda c: (c.rsplit('.', 1)[-1].lower(), c.lower()))
    os.makedirs(os.path.join(out, 'p'), exist_ok=True)
    index = []
    for i in range(0, len(ids), PACK):
        pack = i // PACK
        body = {}
        for c in ids[i:i + PACK]:
            body[c] = [progs[c][k] for k in sorted(progs[c])]
            index.append([c, chans[c] or [c], pack])
        with open(os.path.join(out, 'p', f'{pack}.json'), 'w', encoding='utf-8') as fh:
            json.dump(body, fh, ensure_ascii=False, separators=(',', ':'))
    with open(os.path.join(out, 'index.json'), 'w', encoding='utf-8') as fh:
        json.dump({'v': NOW, 'base': BASE, 'ch': index}, fh, ensure_ascii=False, separators=(',', ':'))
    with open(os.path.join(out, 'meta.json'), 'w') as fh:
        json.dump({'v': NOW, 'base': BASE, 'channels': len(index), 'packs': (len(ids) + PACK - 1) // PACK}, fh)
    print(f'{len(index)} channels in {(len(ids) + PACK - 1) // PACK} packs', flush=True)


if __name__ == '__main__':
    main()
