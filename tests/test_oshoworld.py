import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('oshoworld', Path(__file__).resolve().parents[1] / 'scripts/oshoworld.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class SourceTests(unittest.TestCase):
    def test_url_encoding_once(self):
        raw = '/wp-content/Hindi Audio/a b.mp3'
        encoded = '/wp-content/Hindi%20Audio/a%20b.mp3'
        self.assertEqual(m.audio_url(raw), m.audio_url(encoded))
        self.assertNotIn('%2520', m.audio_url(encoded))
        with self.assertRaises(ValueError):
            m.audio_url('https://archive.org/a.mp3')

    def test_yoga_repaired_track_order(self):
        tracks = [
            {'slug': 'the-path-of-yoga-09', 'source_index': 8, 'url': 'https://oshoworld.com/wp-content/09.mp3'},
            {'slug': 'the-path-of-yoga-01', 'source_index': 1, 'url': 'https://oshoworld.com/wp-content/01.mp3'},
            {'slug': 'the-path-of-yoga-08', 'source_index': 8, 'url': 'https://oshoworld.com/uploads/008.mp3'},
        ]
        self.assertEqual([t['slug'] for t in m.track_order(tracks)], ['the-path-of-yoga-01', 'the-path-of-yoga-08', 'the-path-of-yoga-09'])

    def test_volume_order(self):
        tracks = [{'slug': f'talk-vol-{v}-{n}', 'source_index': n, 'url': 'unused'} for v,n in [(10,1),(2,2),(1,10),(2,1),(1,2)]]
        self.assertEqual([t['slug'] for t in m.track_order(tracks)], ['talk-vol-1-2','talk-vol-1-10','talk-vol-2-1','talk-vol-2-2','talk-vol-10-1'])

    def test_mp3_signature_rejects_html_and_accepts_mislabeled_audio(self):
        self.assertIsNone(m.mp3_signature(b'<html>Not Found</html>'))
        self.assertEqual(m.mp3_signature(b'ID3\x03\x00\x00\x00\x00\x00\x00'), 'ID3')
        self.assertEqual(m.mp3_signature(b'\xff\xfb\x90\x64'), 'MPEG-frame')

    def test_hindi_ascii_filename_xml_roundtrip(self):
        self.assertEqual(m.ascii_slug('Bhakti-Sutra (भक्ति-सूत्र) — 01-10'), 'bhakti-sutra-01-10')
        root = m.ET.Element('rss')
        m.element(root, 'title', 'भक्ति-सूत्र & Tao')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bhakti-sutra.xml'
            m.xml_write(path, root)
            self.assertEqual(m.ET.parse(path).findtext('title'), 'भक्ति-सूत्र & Tao')

    def test_validation_and_zip_of_generated_catalog(self):
        # Integration assertions cover every generated enclosure and OPML target.
        if not (m.ROOT / 'catalog/generated-files.json').exists():
            self.skipTest('Run discover, verify, build to create the integration fixture')
        m.validate()
        m.package()

    def test_tao_complete_and_sequential(self):
        path = m.ROOT / 'Hindi/Tao Upanishad.xml'
        if not path.exists():
            self.skipTest('Generated feed not present')
        items = m.ET.parse(path).findall('.//item')
        self.assertEqual(len(items), 127)
        for number, item in enumerate(items, 1):
            self.assertTrue(item.find('enclosure').get('url').endswith(f'OSHO-Tao_Upanishad_{number:03d}.mp3'))


if __name__ == '__main__':
    unittest.main()
