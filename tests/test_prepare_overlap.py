"""Deterministic barriers exercise production prepare(), without timing guesses."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
import relay


class FakeVideo:
    code = None
    def poll(self): return self.code


class OverlapTests(unittest.TestCase):
    def setup_case(self, folder):
        root = Path(folder); source = root/'video.mp4'; subtitle = root/'captions.srt'
        source.write_bytes(b'fixture'); subtitle.write_text('fixture')
        metadata = {'format': {'duration':'8', 'start_time':'0', 'bit_rate':'1000'},
                    'streams':[{'codec_type':'video','codec_name':'h264','pix_fmt':'yuv420p',
                                'index':0,'start_pts':0,'time_base':'1/90000'}]}
        request = {'source':str(source),'subtitles':[{'url':str(subtitle),'label':'English'}]}
        return root, metadata, request

    def test_video_progresses_while_subtitle_job_is_in_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            root, metadata, request = self.setup_case(folder)
            video = FakeVideo(); launched = threading.Event(); subtitles_started = threading.Event(); video_finished = threading.Event()
            events = []; worker = None
            def launch(*args, **kwargs):
                nonlocal worker
                self.assertTrue(kwargs['start_new_session'])
                launched.set()
                def video_work():
                    if subtitles_started.wait(2): video.code=0; video_finished.set()
                worker=threading.Thread(target=video_work);worker.start()
                return video
            def subtitle_job(*args, **kwargs):
                self.assertTrue(launched.is_set(), 'Video must launch before subtitle conversion')
                self.assertFalse((root/'master.m3u8').exists(), 'No public master before subtitle success')
                subtitles_started.set()
                self.assertTrue(video_finished.wait(2), 'Video must progress during subtitle work')
                return subprocess.CompletedProcess([],0)
            with patch.object(relay,'probe',return_value=metadata), patch.object(relay.subprocess,'Popen',side_effect=launch), \
                 patch.object(relay,'run_cancellable',side_effect=subtitle_job), patch.object(relay,'emit',side_effect=lambda kind,**fields:events.append((kind,fields))):
                result=relay.prepare(request,root,threading.Event(),os.getppid())
            worker.join()
            self.assertIs(result[0],video);self.assertTrue((root/'master.m3u8').exists())
            self.assertFalse(any(kind=='ready' for kind,_ in events))
            self.assertEqual({e['phase'] for _,e in events if 'phase' in e},{'inspection','subtitles','video'})
            self.assertNotIn(folder,str(events))

    def test_subtitle_failure_reaps_an_actual_owned_video_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root, metadata, request = self.setup_case(folder)
            real_popen = subprocess.Popen; processes = []
            def launch(*args, **kwargs):
                process = real_popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                processes.append(process); return process
            try:
                with patch.object(relay, 'probe', return_value=metadata), patch.object(relay.subprocess, 'Popen', side_effect=launch), \
                     patch.object(relay, 'run_cancellable', return_value=subprocess.CompletedProcess([], 1)), patch.object(relay, 'emit'):
                    with self.assertRaisesRegex(ValueError, 'subtitle track could not be prepared'):
                        relay.prepare(request, root, threading.Event(), os.getppid())
                self.assertEqual(len(processes), 1)
                self.assertIsNotNone(processes[0].poll(), 'Failed prepare retained its video process')
            finally:
                for process in processes:
                    if process.poll() is None: process.kill(); process.wait()

    def test_subtitle_failure_timeout_and_cancel_reap_video_before_ownership_transfer(self):
        for mode in ['failure','timeout','cancel','video_failure','storage']:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                root,metadata,request=self.setup_case(folder);video=FakeVideo();stop=threading.Event();launched=threading.Event()
                def launch(*a,**k): launched.set(); return video
                def subtitle_job(command,cancelled,*args,**kwargs):
                    self.assertTrue(launched.is_set())
                    if mode=='failure':return subprocess.CompletedProcess([],1)
                    if mode=='timeout':raise subprocess.TimeoutExpired(command,90)
                    if mode=='cancel':stop.set()
                    if mode=='video_failure':video.code=1
                    self.assertTrue(cancelled.wait(2), 'Supervisor must cancel subtitle job')
                    raise InterruptedError()
                def disk(_):
                    free=relay.EARLY_FLOOR-1 if mode=='storage' and launched.is_set() else relay.EARLY_FLOOR+1024**3
                    return type('Disk',(),{'free':free})()
                with patch.object(relay,'probe',return_value=metadata),patch.object(relay.subprocess,'Popen',side_effect=launch), \
                     patch.object(relay,'run_cancellable',side_effect=subtitle_job),patch.object(relay,'_stop_group') as reap, \
                     patch.object(relay.shutil,'disk_usage',side_effect=disk),patch.object(relay,'emit'):
                    with self.assertRaises(InterruptedError if mode=='cancel' else ValueError):
                        relay.prepare(request,root,stop,os.getppid())
                    reap.assert_called_once_with(video)
                self.assertFalse((root/'master.m3u8').exists())

if __name__=='__main__':unittest.main()
