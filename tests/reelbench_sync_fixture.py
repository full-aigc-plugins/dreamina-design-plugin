"""Private trusted-runtime fixture; no host enrollment/configuration changes."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from scripts.reelbench_adapter import ReelBenchAdapter
from scripts.reelbench_sync_service import ReelBenchSyncService
from scripts.trusted_media_tools import TrustedMediaToolStore
from tests.test_reelbench_project_service import ReelBenchProjectServiceTests
from tests.test_trusted_media_tools import _Approve

class SyncFixture:
    def __init__(self, case, *, browser=False, audio=False, cuts=False):
        if any(shutil.which(kind) is None for kind in ('node','ffmpeg','ffprobe')):
            case.skipTest('real trusted Node/FFmpeg fixture prerequisite unavailable')
        fixture = ReelBenchProjectServiceTests()
        fixture.setUp()
        case.addCleanup(fixture.doCleanups)
        self.project = fixture
        self.root = Path(fixture.temp.name).resolve()
        source = fixture.source
        source.chmod(0o600)
        video='color=c=red:s=640x360:r=30:d=4[a];color=c=blue:s=640x360:r=30:d=4[b];[a][b]concat=n=2:v=1:a=0' if cuts else 'testsrc2=s=640x360:r=25:d=8'
        args = [shutil.which('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi', '-i', video]
        if audio: args += ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=8', '-c:a', 'aac']
        args += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)]
        subprocess.run(args, check=True, capture_output=True)
        source.chmod(0o400)
        data = {k:v for k,v in fixture.source_receipt.items() if k != 'version'}
        data.update(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), size_bytes=source.stat().st_size,
            audio_streams=[{'codec':'aac','channels':1,'sample_rate':44100}] if audio else [])
        fixture.source_receipt = fixture.store.write_version(fixture.project_id, 'source_receipt', data, schema_name='source_receipt.schema.json')
        if cuts:
            from scripts.bounded_process import BoundedProcessResult
            from scripts import reelbench_workspace as ws
            original=fixture.service._adapter._runner
            def two_shots(argv,**kwargs):
                result=original(argv,**kwargs)
                if argv[8]=='seed':
                    doc=json.loads(result.stdout)
                    doc['shots']=[{'id':'S01','start':0,'end':4,'seconds':4,'motion':0}, {'id':'S02','start':4,'end':8,'seconds':4,'motion':0}]
                    return BoundedProcessResult(0,json.dumps(doc),'')
                if argv[8]=='frames':
                    import os
                    with ws.directory(int(argv[4]),'output/frames') as directory: os.fchmod(directory,0o700)
                    for pick in ('a','b'): ws.write(int(argv[4]),'output/frames/S02'+pick+'.jpg',b'frame')
                return result
            fixture.service._adapter._runner=two_shots
        self.evidence = fixture._validated_reelbench()
        self.trust = TrustedMediaToolStore(self.root / 'trust.json', self.root / 'staging')
        for kind in ('node', 'ffmpeg', 'ffprobe'):
            tool = self.root / ('trusted-' + kind)
            shutil.copyfile(Path(shutil.which(kind)).resolve(), tool)
            tool.chmod(0o500)
            self.trust.enroll(kind, tool, approval_provider=_Approve())
        if browser:
            self.trust.enroll('browser', Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'), approval_provider=_Approve())
        self.adapter = ReelBenchAdapter(project_root=fixture.store.project_root(fixture.project_id),
            shots_script=Path(__file__).resolve().parents[1] / 'skills/dreamina-video-shots/scripts/video-shots.mjs',
            tools={k:self.trust.resolve_verified(k) for k in ('node','ffmpeg','ffprobe')}, tool_store=self.trust)
        self.service = ReelBenchSyncService(store=fixture.store, adapter=self.adapter, trusted_tools=self.trust)

    @property
    def args(self): return self.project.project_id, self.evidence['version']
