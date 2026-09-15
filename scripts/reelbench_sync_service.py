"""Project-owned synchronized review execution, publication and verification."""
from __future__ import annotations
import json
import math
import os
import re
import secrets
import stat
import sys
from contextlib import nullcontext
from pathlib import Path
from scripts import reelbench_workspace as ws
from scripts.json_contracts import canonical_fingerprint, validate_contract, parse_rfc3339
from scripts.reelbench_adapter import PINNED_SYNC_SCRIPTS
from scripts.reelbench_contracts import validate_reelbench_evidence
from scripts.reelbench_project_service import ReelBenchProjectService, _now
from scripts.reelbench_sync_operation import SyncOperationJournal
from scripts.trusted_media_tools import TrustedMediaToolError
from scripts.video_project_store import VersionCommitIndeterminateError

MAX_VIDEO_BYTES = 2 * 1024**3
MAX_PANEL_BYTES = 64 * 1024**2
MAX_OUTPUT_BYTES = MAX_VIDEO_BYTES + 3 * MAX_PANEL_BYTES + 16 * 1024**2
PANELS = {'static.png', 'list-dim.png', 'list-lit.png'}

class ReelBenchSyncError(ValueError):
    """Invalid project evidence or failed measured review gate."""

class ReelBenchSyncBlockedError(ReelBenchSyncError):
    code = 'BLOCKED_MISSING_TRUSTED_BROWSER'

class FinalMediaVerificationError(ReelBenchSyncError):
    """Review media cannot be accepted as generated/final media."""

