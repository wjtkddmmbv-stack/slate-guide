#!/usr/bin/env python3
"""Builds Slate's TV guide: merges free XMLTV files, keeps the next 36 hours, and splits the result into a small
channel index and packs of ~60 channels each, so a TV only downloads what it shows.

  python3 build.py <out_dir> <xmltv files (.xml or .xml.gz)...>

Output:
  meta.json        {"v": build time}                       (tiny; lets Slate check for a new guide)
  index.json       {"v", "base", "ch": [[id, [names], pack, source?], ...]}   source: the collector site (osn.com…), if any
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


def read(path, chans, progs, srcs):
    site = os.path.basename(path).replace('.xml.gz', '').replace('.xml', '')
    site = '' if site.startswith('epgshare') else site
    f = gzip.open(path) if path.endswith('.gz') else open(path, 'rb')
    n = 0
    for _, el in iterparse(f):
        if el.tag == 'channel':
            cid = el.get('id')
            names = [d.text.strip() for d in el.findall('display-name') if d.text and d.text.strip()]
            if cid:
                old = chans.get(cid)
                chans[cid] = list(dict.fromkeys((old or []) + names))
                if site: srcs.setdefault(cid, site)
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


NAMES = {}


def fix_bein(progs, srcs):
    """epgshare's beIN MENA listings (ids ending .bein) run late on some channels against beIN's own (bein.com):
    two hours on beIN SPORTS 1-9, none on XTRA (Nov 2026). The offset is measured each day, channel by channel, from
    programmes both list; a channel moves only when most of its matches agree on one offset."""
    def norm(t):
        return re.sub(r'\s+', ' ', re.split(r' - ', t.lower())[0].replace(' v ', ' vs ')).strip()

    def chan(c, names):
        """The same beIN channel in both: "beIN_SPORTS1_ENGLISH_Digital_Mono_AR.bein" and "beINSPORTS1EN" are en1."""
        s = (c + ' ' + ' '.join(names.get(c, []))).lower()
        m = re.search(r'bein_?sports?_?(max_?|xtra_?)?(\d+)_?(english|french|en\b|fr\b)?', s)
        if not m:
            return None
        return (m.group(1) or '').strip('_') + (m.group(3) or '')[:2] + m.group(2)
    own = {}
    for c, p in progs.items():
        if srcs.get(c) == 'bein.com':
            k = chan(c, NAMES)
            if k:
                for row in p.values():
                    own.setdefault((k, norm(row[2])), []).append(row[0])
    fixed = []
    for c, p in progs.items():
        if not c.endswith('.bein') or c in srcs:
            continue
        k, diffs = chan(c, NAMES), []
        for row in p.values():
            starts = own.get((k, norm(row[2])))
            if starts:
                d = min(starts, key=lambda x: abs(x - row[0]))
                if abs(row[0] - d) <= 360:
                    diffs.append(round((row[0] - d) / 15) * 15)
        if len(diffs) < 5:
            continue
        off = max(set(diffs), key=diffs.count)
        if off and diffs.count(off) >= 0.6 * len(diffs):
            for row in p.values():
                row[0] -= off
            fixed.append(f'{c} {off:+d}')
    print(f'beIN times corrected on {len(fixed)} channels: ' + ', '.join(fixed[:12]), flush=True)

def main():
    out, files = sys.argv[1], sys.argv[2:]
    chans, progs, srcs = {}, {}, {}
    for f in files:
        try:
            n = read(f, chans, progs, srcs)
            print(f'{os.path.basename(f)}: {n} programmes', flush=True)
        except Exception as e:                       # one broken source never stops the guide
            print(f'{os.path.basename(f)}: skipped ({e})', flush=True)
    NAMES.update(chans)
    fix_bein(progs, srcs)
    ids = sorted((c for c in chans if progs.get(c)), key=lambda c: (c.rsplit('.', 1)[-1].lower(), c.lower()))
    os.makedirs(os.path.join(out, 'p'), exist_ok=True)
    index = []
    for i in range(0, len(ids), PACK):
        pack = i // PACK
        body = {}
        for c in ids[i:i + PACK]:
            body[c] = [progs[c][k] for k in sorted(progs[c])]
            index.append([c, chans[c] or [c], pack] + ([srcs[c]] if c in srcs else []))
        with open(os.path.join(out, 'p', f'{pack}.json'), 'w', encoding='utf-8') as fh:
            json.dump(body, fh, ensure_ascii=False, separators=(',', ':'))
    with open(os.path.join(out, 'index.json'), 'w', encoding='utf-8') as fh:
        json.dump({'v': NOW, 'base': BASE, 'ch': index}, fh, ensure_ascii=False, separators=(',', ':'))
    with open(os.path.join(out, 'meta.json'), 'w') as fh:
        json.dump({'v': NOW, 'base': BASE, 'channels': len(index), 'packs': (len(ids) + PACK - 1) // PACK}, fh)
    print(f'{len(index)} channels in {(len(ids) + PACK - 1) // PACK} packs', flush=True)


if __name__ == '__main__':
    main()
