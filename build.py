#!/usr/bin/env python3
"""Builds Slate's TV guide: merges free XMLTV files, keeps the next 36 hours, and splits the result into a small
channel index and packs of ~60 channels each, so a TV only downloads what it shows.

  python3 build.py <out_dir> <xmltv files (.xml or .xml.gz)...>

Output:
  meta.json        {"v": build time}                       (tiny; lets Slate check for a new guide)
  index.json       {"v", "base", "ch": [[id, [names], pack, source?], ...]}   source: the collector site (osn.com…), if any
  p/<n>.json       {"_v": build, "<id>": [[start, minutes, title, description], ...]}   start = minutes after "base"
  m.json           {"v", "base", "p": {"<id>": [[start, minutes, title], ...]}}   games and live events only (match pages)
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
CHECKED = set()                                  # epgshare beIN channels whose times were checked against beIN's own


def norm(t):
    return re.sub(r'\s+', ' ', re.split(r' - ', t.lower())[0].replace(' v ', ' vs ')).strip()


def chan(c, names):
    """The same beIN channel in both: "beIN_SPORTS1_ENGLISH_Digital_Mono_AR.bein" and "beINSPORTS1EN" are en1."""
    s = (c + ' ' + ' '.join(names.get(c, []))).lower()
    m = re.search(r'bein_?sports?_?(max_?|xtra_?)?(\d+)_?(english|french|en\b|fr\b)?', s)
    if not m:
        return None
    return (m.group(1) or '').strip('_') + (m.group(3) or '')[:2] + m.group(2)


def tidy(p):
    """Drops programmes that start while the one before is still on (beIN's own feed sometimes mixes two lists)."""
    end = None
    for k in sorted(p, key=lambda k: p[k][0]):
        row = p[k]
        if end is not None and row[0] < end - 5:
            del p[k]
            continue
        end = row[0] + row[1]


def fill_bein(progs, srcs):
    """Gaps in beIN's own schedule (bein.com) filled from epgshare's copy of the same channel, once its times are
    corrected: beIN SPORTS 6 had nothing in beIN's own feed until late evening on 22 Nov 2026."""
    other = {}
    for c in progs:
        if c in CHECKED and '_AR' in c:
            other.setdefault(chan(c, NAMES), c)
    n = 0
    for c, p in progs.items():
        if srcs.get(c) != 'bein.com' or not p:
            continue
        o = other.get(chan(c, NAMES))
        if not o:
            continue
        spans = sorted((r[0], r[0] + r[1]) for r in p.values())
        for row in progs[o].values():
            a, b = row[0], row[0] + row[1]
            if all(b <= x + 5 or a >= y - 5 for x, y in spans):
                p[BASE + row[0] * 60 + 0.5] = list(row)
                n += 1
    print(f'beIN gaps filled with {n} programmes', flush=True)


def fix_bein(progs, srcs):
    """epgshare's beIN MENA listings (ids ending .bein) run late on some channels against beIN's own (bein.com):
    two hours on beIN SPORTS 1-9, none on XTRA (Nov 2026). The offset is measured each day, channel by channel, from
    programmes both list; a channel moves only when most of its matches agree on one offset."""
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
        if diffs.count(off) < 0.6 * len(diffs):
            continue
        CHECKED.add(c)
        if off:
            for row in p.values():
                row[0] -= off
            fixed.append(f'{c} {off:+d}')
    print(f'beIN times checked on {len(CHECKED)} channels, corrected on {len(fixed)}: ' + ', '.join(fixed[:12]), flush=True)


ARAB_FEEDS = {'ae', 'sa', 'eg', 'qa', 'kw', 'bh', 'om', 'jo', 'lb', 'iq', 'ma', 'dz', 'tn', 'ly', 'sy', 'ps', 'ye', 'sd'}


def name_key(c, names):
    n = (names.get(c) or [c])[0].lower()
    n = re.sub(r'^\s*[a-z]{2,3}\s*:\s*', '', n)
    n = re.sub(r'\b(uhd|fhd|hd|sd|4k|tv|channel)\b', ' ', n)
    n = n.replace('sports', 'sport').replace('abu dhabi', 'ad')
    return re.sub(r'[^a-z0-9\u0600-\u06ff]', '', n)


GENERIC = {'sport', 'sports', 'news', 'channel', 'drama', 'cinema', 'aflam', 'music', 'live', 'kids', 'family', 'arabic', 'arabia', 'mubasher', 'plus', 'extra'}


def owns(c, names, titles):
    """The schedule names this channel ("On Time Stadium" on ON Time Sports): it's this channel's own."""
    words = [w for w in re.findall(r'[a-z]{4,}', (names.get(c) or [c])[0].lower()) if w not in GENERIC]
    return any(w in t for w in words for t in titles)


def drop_copies(progs, names, srcs):
    """Channels in epgshare's Arab guides (ids ending .ae, .sa…) that carry another channel's schedule word for word:
    "KSA sports 1" had a cartoon channel's, "KSA Sports 3" ON Time Sports', Zamalek TV Alkass One's (Nov 2026).
    A channel the schedule names ("On Time Stadium" on ON Time Sports), or the one sports channel among them when it
    lists games, keeps it; otherwise there's no telling which
    is right, so all of them are left out (Slate then shows no guide for them rather than a wrong one). Copies of one
    channel (HD and SD, "AD Sports 1" and "Abu Dhabi Sports 1") are kept."""
    feeds = {}
    for c, p in progs.items():
        feed = c.rsplit('.', 1)[-1].split('@')[0].lower()
        if feed in ARAB_FEEDS and c not in srcs and len(p) >= 8:
            feeds.setdefault(feed, []).append(c)
    drop, sig_all = {}, {}
    for feed, ids in feeds.items():
        sig = {c: {(r[0], r[2].strip().lower()) for r in progs[c].values()} for c in ids}
        sig_all.update(sig)
        post = {}
        for c in ids:
            for x in sig[c]:
                post.setdefault(x, []).append(c)
        pairs = {}
        for cs in post.values():
            if 1 < len(cs) < 1000:
                for i in range(len(cs)):
                    for j in range(i + 1, len(cs)):
                        k = (cs[i], cs[j]) if cs[i] < cs[j] else (cs[j], cs[i])
                        pairs[k] = pairs.get(k, 0) + 1
        for (a, b), n in pairs.items():
            if n >= 0.9 * min(len(sig[a]), len(sig[b])) and name_key(a, names) != name_key(b, names):
                drop.setdefault(a, set()).add(b)
                drop.setdefault(b, set()).add(a)
    def sporty(c):
        return bool(re.search(r'sport|bein|alkass|ssc', (names.get(c) or [c])[0].lower()))
    kept = []
    for c in list(drop):
        titles = {t for _, t in sig_all[c]}
        if owns(c, names, titles) and not any(owns(o, names, titles) for o in drop[c]):
            kept.append(c)
        # Games ("Iran Vs Italy") on the one sports channel of the group: the sports channel's own (beIN SPORTS).
        elif sporty(c) and not any(sporty(o) for o in drop[c]) and sum(1 for t in titles if FIXTURE.search(t)) >= 3:
            kept.append(c)
    for c in kept:
        del drop[c]
    for c in drop:
        progs[c] = {}
    print(f'kept as the owner of a copied schedule: ' + ', '.join(sorted(kept)), flush=True)
    print(f'copied schedules left out on {len(drop)} channels: ' + ', '.join(sorted(drop)[:40]), flush=True)
    return drop


FIXTURE = re.compile(r"(?i)\b[\w'.]+\s+(?:vs?\.?|@)\s+[\w']")
SPORTY = re.compile(r"(?i)\b(darts|snooker|golf|tennis|nfl|nba|nhl|mlb|ufc|boxing|cricket|formula 1|grand prix|motogp|cyclocross|redzone|red zone|wwe|rugby)\b")


def matches(out, ids, progs):
    """m.json: only the programmes that are a game or another live event ("Liverpool vs Manchester United", "Live
    Grand Slam Of Darts"), so a match page can find which channels show a game without loading every pack.
    US local stations (they only say "NFL Football") are left out."""
    p = {}
    for c in ids:
        if '.us_locals' in c:
            continue
        keep = []
        for row in sorted(progs[c].values(), key=lambda r: r[0]):
            if FIXTURE.search(row[2]):
                keep.append([row[0], row[1], row[2][:60]])
            elif SPORTY.search(row[2]):
                keep.append([row[0], row[1], row[2][:40]])
        if keep:
            p[c] = keep
    with open(os.path.join(out, 'm.json'), 'w', encoding='utf-8') as fh:
        json.dump({'v': NOW, 'base': BASE, 'p': p}, fh, ensure_ascii=False, separators=(',', ':'))
    print(f'm.json: {sum(len(v) for v in p.values())} programmes on {len(p)} channels', flush=True)


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
    for c in progs:
        if srcs.get(c) == 'bein.com':
            tidy(progs[c])
    fix_bein(progs, srcs)
    for c in progs:
        if c.endswith('.bein'):
            tidy(progs[c])
    fill_bein(progs, srcs)
    drop_copies(progs, NAMES, srcs)
    ids = sorted((c for c in chans if progs.get(c)), key=lambda c: (c.rsplit('.', 1)[-1].lower(), c.lower()))
    os.makedirs(os.path.join(out, 'p'), exist_ok=True)
    index = []
    for i in range(0, len(ids), PACK):
        pack = i // PACK
        body = {'_v': NOW}                          # the build, so Slate can tell a pack from a newer build
        for c in ids[i:i + PACK]:
            body[c] = sorted(progs[c].values(), key=lambda r: r[0])
            index.append([c, chans[c] or [c], pack] + ([srcs[c]] if c in srcs else []))
        with open(os.path.join(out, 'p', f'{pack}.json'), 'w', encoding='utf-8') as fh:
            json.dump(body, fh, ensure_ascii=False, separators=(',', ':'))
    matches(out, ids, progs)
    with open(os.path.join(out, 'index.json'), 'w', encoding='utf-8') as fh:
        json.dump({'v': NOW, 'base': BASE, 'ch': index}, fh, ensure_ascii=False, separators=(',', ':'))
    with open(os.path.join(out, 'meta.json'), 'w') as fh:
        json.dump({'v': NOW, 'base': BASE, 'channels': len(index), 'packs': (len(ids) + PACK - 1) // PACK}, fh)
    print(f'{len(index)} channels in {(len(ids) + PACK - 1) // PACK} packs', flush=True)


if __name__ == '__main__':
    main()