class ReelBenchSyncService(ReelBenchProjectService):
    """Resolve all consumed bytes from exact Task 3 lineage under the project lock."""
    def __init__(self, *, store, adapter, trusted_tools=None):
        super().__init__(store, adapter)
        self._trusted_tools = trusted_tools

    def plan(self, project_id: str, reelbench_evidence_version: str):
        return self.run(project_id, action='plan', reelbench_evidence_version=reelbench_evidence_version)

    def panels(self, project_id: str, reelbench_evidence_version: str):
        return self.run(project_id, action='panels', reelbench_evidence_version=reelbench_evidence_version)

    def export(self, project_id: str, reelbench_evidence_version: str, *, audio_policy: str):
        return self.run(project_id, action='export', reelbench_evidence_version=reelbench_evidence_version, audio_policy=audio_policy)

    def verify(self, project_id: str, reelbench_evidence_version: str, *, sync_version: str):
        return self.run(project_id, action='verify', reelbench_evidence_version=reelbench_evidence_version, sync_version=sync_version)

    def status(self, project_id: str):
        with self._locked_project(project_id) as (_, fd, _, guard):
            latest = self._latest(fd)
            guard()
            return {'project_id': project_id, 'latest_version': latest}

    def run(self, project_id: str, *, action: str, reelbench_evidence_version: str,
            audio_policy: str | None = None, sync_version: str | None = None):
        if action not in {'plan', 'panels', 'export', 'verify'}:
            raise ReelBenchSyncError('unknown sync action')
        self._version(reelbench_evidence_version)
        if action == 'verify': self._version(sync_version)
        elif sync_version is not None: raise ReelBenchSyncError('sync_version is only valid for verify')
        if action != 'export' and audio_policy is not None:
            raise ReelBenchSyncError('audio policy is only valid for export')
        with self._locked_project(project_id) as (store_fd, fd, root, guard):
            journal = SyncOperationJournal(store_fd, fd, project_id, root)
            journal.recover(self, guard)
            evidence = self._read_version(fd, 'reelbench_evidence', reelbench_evidence_version)
            validate_reelbench_evidence(evidence)
            source = self._read_version(fd, 'source_receipt', evidence['source_receipt_version'])
            validate_contract(source, 'source_receipt.schema.json')
            ReelBenchProjectService._lineage(self, fd, project_id, reelbench_evidence_version, source)
            if evidence['action'] != 'validate' or any(g['status'] == 'FAIL' for g in evidence['gates']):
                raise ReelBenchSyncError('sync requires exact validated ReelBench evidence')
            shots = self._comparison_reelbench_documents(fd, evidence)['shots']
            if self._timeline_mismatches(shots['shots'], source['duration_seconds'], 'source'):
                raise ReelBenchSyncError('shots do not cover source continuously')
            if abs(shots['meta']['durationSeconds'] - source['duration_seconds']) > .001:
                raise ReelBenchSyncError('shots/source duration differs')
            if not 0 < source['duration_seconds'] <= 1800 or source['size_bytes'] > MAX_VIDEO_BYTES:
                raise ReelBenchSyncError('source exceeds preventive bounds')
            previous = None
            if action == 'verify':
                previous = self._read_sync(fd, project_id, sync_version)
                if previous['reelbench_evidence_version'] != reelbench_evidence_version or previous['action'] != 'export':
                    raise ReelBenchSyncError('verify requires exact exported evidence binding')
                audio_policy = previous['audio_policy']
            if action in {'export', 'verify'}: self._policy(fd, source, audio_policy)
            lease = self._browser_lease() if action in {'panels', 'export'} else nullcontext(None)
            result = None
            try:
                with lease as browser:
                    result = self._execute_sync(fd, root, project_id, action, source, evidence,
                        shots, audio_policy, previous, browser, guard, journal)
                    guard()
                return result
            except BaseException as exc:
                if result is not None and action in {'panels', 'export'}:
                    raise self._indeterminate(root, result) from exc
                raise

    def _execute_sync(self, fd, root, project_id, action, source, evidence, shots, policy, previous, browser, guard, journal):
        latest = self._latest(fd)
        parent = self._read_sync(fd, project_id, latest) if latest else None
        version = self._next_version(fd, 'reelbench_sync')
        name = '.reelbench-sync-work-' + secrets.token_hex(12)
        os.mkdir(name, 0o700, dir_fd=fd)
        work = output_fd = None
        marker_created = published = False
        receipt = None
        try:
            work = os.open(name, ws.DIRECTORY, dir_fd=fd)
            marker = journal.create(work, name, action=action, version=version, parent=latest,
                parent_fingerprint=parent['evidence_fingerprint'] if parent else None, source=source)
            marker_created = True
            os.fsync(fd)
            consumed = self._materialize(fd, work, root, source, evidence)
            original = consumed[0]['workspace_path']
            ws.mkdir(work, 'output')
            commands = []
            with self._adapter.execution_workspace(work, consumed, workflow='sync', browser_lease=browser):
                base = Path('/dev/fd') / str(work)
                measured_source = self._probe(original, commands)
                measured_duration = float(measured_source['format']['duration'])
                if not math.isfinite(measured_duration) or abs(measured_duration-source['duration_seconds']) > .25:
                    raise ReelBenchSyncError('measured source duration differs')
                self._adapter.set_sync_duration(measured_duration)
                planned = self._adapter.sync_plan(shots=base / 'inputs/shots.json', source=base / original)
                commands.append(planned)
                plan = self._json(planned.stdout.encode())
                self._validate_plan(plan, source)
                if action == 'plan': return plan
                ws.write(work, 'output/plan.json', json.dumps(plan, sort_keys=True).encode())
                video = original
                if action == 'export' and policy == 'silent':
                    video = 'source/silent.mp4'
                    commands.append(self._adapter.sync_media('ffmpeg', ['-v', 'error', '-n', '-i', original,
                        '-map', '0:v:0', '-c:v', 'copy', '-an', '-movflags', '+faststart', video]))
                    if self._audio(self._probe(video, commands)):
                        raise ReelBenchSyncError('silent derivative contains audio')
                if action == 'panels':
                    commands.append(self._adapter.sync_panels(shots=base / 'inputs/shots.json', source=base / video,
                        panels_dir=base / 'output/panels', browser='tools/browser-proxy'))
                elif action == 'export':
                    upstream_output = 'output/upstream.mp4' if policy == 'preserve_source_audio' else 'output/review.mp4'
                    commands.append(self._adapter.sync_export(shots=base / 'inputs/shots.json', source=base / video,
                        panels_dir=base / 'output/panels', output=base / upstream_output, browser='tools/browser-proxy'))
                    if policy == 'preserve_source_audio':
                        commands.append(self._adapter.sync_media('ffmpeg', ['-v', 'error', '-n', '-i', upstream_output,
                            '-i', original, '-map', '0:v:0', '-map', '1:a', '-c:v', 'copy', '-c:a', 'copy',
                            '-movflags', '+faststart', 'output/review.mp4']))
                else:
                    for artifact in previous['artifacts']:
                        relative = artifact['path'].split('/', 2)[2]
                        if relative != 'plan.json':
                            ws.copy(fd, artifact['path'], work, 'output/' + relative,
                                maximum=MAX_VIDEO_BYTES, expected=artifact, mode=0o400)
                layout = self._json(ws.read(work, 'output/panels/layout.json'))
                self._validate_layout(layout, plan, shots)
                self._validate_panel_images(work, plan, layout, commands)
                gates, alignment = ({}, [])
                if action in {'export', 'verify'}:
                    gates, alignment = self._verify_media(work, original, plan, layout, shots, source, policy, commands)
                for item in consumed:
                    for directory, path in ((work, item['workspace_path']), (fd, item['path'])):
                        if self._digest(directory, path, MAX_VIDEO_BYTES) != {k: item[k] for k in ('sha256', 'size_bytes')}:
                            raise ReelBenchSyncError('consumed artifact changed')
                guard()
                if action == 'verify':
                    self._read_sync(fd, project_id, previous['version'])
                    return {'version': previous['version'], 'artifact_role': 'synchronized_review',
                        'evidence_fingerprint': previous['evidence_fingerprint'], 'gates': gates, 'sampled_alignment': alignment}
                selected = ['plan.json', 'panels/layout.json', *('panels/' + n for n in sorted(PANELS))]
                if action == 'export': selected.append('review.mp4')
                inventory = ws.inventory(work, 'output', max_bytes=4608 * 1024**2, file_maximum=MAX_VIDEO_BYTES)
                ws.mkdir(fd, 'reelbench_sync_media')
                with ws.directory(fd, 'reelbench_sync_media') as family:
                    os.mkdir(version, 0o700, dir_fd=family)
                    output_fd = os.open(version, ws.DIRECTORY, dir_fd=family)
                    journal.bind_output(work, marker, output_fd)
                    os.fsync(family)
                artifacts = []
                for relative in selected:
                    guard()
                    expected = inventory[relative]
                    ws.copy(work, 'output/' + relative, output_fd, relative, maximum=MAX_VIDEO_BYTES, expected=expected)
                    mime = 'video/mp4' if relative.endswith('.mp4') else 'image/png' if relative.endswith('.png') else 'application/json'
                    artifacts.append({'path': f'reelbench_sync_media/{version}/{relative}', 'mime_type': mime, **expected})
                os.fsync(output_fd)
                with ws.directory(fd, 'reelbench_sync_media/' + version) as check:
                    if self._directory_identity(os.fstat(check)) != self._directory_identity(os.fstat(output_fd)):
                        raise ReelBenchSyncError('published media directory changed')
                composition_argv = self._json(ws.read(work, 'output/compose-argv.json')) if action == 'export' else None
                if composition_argv is not None:
                    composition_argv['argv_fingerprint']=canonical_fingerprint(composition_argv)
                command_records = [{'action': c.action, 'argv': c.argv, 'returncode': c.returncode,
                    'expanded_process':composition_argv if c.action == 'export' else None,
                    'tool_identities': list(c.tool_identities), 'script_manifest': list(c.script_manifest)} for c in commands]
                receipt = {'schema_version': '1.2', 'project_id': project_id, 'version': version,
                    'parent_version': latest, 'parent_fingerprint': parent['evidence_fingerprint'] if parent else None,
                    'action': action, 'artifact_role': 'synchronized_review', 'source_receipt_version': source['version'],
                    'source_sha256': source['source_sha256'], 'reelbench_evidence_version': evidence['version'],
                    'reelbench_evidence_fingerprint': evidence['evidence_fingerprint'], 'shots_sha256': evidence['shots']['sha256'],
                    'audio_policy': policy, 'plan': plan, 'layout': layout, 'artifacts': artifacts,
                    'consumed_artifacts': consumed, 'commands': command_records,
                    'composition_argv': composition_argv,
                    'argv_fingerprint': canonical_fingerprint({'commands': command_records}),
                    'browser': None if browser is None else {'identity': browser.context.executable.to_record(), 'verified_at': browser.context.verified_at},
                    'gates': gates, 'sampled_alignment': alignment, 'created_at': _now()}
                receipt['evidence_fingerprint'] = canonical_fingerprint(receipt)
                self._validate_receipt(fd, receipt)
                journal.bind_receipt(work, marker, receipt)
                guard()
                try:
                    result = self._store.write_version(project_id, 'reelbench_sync', receipt, version=version,
                        project_fd=fd, publication_guard=guard)
                except BaseException as exc:
                    try:
                        with ws.file_at(fd, f'reelbench_sync/{version}.json'): published = True
                    except FileNotFoundError: published = isinstance(exc, VersionCommitIndeterminateError)
                    except BaseException: published = True
                    if published: raise self._indeterminate(root, receipt) from exc
                    raise
                published = True
                self._read_sync(fd, project_id, version)
                guard()
                return result
        except BaseException as exc:
            if published and receipt is not None: raise self._indeterminate(root, receipt) from exc
            raise
        finally:
            primary = sys.exc_info()[1]
            cleanup_error = None
            try:
                if work is None: os.rmdir(name, dir_fd=fd)
                elif marker_created: journal.cleanup(work, name, published=published, guard=guard)
                else: ws.remove_tree(fd, name)
            except BaseException as exc: cleanup_error = exc
            finally:
                for descriptor in (output_fd, work):
                    if descriptor is not None:
                        try: ws._close_owned([descriptor])
                        except BaseException as exc:
                            if cleanup_error is None: cleanup_error = exc
            if cleanup_error is not None:
                if primary is not None: ws.cleanup_failure(primary, cleanup_error)
                elif published: raise self._indeterminate(root, receipt) from cleanup_error
                else: raise cleanup_error

    def _materialize(self, fd, work, root, source, evidence):
        relative = Path(source['staged_path']).relative_to(root).as_posix()
        if not relative.startswith('source/'): raise ReelBenchSyncError('source path escaped project')
        records = [{'version': source['version'], 'receipt_fingerprint': canonical_fingerprint(source),
            'path': relative, 'workspace_path': 'source/original.mp4', 'sha256': source['source_sha256'], 'size_bytes': source['size_bytes']}]
        records.extend(dict(item) for item in evidence['consumed_artifacts'] if item['workspace_path'].startswith('inputs/'))
        for item in records:
            with ws.file_at(fd, item['path']) as opened:
                info = os.fstat(opened)
                if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                    raise ReelBenchSyncError('consumed input is not private')
            ws.copy(fd, item['path'], work, item['workspace_path'], maximum=MAX_VIDEO_BYTES,
                expected=item, mode=0o400, quota=True)
        return records

    @staticmethod
    def _policy(fd, source, policy):
        if policy not in {'silent', 'preserve_source_audio'}: raise ReelBenchSyncError('explicit review audio policy is required')
        project = ReelBenchProjectService._json(ws.read(fd, 'project.json'))
        validate_contract(project, 'video_project.schema.json')
        if policy == 'preserve_source_audio' and (project['audio_policy'] != 'preserve_authorized_audio' or not source['audio_streams']):
            raise ReelBenchSyncError('source audio has no approved preservation policy')

    def _browser_lease(self):
        try:
            if self._trusted_tools is None: raise TrustedMediaToolError('browser not enrolled')
            return self._trusted_tools.reverify_browser_for_sync_lease()
        except (TrustedMediaToolError, AttributeError, OSError) as exc:
            raise ReelBenchSyncBlockedError('BLOCKED_MISSING_TRUSTED_BROWSER') from exc

    @staticmethod
    def _validate_plan(plan, source):
        if not isinstance(plan, dict) or set(plan) != {'source', 'portrait', 'stack', 'video', 'panel', 'output', 'fps', 'crf'}:
            raise ReelBenchSyncError('upstream plan fields differ')
        for key in ('source', 'video', 'panel', 'output'):
            if not isinstance(plan[key], dict) or set(plan[key]) != {'width', 'height'} or any(type(x) is not int or not 2 <= x <= 7680 for x in plan[key].values()):
                raise ReelBenchSyncError('invalid upstream geometry')
        if plan['source'] != {'width': source['width'], 'height': source['height']}:
            raise ReelBenchSyncError('planned source geometry differs from source receipt')
        portrait = source['height'] > source['width']
        if type(plan['portrait']) is not bool or plan['portrait'] != portrait or plan['stack'] != ('hstack' if portrait else 'vstack'):
            raise ReelBenchSyncError('invalid upstream stack')
        video, panel, output = (plan[k] for k in ('video', 'panel', 'output'))
        if any(v % 2 for box in (video, panel, output) for v in box.values()): raise ReelBenchSyncError('odd output geometry')
        if abs(video['width'] * source['height'] - video['height'] * source['width']) > 2 * max(source['width'], source['height']):
            raise ReelBenchSyncError('source aspect changed')
        if output != ({'width': video['width'] + panel['width'], 'height': video['height']} if portrait else {'width': video['width'], 'height': video['height'] + panel['height']}):
            raise ReelBenchSyncError('output stack geometry inconsistent')
        if (panel['height'] != video['height'] if portrait else panel['width'] != video['width']): raise ReelBenchSyncError('panel stack edge inconsistent')
        if type(plan['fps']) not in (int, float) or not 1 <= plan['fps'] <= 60 or type(plan['crf']) not in (int, float) or not 0 <= plan['crf'] <= 51:
            raise ReelBenchSyncError('invalid upstream encoding parameters')

    @staticmethod
    def _validate_layout(layout, plan, shots):
        if not isinstance(layout, dict) or set(layout) != {'panel', 'view', 'content', 'rows', 'tallHeight'} or layout['panel'] != plan['panel']:
            raise ReelBenchSyncError('layout fields or panel differ')
        view = layout['view']
        if not isinstance(view, dict) or set(view) != {'x', 'y', 'width', 'height'} or any(type(v) is not int for v in view.values()):
            raise ReelBenchSyncError('invalid view geometry')
        if view['x'] < 0 or view['y'] < 0 or view['width'] < 2 or view['height'] < 2 or view['x'] + view['width'] > plan['panel']['width'] or view['y'] + view['height'] > plan['panel']['height']:
            raise ReelBenchSyncError('view escapes panel')
        if type(layout['content']) is not int or not 1 <= layout['content'] <= 16000 or type(layout['tallHeight']) is not int or layout['tallHeight'] != max(plan['panel']['height'], layout['content']):
            raise ReelBenchSyncError('invalid list height')
        rows = layout['rows']
        if not isinstance(rows, list) or len(rows) != len(shots['shots']): raise ReelBenchSyncError('row count differs')
        previous = 0
        for row, shot in zip(rows, shots['shots']):
            if not isinstance(row, dict) or set(row) != {'id', 'top', 'height'} or row['id'] != shot['id'] or type(row['top']) is not int or type(row['height']) is not int:
                raise ReelBenchSyncError('invalid layout row')
            if row['top'] < previous or row['height'] < 2 or row['height'] > view['height'] or row['top'] + row['height'] > layout['content']:
                raise ReelBenchSyncError('layout row escapes list or overlaps')
            previous = row['top'] + row['height']

    def _probe(self, path, commands):
        result = self._adapter.sync_media('ffprobe', ['-v', 'error', '-show_streams', '-show_format', '-of', 'json', path])
        commands.append(result)
        return self._json(result.stdout.encode())

    @staticmethod
    def _audio(probe): return [s for s in probe['streams'] if s['codec_type'] == 'audio']

    def _validate_panel_images(self, work, plan, layout, commands):
        inventory = ws.inventory(work, 'output/panels', max_bytes=3 * MAX_PANEL_BYTES + 8 * 1024**2, file_maximum=MAX_PANEL_BYTES)
        if {n for n in inventory if n.endswith('.png')} != PANELS or set(inventory) - PANELS - {'layout.json', 'panel.html', 'motion.cmd'}:
            raise ReelBenchSyncError('panel inventory differs')
        for name in sorted(PANELS):
            video = self._probe('output/panels/' + name, commands)['streams']
            height = plan['panel']['height'] if name == 'static.png' else layout['tallHeight']
            if len(video) != 1 or video[0].get('codec_name') != 'png' or (video[0].get('width'), video[0].get('height')) != (plan['panel']['width'], height):
                raise ReelBenchSyncError('panel pixels differ from measured layout')

    def _verify_media(self, work, original, plan, layout, shots, source, policy, commands):
        media = self._probe('output/review.mp4', commands)
        videos = [s for s in media['streams'] if s['codec_type'] == 'video']
        if len(videos) != 1: raise ReelBenchSyncError('review must have one video stream')
        video = videos[0]
        duration = float(media['format']['duration'])
        gates = {'duration': {'passed': math.isfinite(duration) and abs(duration - source['duration_seconds']) <= .25, 'measured': duration},
            'dimensions': {'passed': (video.get('width'), video.get('height')) == (plan['output']['width'], plan['output']['height']), 'measured': [video.get('width'), video.get('height')]},
            'codec': {'passed': video.get('codec_name') == 'h264' and video.get('pix_fmt') == 'yuv420p', 'measured': video.get('codec_name')}}
        if policy == 'silent':
            gates['audio_policy'] = {'passed': not self._audio(media), 'measured': len(self._audio(media))}
        else:
            original_probe = self._probe(original, commands)
            fields = ('codec_name', 'channels', 'sample_rate', 'channel_layout', 'profile')
            source_streams = [{k: a.get(k) for k in fields} for a in self._audio(original_probe)]
            output_streams = [{k: a.get(k) for k in fields} for a in self._audio(media)]
            source_packets, output_packets = self._packet_hashes(original, commands), self._packet_hashes('output/review.mp4', commands)
            gates['audio_policy'] = {'passed': bool(source_packets) and source_packets == output_packets and source_streams == output_streams,
                'measured': {'source_packets': canonical_fingerprint({'packets':source_packets}), 'output_packets': canonical_fingerprint({'packets':output_packets}), 'streams': output_streams}}
        if any(not g['passed'] for g in gates.values()): raise ReelBenchSyncError('review media gate failed: ' + json.dumps(gates))
        alignment = self._sample_alignment(work, original, plan, layout, shots, commands)
        gates['cut_alignment'] = {'passed': all(c['error'] <= 18 for s in alignment for c in s['cut_samples']), 'measured': sum(len(s['cut_samples']) for s in alignment)}
        gates['sampled_correspondence'] = {'passed': all(s['highlight_error'] <= 22 and s['highlight_margin'] >= 1 for s in alignment), 'measured': len(alignment)}
        if any(not g['passed'] for g in gates.values()): raise ReelBenchSyncError('sampled review correspondence failed: ' + json.dumps(alignment))
        return gates, alignment

    def _packet_hashes(self, path, commands):
        result = self._adapter.sync_media('ffprobe', ['-v', 'error', '-select_streams', 'a', '-show_packets',
            '-show_data_hash', 'sha256', '-show_entries', 'packet=stream_index,data_hash,size', '-of', 'json', path])
        commands.append(result)
        packets = json.loads(result.stdout)['packets']
        grouped = {}
        for packet in packets:
            if not {'stream_index', 'size', 'data_hash'} <= set(packet): raise ReelBenchSyncError('missing audio packet hash')
            grouped.setdefault(packet['stream_index'], []).append([packet['size'], packet['data_hash']])
        return list(grouped.values())

    def _pixels(self, work, path, seconds, crop, commands, *, video_scale=None):
        name = 'output/sample-' + secrets.token_hex(8) + '.gray'
        filters = []
        if video_scale is not None: filters.append(f'scale={video_scale[0]}:{video_scale[1]}:flags=lanczos')
        filters.extend([f'crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}', 'scale=64:32', 'format=gray'])
        commands.append(self._adapter.sync_media('ffmpeg', ['-v', 'error', '-n', '-i', path, '-ss', f'{seconds:.6f}',
            '-frames:v', '1', '-vf', ','.join(filters), '-f', 'rawvideo', name]))
        payload = ws.read(work, name, 2048)
        if len(payload) != 2048: raise ReelBenchSyncError('sample decode returned incomplete pixels')
        with ws.directory(work, 'output') as output: os.unlink(name.split('/')[-1], dir_fd=output)
        return payload

    @staticmethod
    def _distance(a, b): return sum(abs(x-y) for x,y in zip(a,b)) / len(a)

    def _sample_alignment(self, work, original, plan, layout, shots, commands):
        view, rows = layout['view'], layout['rows']
        vw, vh = plan['video']['width'], plan['video']['height']
        px, py = (vw, 0) if plan['portrait'] else (0, vh)
        anchor = rows[min(1, len(rows)-1)]['top'] - rows[0]['top']
        samples = []
        for index, (shot, row) in enumerate(zip(shots['shots'], rows)):
            start, end = float(shot['start']), float(shot['end'])
            stable = start if index == 0 else start + min(.45, end-start)
            frame = math.ceil((stable + 1/plan['fps']) * plan['fps']) / plan['fps']
            if frame >= end: raise ReelBenchSyncError('shot has no stable frame for highlight verification')
            offset = min(max(0, layout['content'] - view['height']), max(0, row['top'] - anchor))
            y = max(view['y'], min(view['y'] + view['height'] - row['height'], view['y'] + row['top'] - offset))
            source_pixels = self._pixels(work, original, frame, (0,0,vw,vh), commands, video_scale=(vw,vh))
            review_pixels = self._pixels(work, 'output/review.mp4', frame, (0,0,vw,vh), commands)
            actual = self._pixels(work, 'output/review.mp4', frame, (px+view['x'],py+y,view['width'],row['height']), commands)
            lit = self._pixels(work, 'output/panels/list-lit.png', 0, (view['x'],row['top'],view['width'],row['height']), commands)
            dim = self._pixels(work, 'output/panels/list-dim.png', 0, (view['x'],row['top'],view['width'],row['height']), commands)
            lit_error = self._distance(actual, lit)
            cut_samples=[]
            cut_frame=math.ceil(start*plan['fps'])/plan['fps']
            for seconds in sorted({max(0,cut_frame-1/plan['fps']),cut_frame}):
                left=self._pixels(work,original,seconds,(0,0,vw,vh),commands,video_scale=(vw,vh))
                right=self._pixels(work,'output/review.mp4',seconds,(0,0,vw,vh),commands)
                cut_samples.append({'seconds':seconds,'error':self._distance(left,right)})
            samples.append({'shot_id': shot['id'], 'cut_seconds': start, 'sample_seconds': frame,
                'cut_samples':cut_samples,
                'source_error': self._distance(source_pixels, review_pixels), 'highlight_error': lit_error,
                'highlight_margin': self._distance(actual, dim) - lit_error})
        return samples

    def _read_sync(self, fd, project_id, version):
        receipt = self._read_version(fd, 'reelbench_sync', version)
        self._validate_receipt(fd, receipt)
        body = {k:v for k,v in receipt.items() if k != 'evidence_fingerprint'}
        if receipt.get('artifact_role') != 'synchronized_review' or receipt.get('project_id') != project_id or canonical_fingerprint(body) != receipt.get('evidence_fingerprint'):
            raise ReelBenchSyncError('sync receipt fingerprint or identity differs')
        expected = {}
        for artifact in receipt['artifacts']:
            prefix = 'reelbench_sync_media/' + version + '/'
            if not artifact['path'].startswith(prefix): raise ReelBenchSyncError('sync artifact escaped version')
            relative = artifact['path'][len(prefix):]
            if relative in expected: raise ReelBenchSyncError('duplicate sync artifact')
            expected[relative] = {k:artifact[k] for k in ('sha256', 'size_bytes')}
        observed = ws.inventory(fd, 'reelbench_sync_media/' + version, max_bytes=MAX_OUTPUT_BYTES, file_maximum=MAX_VIDEO_BYTES, private=True)
        if observed != expected: raise ReelBenchSyncError('sync artifact inventory or digest differs')
        return receipt

    def _validate_receipt(self, fd, receipt):
        required = {'schema_version','project_id','version','parent_version','parent_fingerprint','action',
            'artifact_role','source_receipt_version','source_sha256','reelbench_evidence_version',
            'reelbench_evidence_fingerprint','shots_sha256','audio_policy','plan','layout','artifacts',
            'consumed_artifacts','commands','composition_argv','argv_fingerprint','browser','gates',
            'sampled_alignment','created_at','evidence_fingerprint'}
        if not isinstance(receipt, dict) or set(receipt) != required or receipt['schema_version'] != '1.2' or receipt['action'] not in {'panels','export'}:
            raise ReelBenchSyncError('sync receipt fields/action differ')
        if canonical_fingerprint({k:v for k,v in receipt.items() if k != 'evidence_fingerprint'}) != receipt['evidence_fingerprint']:
            raise ReelBenchSyncError('sync receipt fingerprint differs')
        self._version(receipt['version'])
        self._version(receipt['parent_version'], nullable=True)
        number = int(receipt['version'][1:])
        if (receipt['parent_version'] is None and (number != 1 or receipt['parent_fingerprint'] is not None)) or (receipt['parent_version'] is not None and int(receipt['parent_version'][1:]) != number-1):
            raise ReelBenchSyncError('sync parent is not preceding version')
        if receipt['parent_version'] is not None:
            parent = self._read_version(fd,'reelbench_sync',receipt['parent_version'])
            if canonical_fingerprint({k:v for k,v in parent.items() if k != 'evidence_fingerprint'}) != receipt['parent_fingerprint'] or parent['evidence_fingerprint'] != receipt['parent_fingerprint']:
                raise ReelBenchSyncError('sync parent fingerprint differs')
        parse_rfc3339(receipt['created_at'], label='created_at')
        source = self._read_version(fd,'source_receipt',receipt['source_receipt_version'])
        evidence = self._read_version(fd,'reelbench_evidence',receipt['reelbench_evidence_version'])
        validate_contract(source,'source_receipt.schema.json')
        validate_reelbench_evidence(evidence)
        if source['project_id'] != receipt['project_id'] or source['source_sha256'] != receipt['source_sha256'] or evidence['evidence_fingerprint'] != receipt['reelbench_evidence_fingerprint'] or evidence['shots']['sha256'] != receipt['shots_sha256']:
            raise ReelBenchSyncError('sync source/evidence binding differs')
        shots = self._comparison_reelbench_documents(fd,evidence)['shots']
        self._validate_plan(receipt['plan'],source)
        self._validate_layout(receipt['layout'],receipt['plan'],shots)
        expected = {'plan.json','panels/layout.json',*('panels/'+n for n in PANELS)}
        if receipt['action'] == 'export': expected.add('review.mp4')
        prefix = 'reelbench_sync_media/' + receipt['version'] + '/'
        paths = []
        for artifact in receipt['artifacts']:
            if set(artifact) != {'path','mime_type','size_bytes','sha256'} or not artifact['path'].startswith(prefix): raise ReelBenchSyncError('invalid sync artifact')
            path = artifact['path'][len(prefix):]
            paths.append(path)
            if type(artifact['size_bytes']) is not int or not 0 < artifact['size_bytes'] <= (MAX_VIDEO_BYTES if path.endswith('.mp4') else MAX_PANEL_BYTES) or re.fullmatch('[a-f0-9]{64}',artifact['sha256']) is None:
                raise ReelBenchSyncError('invalid sync artifact digest/size')
        if len(paths) != len(expected) or set(paths) != expected: raise ReelBenchSyncError('incomplete sync artifact set')
        commands = receipt['commands']
        if not isinstance(commands,list) or not 1 <= len(commands) <= 1200 or canonical_fingerprint({'commands':commands}) != receipt['argv_fingerprint']:
            raise ReelBenchSyncError('invalid command provenance')
        for command in commands:
            if set(command) != {'action','argv','returncode','tool_identities','script_manifest','expanded_process'} or command['returncode'] != 0 or command['action'] not in {'ffmpeg','ffprobe','plan','panels','export'}:
                raise ReelBenchSyncError('invalid sync command')
            if command['expanded_process'] != (receipt['composition_argv'] if command['action']=='export' else None):
                raise ReelBenchSyncError('expanded command is outside ordered provenance')
            manifest = {m['path']:(m['sha256'],m['size_bytes']) for m in command['script_manifest']}
            if any(manifest.get('script/'+name) != pin for name,pin in PINNED_SYNC_SCRIPTS.items()):
                raise ReelBenchSyncError('sync command lacks independently pinned scripts')
        if receipt['action'] == 'panels':
            if receipt['audio_policy'] is not None or receipt['gates'] or receipt['sampled_alignment'] or receipt['composition_argv'] is not None:
                raise ReelBenchSyncError('panel receipt cannot claim video verification')
        else:
            expanded=receipt['composition_argv']
            if not isinstance(expanded,dict) or set(expanded) != {'upstream_argv','actual_argv','argv_fingerprint'} or canonical_fingerprint({k:v for k,v in expanded.items() if k!='argv_fingerprint'}) != expanded['argv_fingerprint']:
                raise ReelBenchSyncError('expanded command fingerprint differs')
            if receipt['audio_policy'] not in {'silent','preserve_source_audio'} or set(receipt['gates']) != {'duration','dimensions','codec','audio_policy','cut_alignment','sampled_correspondence'}:
                raise ReelBenchSyncError('incomplete review gates')
            if any(set(g) != {'passed','measured'} or g['passed'] is not True for g in receipt['gates'].values()): raise ReelBenchSyncError('failed review gate')
            samples = receipt['sampled_alignment']
            if [s['shot_id'] for s in samples] != [s['id'] for s in shots['shots']]: raise ReelBenchSyncError('sample identity differs')
            for sample,shot in zip(samples,shots['shots']):
                if set(sample) != {'shot_id','cut_seconds','sample_seconds','source_error','highlight_error','highlight_margin','cut_samples'} or sample['cut_seconds'] != shot['start'] or not shot['start'] <= sample['sample_seconds'] < shot['end']:
                    raise ReelBenchSyncError('invalid shot sample')
                if not 1 <= len(sample['cut_samples']) <= 2 or any(set(c) != {'seconds','error'} or type(c['error']) not in (int,float) or not math.isfinite(c['error']) or not 0 <= c['error'] <= 18 for c in sample['cut_samples']):
                    raise ReelBenchSyncError('failed cut measurements')
                if any(type(sample[k]) not in (int,float) or not math.isfinite(sample[k]) for k in ('source_error','highlight_error','highlight_margin')) or not 0 <= sample['source_error'] <= 18 or not 0 <= sample['highlight_error'] <= 22 or sample['highlight_margin'] < 1:
                    raise ReelBenchSyncError('failed correspondence measurements')

    def _lineage(self, fd, project_id, version, source):
        receipt = self._read_sync(fd, project_id, version)
        if receipt['source_sha256'] != source['source_sha256'] or receipt['source_receipt_version'] != source['version']:
            raise ReelBenchSyncError('sync source differs')
        evidence = self._read_version(fd, 'reelbench_evidence', receipt['reelbench_evidence_version'])
        if evidence['evidence_fingerprint'] != receipt['reelbench_evidence_fingerprint']:
            raise ReelBenchSyncError('sync evidence binding differs')
        ReelBenchProjectService._lineage(self, fd, project_id, evidence['version'], source)
        return [receipt]

    @classmethod
    def _latest(cls, fd):
        try:
            with ws.directory(fd, 'reelbench_sync') as family:
                versions = [name[:-5] for name in os.listdir(family) if name.endswith('.json') and name.startswith('v')]
            for version in versions: cls._version(version)
            return max(versions, key=lambda v:int(v[1:]), default=None)
        except FileNotFoundError: return None

    @staticmethod
    def _indeterminate(root, receipt):
        return VersionCommitIndeterminateError(project_id=receipt['project_id'], family='reelbench_sync',
            version=receipt['version'], path=root / 'reelbench_sync' / (receipt['version'] + '.json'),
            payload_fingerprint=canonical_fingerprint(receipt))

    def reconcile_indeterminate(self, error):
        if error.family != 'reelbench_sync': raise ReelBenchSyncError('wrong recovery family')
        with self._locked_project(error.project_id) as (_, fd, _, guard):
            receipt = self._read_sync(fd, error.project_id, error.version)
            if canonical_fingerprint(receipt) != error.payload_fingerprint: raise ReelBenchSyncError('exact recovery fingerprint differs')
            source = self._read_version(fd, 'source_receipt', receipt['source_receipt_version'])
            self._lineage(fd, error.project_id, error.version, source)
            guard()
            return receipt

    @staticmethod
    def accept_generated_shot(receipt):
        raise FinalMediaVerificationError('synchronized_review is never generated or final media')
