import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import subtitle_hls as hls

SOURCE = ('WEBVTT\n\n'
          'before\n00:00.500 --> 00:01.000\nBefore\n\n'
          'cross\n00:03.500 --> 00:04.500 align:start position:20%\n中文跨段\n\n'
          'edge\n00:04.000 --> 00:08.000\nExact boundaries\n\n'
          'after\n00:08.500 --> 00:09.000\nAfter\n')


class SubtitleHLSTests(unittest.TestCase):
    def test_boundary_cues_keep_timestamps_settings_and_cjk(self):
        result = hls.package_webvtt(SOURCE, [4, 4, 4], 4, True)
        a, b, c = [result.segments[f'sub0-{i:06d}.vtt'].decode() for i in range(3)]
        cross = '00:03.500 --> 00:04.500 align:start position:20%\n中文跨段'
        self.assertIn('Before', a); self.assertNotIn('Before', b)
        self.assertIn(cross, a); self.assertIn(cross, b); self.assertNotIn(cross, c)
        self.assertNotIn('Exact boundaries', a); self.assertIn('Exact boundaries', b)
        self.assertNotIn('Exact boundaries', c)
        self.assertIn('After', c)
        self.assertIn('#EXT-X-ENDLIST', result.playlist.decode())

    def test_progressive_event_matches_video_snapshot_and_is_append_only(self):
        early = hls.package_webvtt(SOURCE, [4], 4, False)
        final = hls.package_webvtt(SOURCE, [4, 4, 1.125], 4, True)
        self.assertTrue(final.playlist.startswith(early.playlist))
        self.assertEqual(early.segments['sub0-000000.vtt'], final.segments['sub0-000000.vtt'])
        self.assertIn(b'#EXT-X-PLAYLIST-TYPE:EVENT\n', early.playlist)
        self.assertIn(b'#EXT-X-TARGETDURATION:4\n', final.playlist)
        self.assertIn(b'#EXTINF:1.125000,\nsub0-000002.vtt', final.playlist)
        self.assertNotIn(b'#EXT-X-ENDLIST', early.playlist)
        self.assertEqual(len(early.segments), 1)

    def test_empty_segments_and_empty_vtt_are_valid(self):
        result = hls.package_webvtt('WEBVTT\n\n', [2, 2], 2, False)
        for data in result.segments.values():
            self.assertTrue(data.startswith(b'WEBVTT\n'))
            self.assertNotIn(b'-->', data)
            _, cues = hls.parse_webvtt(data.decode())
            self.assertEqual(cues, [])
        gaps = hls.package_webvtt('WEBVTT\n\n00:04.000 --> 00:05.000\nLater\n', [2, 2, 2], 2, True)
        self.assertNotIn(b'Later', gaps.segments['sub0-000001.vtt'])
        self.assertIn(b'Later', gaps.segments['sub0-000002.vtt'])

    def test_header_style_region_and_crlf_are_preserved(self):
        text = '\ufeffWEBVTT\r\n\r\nSTYLE\r\n::cue { color: lime; }\r\n\r\nREGION\r\nid:bottom\r\n\r\n00:00.000 --> 00:01.000 region:bottom\r\n字幕\r\n'
        result = hls.package_webvtt(text, [1, 1], 1, True, prefix='sub12', playlist_type='VOD')
        self.assertEqual(result.playlist_name, 'sub12.m3u8')
        self.assertIn(b'#EXT-X-PLAYLIST-TYPE:VOD', result.playlist)
        for data in result.segments.values():
            self.assertIn(b'STYLE\n::cue { color: lime; }', data)
            self.assertIn(b'REGION\nid:bottom', data)

    def test_malformed_text_and_unsupported_epochs_reject(self):
        for text in ['not vtt', 'WEBVTT\n00:00.000 --> 00:01.000\nText',
                     'WEBVTT\n\n00:61.000 --> 00:62.000\nBad',
                     'WEBVTT\n\n00:02.000 --> 00:01.000\nBad',
                     'WEBVTT\n\nidentifier only', 'WEBVTT\n\n\x00',
                     'WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:90000\n\n',
                     'WEBVTT\n\n00:02.000 --> 00:03.000\nx\n\n00:01.000 --> 00:02.000\ny']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                hls.package_webvtt(text, [4], 4, True)

    def test_metadata_limits_and_path_injection_reject(self):
        for durations, target, complete in [([], 4, True), ([float('nan')], 4, True),
                                              ([-1], 4, True), ([5], 4, True), ([True], 4, True),
                                              ([4], True, True), ([4], 4, 1), ([10**1000], 4, True)]:
            with self.subTest(durations=durations), self.assertRaises(ValueError):
                hls.package_webvtt(SOURCE, durations, target, complete)
        with self.assertRaises(ValueError): hls.package_webvtt(SOURCE, [4], 4, True, prefix='../escape')
        with self.assertRaises(ValueError): hls.package_webvtt(SOURCE, [4], 4, False, playlist_type='VOD')
        with self.assertRaises(ValueError):
            hls.package_webvtt('WEBVTT\n\n' + '9' * 1000 + ':00:00.000 --> 00:01.000\nBad', [4], 4, True)
        with patch.object(hls, 'MAX_OUTPUT_BYTES', 10), self.assertRaises(ValueError):
            hls.package_webvtt(SOURCE, [4], 4, True)
        with patch.object(hls, 'MAX_REFERENCES', 1), self.assertRaises(ValueError):
            hls.package_webvtt(SOURCE, [4, 4], 4, True)


if __name__ == '__main__': unittest.main()
