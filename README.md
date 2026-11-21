# slate-guide

Daily TV guide data for Slate, prepared from free XMLTV sources so that TVs only download small pieces.

- Sources: [epgshare01](https://epgshare01.online/) (free EPG, for legal use only) and guides collected with
  [iptv-org/epg](https://github.com/iptv-org/epg) from the sites in `sites.txt`.
- `build.py` keeps the next 36 hours and writes `index.json` (channel names) and `p/<n>.json` (programmes, ~60 channels each).
- Rebuilt daily by `.github/workflows/daily.yml` and published with GitHub Pages.

No personal data, channel lists or keys are kept here.
