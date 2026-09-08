#!/usr/bin/env python3
"""Discover OshoWorld metadata, verify remote MP3s, and build podcast feeds.

No audio is saved. Verification reads at most 4096 bytes per request. Metadata
and verification evidence are persisted so generation is offline/reproducible.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
import json
from pathlib import Path
import re
import sys
import threading
import time
import unicodedata
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
import uuid
import xml.etree.ElementTree as ET
import zipfile

import requests

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'https://oshoworld.com'
PAGES = 'https://nirmalveer.github.io/osho/'
ATOM = 'http://www.w3.org/2005/Atom'
ITUNES = 'http://www.itunes.com/dtds/podcast-1.0.dtd'
ET.register_namespace('atom', ATOM)
ET.register_namespace('itunes', ITUNES)
LOCAL = threading.local()


def now():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def session():
    if not hasattr(LOCAL, 'session'):
        LOCAL.session = requests.Session()
        LOCAL.session.headers['User-Agent'] = 'OshoWorldPodcastCatalog/1.0 (+https://github.com/nirmalveer/osho)'
    return LOCAL.session


def request(url, **kwargs):
    for attempt in range(4):
        try:
            response = session().get(url, timeout=(15, 40), **kwargs)
            if response.status_code in (429, 500, 502, 503, 504):
                response.close()
                raise requests.RequestException(f'transient HTTP {response.status_code}')
            response.raise_for_status()
            return response
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(1 + attempt * 2)


def get_json(url):
    with request(url) as response:
        return response.json()


def audio_url(value):
    parts = urlsplit(urljoin(SOURCE + '/', value))
    if parts.hostname not in ('oshoworld.com', 'www.oshoworld.com'):
        raise ValueError(f'Unexpected audio host: {parts.hostname}')
    path = quote(unquote(parts.path), safe='/')
    if not path.lower().endswith('.mp3'):
        raise ValueError(f'Not an MP3 path: {path}')
    return urlunsplit(('https', parts.netloc, path, parts.query, ''))


def ascii_slug(value):
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-') or 'series'


def natural_key(value):
    return tuple((1, int(p)) if p.isdigit() else (0, p.lower()) for p in re.split(r'(\d+)', unquote(value)))


def track_order(tracks):
    # Source indexes restart within some volumes. A unique index is authoritative;
    # otherwise naturally sort published slugs, which include volume/track numbers.
    # Sorting full URLs would put a repaired track in /uploads/ before track 1
    # in /wp-content/ (the live Yoga volume 1 catalog has exactly this case).
    indexes = [t.get('source_index') for t in tracks]
    if all(isinstance(i, (int, float)) for i in indexes) and len(set(indexes)) == len(indexes):
        return sorted(tracks, key=lambda t: t['source_index'])
    return sorted(tracks, key=lambda t: natural_key(t.get('slug') or unquote(urlsplit(t['url']).path).rsplit('/', 1)[-1]))


def discover(workers):
    with request(SOURCE + '/sitemap.xml') as response:
        sitemap = ET.fromstring(response.content)
    locations = [e.text for e in sitemap.iter() if e.tag.endswith('}loc')]
    languages = {'english', 'hindi'}
    for location in locations:
        match = re.search(r'/audio-(?:series-home-([a-z]+)|([a-z]+)-home)/?$', location)
        if match:
            languages.add(match[1] or match[2])
    seeds = []
    for language in sorted(languages):
        data = get_json(f'{SOURCE}/api/audio/catalog/{language}')
        if not isinstance(data.get('series'), list):
            raise ValueError(f'Catalog schema changed: {language}')
        for row in data['series']:
            seeds.append({**row, 'language': language})
    if len({s['_id'] for s in seeds}) != len(seeds):
        raise ValueError('Duplicate series IDs across catalogs')

    def fetch_series(seed):
        endpoint = f"{SOURCE}/api/audio/get-all-audios-list/{seed['_id']}"
        rows = get_json(endpoint)
        if not isinstance(rows, list):
            raise ValueError(f'Unexpected track response: {endpoint}')
        tracks, seen, duplicates, no_file = [], set(), [], []
        for row in rows:
            if row.get('series_id') != seed['_id']:
                raise ValueError(f'Cross-series track in {endpoint}: {row.get("_id")}')
            if not row.get('file'):
                no_file.append(row.get('_id'))
                continue
            url = audio_url(row['file'])
            if url in seen:
                duplicates.append(row.get('_id'))
                continue
            seen.add(url)
            tracks.append({'id': row['_id'], 'slug': row.get('slug'),
                           'title': row.get('title') or seed['title'], 'url': url,
                           'source_index': row.get('index', row.get('audio_index')),
                           'duration': row.get('duration', '')})
        return {'id': seed['_id'], 'slug': seed['slug'], 'title': seed['title'],
                'language': seed['language'], 'page_url': SOURCE + '/' + seed['slug'],
                'catalog_reported_count': seed.get('count'), 'listed_record_count': len(rows),
                'duplicate_record_ids': duplicates, 'records_without_audio': no_file,
                'tracks': track_order(tracks)}

    series = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_series, seed) for seed in seeds]
        for future in as_completed(futures):
            series.append(future.result())
            if len(series) % 50 == 0:
                print(f'Discovered {len(series)}/{len(seeds)} series', flush=True)
    catalog = {'source': SOURCE, 'discovered_at': now(),
               'discovery': {'sitemap': SOURCE + '/sitemap.xml',
                             'languages': sorted(languages), 'sitemap_url_count': len(locations),
                             'catalog_endpoint': '/api/audio/catalog/{language}',
                             'track_endpoint': '/api/audio/get-all-audios-list/{series_id}'},
               'series': sorted(series, key=lambda s: (s['language'], s['slug']))}
    save_json(ROOT / 'catalog/oshoworld.json', catalog)
    print(f"Discovered {len(series)} series / {sum(len(s['tracks']) for s in series)} listed unique tracks")


def mp3_signature(data):
    if data.startswith(b'ID3') and len(data) >= 10 and data[3] in (2, 3, 4):
        return 'ID3'
    for i in range(min(len(data) - 3, 1024)):
        a, b, c = data[i:i+3]
        if a == 255 and b & 224 == 224 and b & 24 != 8 and b & 6 != 0 and c & 240 not in (0, 240) and c & 12 != 12:
            return 'MPEG-frame'
    return None


def verify_url(url):
    record = {'url': url, 'checked_at': now(), 'verified': False}
    try:
        with request(url, headers={'Range': 'bytes=0-4095', 'Accept-Encoding': 'identity'}, stream=True) as r:
            record.update(status=r.status_code, resolved_url=r.url,
                          content_type=r.headers.get('Content-Type', ''),
                          content_range=r.headers.get('Content-Range', ''),
                          etag=r.headers.get('ETag', ''),
                          last_modified=r.headers.get('Last-Modified', ''))
            if urlsplit(r.url).hostname not in ('oshoworld.com', 'www.oshoworld.com'):
                raise ValueError('Audio redirected outside OshoWorld')
            sample = r.raw.read(4096)
            signature = mp3_signature(sample)
            match = re.fullmatch(r'bytes 0-(\d+)/(\d+)', record['content_range'])
            length = int(match[2]) if r.status_code == 206 and match else int(r.headers.get('Content-Length', '0')) if r.status_code == 200 else 0
            if r.status_code not in (200, 206) or not signature or length <= 4096:
                raise ValueError(f'Invalid audio response: status={r.status_code}, signature={signature}, length={length}')
            record.update(verified=True, length=length, signature=signature,
                          range_supported=r.status_code == 206 and match is not None)
    except (requests.RequestException, ValueError, OSError) as exc:
        record['error'] = str(exc)
    return record


def verify(workers, retry_failed=False):
    catalog = json.loads((ROOT / 'catalog/oshoworld.json').read_text(encoding='utf-8'))
    urls = sorted({t['url'] for s in catalog['series'] for t in s['tracks']})
    path = ROOT / 'catalog/verification.json'
    previous = json.loads(path.read_text(encoding='utf-8'))['files'] if retry_failed and path.exists() else {}
    files = {url: previous[url] for url in urls if previous.get(url, {}).get('verified')}
    pending = [url for url in urls if url not in files]
    print(f'Verifying {len(pending)} MP3 URLs; {len(files)} previous successes retained', flush=True)
    def checkpoint():
        save_json(path, {'updated_at': now(), 'method': 'GET Range bytes=0-4095; MP3 signature and total byte length',
                         'files': dict(sorted(files.items()))})
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(verify_url, url) for url in pending]
        for future in as_completed(futures):
            result = future.result()
            files[result['url']] = result
            if len(files) % 100 == 0:
                checkpoint()
                print(f"Checked {len(files)}/{len(urls)}; failed {sum(not f['verified'] for f in files.values())}", flush=True)
    checkpoint()
    print(f"Verified {sum(f['verified'] for f in files.values())}/{len(urls)} unique MP3 files", flush=True)


def xml_write(path, root):
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space='  ')
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)


def element(parent, name, text):
    child = ET.SubElement(parent, name)
    child.text = str(text)
    return child


def clean_title(title):
    return re.sub(r'\s*#?\s*\d+\s*-\s*\d+\s*$', '', title).strip()


def legacy_feeds():
    # Reuse old ASCII feed locations and GUIDs where filenames match. This keeps
    # existing subscriptions and episode identities stable through the migration.
    entries = []
    for language in ('English', 'Hindi'):
        for path in sorted((ROOT / language).glob('*.xml')):
            channel = ET.parse(path).getroot().find('channel')
            episodes = {}
            for item in channel.findall('item'):
                enclosure = item.find('enclosure')
                if enclosure is not None:
                    name = unquote(urlsplit(enclosure.get('url')).path).rsplit('/', 1)[-1].casefold()
                    episodes[name] = {'guid': item.findtext('guid'), 'isPermaLink': item.find('guid').get('isPermaLink', 'true')}
            entries.append({'path': path.relative_to(ROOT).as_posix(),
                            'language': language.lower(), 'title': channel.findtext('title'), 'episodes': episodes})
    return entries


def migrate_map(catalog):
    path = ROOT / 'catalog/legacy-map.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    old = legacy_feeds()
    mappings, claimed = {}, set()
    for series in catalog['series']:
        names = {unquote(urlsplit(t['url']).path).rsplit('/', 1)[-1].casefold() for t in series['tracks']}
        scores = sorted(((len(names & set(entry['episodes'])), entry['path'], entry)
                         for entry in old if entry['language'] == series['language']), reverse=True, key=lambda x: (x[0], x[1]))
        if scores and scores[0][0] > 0 and (len(scores) == 1 or scores[0][0] > scores[1][0]) and scores[0][1] not in claimed:
            entry = scores[0][2]
            if entry['path'].isascii():
                mappings[series['id']] = entry
                claimed.add(entry['path'])
    result = {'series': mappings, 'unmatched_legacy_paths': [e['path'] for e in old if e['path'] not in claimed]}
    save_json(path, result)
    return result


def build():
    catalog = json.loads((ROOT / 'catalog/oshoworld.json').read_text(encoding='utf-8'))
    files = json.loads((ROOT / 'catalog/verification.json').read_text(encoding='utf-8'))['files']
    untested = [t['url'] for s in catalog['series'] for t in s['tracks'] if t['url'] not in files]
    if untested:
        raise ValueError(f'{len(untested)} tracks have no verification record; run verify first')
    mapping = migrate_map(catalog)
    outputs, summaries, used_paths = [], [], set()
    feeds = []
    for series in catalog['series']:
        series['tracks'] = track_order(series['tracks'])
        tracks = [t for t in series['tracks'] if files[t['url']]['verified']]
        excluded = [{'title': t['title'], 'url': t['url'], 'error': files[t['url']].get('error')} for t in series['tracks'] if not files[t['url']]['verified']]
        old = mapping['series'].get(series['id'], {})
        folder = ascii_slug(series['language']).capitalize()
        relative = old.get('path', f"{folder}/{ascii_slug(series['slug'])}.xml")
        if relative.casefold() in used_paths or not relative.isascii():
            raise ValueError(f'Output path collision or non-ASCII path: {relative}')
        used_paths.add(relative.casefold())
        title = clean_title(series['title'])
        summary = {'title': title, 'language': series['language'], 'slug': series['slug'],
                   'catalog_reported_count': series['catalog_reported_count'],
                   'listed_records': series['listed_record_count'], 'unique_listed_mp3s': len(series['tracks']),
                   'verified_episodes': len(tracks), 'excluded': excluded,
                   'duplicate_record_ids': series['duplicate_record_ids'], 'feed': relative if tracks else None}
        summaries.append(summary)
        if not tracks:
            continue
        rss = ET.Element('rss', version='2.0')
        channel = ET.SubElement(rss, 'channel')
        element(channel, 'title', title)
        element(channel, 'link', series['page_url'])
        element(channel, 'description', f"Osho discourse series: {title}. Audio streamed directly from OshoWorld. Dates are synthetic ordering dates, not recording dates.")
        element(channel, 'language', {'english': 'en', 'hindi': 'hi'}.get(series['language'], 'und'))
        element(channel, f'{{{ITUNES}}}author', 'Osho')
        element(channel, f'{{{ITUNES}}}explicit', 'false')
        ET.SubElement(channel, f'{{{ATOM}}}link', href=PAGES + quote(relative, safe='/'), rel='self', type='application/rss+xml')
        playlist = ['#EXTM3U']
        # Dates follow positions in the complete listing, so excluding a broken
        # URL and later restoring it never changes dates of existing episodes.
        positions = {t['id']: i for i, t in enumerate(series['tracks'], 1)}
        for track in tracks:
            index = positions[track['id']]
            item = ET.SubElement(channel, 'item')
            element(item, 'title', track['title'])
            element(item, 'link', SOURCE + '/' + track['slug'] if track['slug'] else series['page_url'])
            ET.SubElement(item, 'enclosure', url=track['url'], type='audio/mpeg', length=str(files[track['url']]['length']))
            basename = unquote(urlsplit(track['url']).path).rsplit('/', 1)[-1].casefold()
            identity = old.get('episodes', {}).get(basename)
            guid = element(item, 'guid', identity['guid'] if identity else 'urn:uuid:' + str(uuid.uuid5(uuid.NAMESPACE_URL, SOURCE + '/audio/' + series['id'] + '/' + track['id'])))
            guid.set('isPermaLink', identity.get('isPermaLink', 'true') if identity else 'false')
            date = datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(days=index)
            element(item, 'pubDate', format_datetime(date))
            if track['duration']:
                element(item, f'{{{ITUNES}}}duration', track['duration'])
            playlist.extend([f"#EXTINF:-1,{track['title'].replace(chr(10), ' ').replace(chr(13), ' ')}", track['url']])
        xml_write(ROOT / relative, rss)
        playlist_path = f'playlists/{folder}/{Path(relative).stem}.m3u'
        (ROOT / playlist_path).parent.mkdir(parents=True, exist_ok=True)
        (ROOT / playlist_path).write_text('\n'.join(playlist) + '\n', encoding='utf-8')
        outputs.extend([relative, playlist_path])
        feeds.append({'path': relative, 'language': series['language'], 'title': title, 'page_url': series['page_url']})
    for language in ['all'] + catalog['discovery']['languages']:
        opml = ET.Element('opml', version='2.0')
        element(ET.SubElement(opml, 'head'), 'title', f'Osho {language.capitalize()} Discourses')
        body = ET.SubElement(opml, 'body')
        for feed in sorted(feeds, key=lambda f: (f['language'], f['title'].casefold())):
            if language != 'all' and feed['language'] != language:
                continue
            # Devanagari is retained in RSS titles; OPML display labels and all
            # physical paths use the existing ASCII name or source romanization.
            label = f"[{feed['language'].capitalize()}] {Path(feed['path']).stem}"
            ET.SubElement(body, 'outline', text=label, title=label, type='rss',
                          xmlUrl=PAGES + quote(feed['path'], safe='/'), htmlUrl=feed['page_url'])
        opml_path = f'Osho_{language.capitalize()}_Podcasts.opml'
        xml_write(ROOT / opml_path, opml)
        outputs.append(opml_path)
    report = {'generated_at': now(), 'discovered_at': catalog['discovered_at'],
              'series_listed': len(summaries), 'feeds_generated': len(feeds),
              'verified_episodes': sum(s['verified_episodes'] for s in summaries),
              'unique_verified_mp3s': len({t['url'] for s in catalog['series'] for t in s['tracks'] if files[t['url']]['verified']}),
              'preserved_legacy_feed_paths': len(mapping['series']),
              'unmatched_legacy_paths': mapping['unmatched_legacy_paths'], 'series': summaries}
    save_json(ROOT / 'catalog/build-report.json', report)
    previous_manifest = ROOT / 'catalog/generated-files.json'
    previous_outputs = json.loads(previous_manifest.read_text(encoding='utf-8')) if previous_manifest.exists() else []
    stale = set(previous_outputs + mapping['unmatched_legacy_paths']) - set(outputs)
    for relative in stale:
        target = (ROOT / relative).resolve()
        if not target.is_relative_to(ROOT.resolve()) or target.suffix not in ('.xml', '.opml', '.m3u'):
            raise ValueError(f'Unsafe stale generated path: {relative}')
        target.unlink(missing_ok=True)
    save_json(ROOT / 'catalog/generated-files.json', sorted(outputs))
    print(f"Built {len(feeds)} feeds / {report['verified_episodes']} episodes; excluded {sum(len(s['excluded']) for s in summaries)} failed URLs")


def validate():
    outputs = json.loads((ROOT / 'catalog/generated-files.json').read_text(encoding='utf-8'))
    files = json.loads((ROOT / 'catalog/verification.json').read_text(encoding='utf-8'))['files']
    feed_paths, guids, count = set(), set(), 0
    for relative in outputs:
        assert relative.isascii(), relative
        path = ROOT / relative
        assert path.is_file(), relative
        if path.suffix != '.xml':
            continue
        feed_paths.add(relative)
        channel = ET.parse(path).getroot().find('channel')
        assert channel.find(f'{{{ATOM}}}link').get('href') == PAGES + quote(relative, safe='/')
        dates, urls = [], set()
        for item in channel.findall('item'):
            enclosure = item.find('enclosure')
            url = enclosure.get('url')
            assert url not in urls, (relative, url)
            urls.add(url)
            assert files[url]['verified'] and enclosure.get('type') == 'audio/mpeg'
            assert int(enclosure.get('length')) == files[url]['length'] > 0
            guid = item.findtext('guid')
            assert guid and guid not in guids, (relative, guid)
            guids.add(guid)
            dates.append(parsedate_to_datetime(item.findtext('pubDate')))
            count += 1
        assert dates == sorted(set(dates)) and dates, relative
        playlist = ROOT / 'playlists' / Path(relative).with_suffix('.m3u')
        playlist_urls = [line for line in playlist.read_text(encoding='utf-8').splitlines() if line and not line.startswith('#')]
        assert playlist_urls == [e.get('url') for e in channel.findall('item/enclosure')], relative
    actual_feeds = {p.relative_to(ROOT).as_posix() for folder in ('English', 'Hindi') for p in (ROOT / folder).glob('*.xml')}
    expected_feeds = {p for p in feed_paths if p.split('/')[0] in ('English', 'Hindi')}
    assert actual_feeds == expected_feeds, 'Stale or missing feed files'
    all_urls = []
    for relative in outputs:
        if not relative.endswith('.opml'):
            continue
        urls = [o.get('xmlUrl') for o in ET.parse(ROOT / relative).findall('.//outline')]
        assert len(urls) == len(set(urls))
        for url in urls:
            assert url.startswith(PAGES)
            assert unquote(url[len(PAGES):]) in feed_paths, url
        if relative == 'Osho_All_Podcasts.opml':
            all_urls = urls
    assert len(all_urls) == len(feed_paths)
    print(f'Validation passed: {len(feed_paths)} feeds / {count} episodes; verified enclosures, GUIDs, ordering, ASCII paths, OPML links')


def package():
    validate()
    paths = json.loads((ROOT / 'catalog/generated-files.json').read_text(encoding='utf-8'))
    target = ROOT / 'dist/OshoWorld-Podcasts.zip'
    target.parent.mkdir(exist_ok=True)
    # Python sets the ZIP UTF-8 filename flag for every non-ASCII name. Generated
    # paths are stricter: all ASCII, so legacy extractors cannot create mojibake.
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in paths:
            assert relative.isascii(), relative
            archive.write(ROOT / relative, relative)
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert all(i.filename.isascii() or i.flag_bits & 0x800 for i in archive.infolist())
    print(f'Packaged {len(paths)} files: {target}')


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['discover', 'verify', 'build', 'validate', 'package'])
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--retry-failed', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.workers <= 12:
        parser.error('--workers must be between 1 and 12')
    if args.command == 'discover':
        discover(args.workers)
    elif args.command == 'verify':
        verify(args.workers, args.retry_failed)
    else:
        globals()[args.command]()


if __name__ == '__main__':
    main()
