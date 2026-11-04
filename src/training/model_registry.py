"""Immutable local research model records; promotion is a separate human action."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid

from ..hydroponic.config import ROOT

FILENAMES = {'checkpoint': 'model.safetensors', 'config': 'config.json',
             'train_manifest': 'train_manifest.jsonl', 'provenance': 'provenance.json',
             'evaluation': 'evaluation.json', 'validation_manifest': 'validation_manifest.jsonl'}
REQUIRED = set(FILENAMES) - {'validation_manifest'}


def bounded(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT.resolve()) or path == ROOT.resolve():
        raise ValueError('Path must be below project root')
    return path


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, record):
    with Path(path).open('x', encoding='utf8') as stream:
        stream.write(json.dumps(record, indent=2, allow_nan=False)+'\n')
        stream.flush(); os.fsync(stream.fileno())


@contextmanager
def mutation_lock(root):
    path = bounded(Path(root)/'.mutation.lock')
    with path.open('x', encoding='utf8') as stream:
        stream.write(str(os.getpid())); stream.flush(); os.fsync(stream.fileno())
    try:
        yield
    finally:
        path.unlink()


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf8').splitlines() if line.strip()]


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class ModelRegistry:
    def __init__(self, root=ROOT/'models/lettuce'):
        self.root = bounded(root)

    def _version(self, version):
        if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', version):
            raise ValueError('Invalid model version')
        return bounded(self.root/'versions'/version)

    def register(self, record):
        """Copy every hashed artifact, creating a version exactly once; no promotion."""
        record = json.loads(json.dumps(record, allow_nan=False))
        destination = self._version(record['version'])
        if record.get('species') != 'lettuce' or record.get('architecture') not in ('unet_resnet18','segformer_b0'):
            raise ValueError('Only supported lettuce architectures may be registered')
        artifacts = record.get('artifacts', {})
        if not REQUIRED.issubset(artifacts) or set(artifacts) - set(FILENAMES):
            raise ValueError('Missing or unknown model artifacts')
        contents = {}
        for name, ref in artifacts.items():
            data = bounded(ref['path']).read_bytes()
            if not data or sha256(data) != ref.get('sha256'):
                raise ValueError('Model artifact SHA256 mismatch: '+name)
            if name in ('config','provenance','evaluation'):
                if not isinstance(json.loads(data),dict):
                    raise ValueError('Model JSON artifact must be an object: '+name)
            contents[name] = data
        # Validate serialization and all inputs before creating an immutable directory.
        self.root.mkdir(parents=True, exist_ok=True)
        with mutation_lock(self.root):
            destination.mkdir(parents=True, exist_ok=False)
            refs = {}
            for name, data in contents.items():
                path = bounded(destination/FILENAMES[name])
                with path.open('xb') as stream:
                    stream.write(data);stream.flush();os.fsync(stream.fileno())
                refs[name] = dict(path=path.relative_to(self.root).as_posix(), sha256=sha256(data), bytes=len(data))
            saved = {**record, 'schema_version': 1, 'registered_at': stamp(), 'artifacts': refs,
                     'automatic_promotion': False}
            write_json(destination/'registry.json', saved)
        return self.get(record['version'])

    def get(self, version):
        path = bounded(self._version(version)/'registry.json')
        record = json.loads(path.read_text(encoding='utf8'))
        if record.get('version') != version or record.get('species') != 'lettuce':
            raise ValueError('Invalid saved model record')
        artifacts = record.get('artifacts', {})
        if not REQUIRED.issubset(artifacts) or set(artifacts)-set(FILENAMES):
            raise ValueError('Invalid saved model artifacts')
        for name, ref in artifacts.items():
            expected = self._version(version)/FILENAMES[name]
            actual = bounded(self.root/ref['path'])
            if actual != expected or sha256(actual.read_bytes()) != ref['sha256']:
                raise ValueError('Saved model artifact changed: '+name)
        return record

    def list(self):
        path = self.root/'versions'
        return [self.get(p.name) for p in sorted(path.iterdir()) if p.is_dir() and (p/'registry.json').is_file()] if path.exists() else []

    def champion(self):
        pointer = self.root/'champion.json'
        if not pointer.exists():
            return None
        saved = json.loads(bounded(pointer).read_text(encoding='utf8'))
        record = self.get(saved['version'])
        if saved.get('report_sha256') != record['artifacts']['evaluation']['sha256']:
            raise ValueError('Champion pointer/report mismatch')
        return record

    def _public_gate(self, report):
        reasons = []
        if report.get('target_validation') != 'UNVALIDATED / REVIEW REQUIRED':
            reasons.append('Initial research challenger must retain unvalidated target status')
        selection = report.get('selection') or {}
        if not isinstance(selection,dict):
            reasons.append('Invalid development selection schema');selection={}
        baseline = selection.get('baseline') or {}
        if not isinstance(baseline,dict):
            reasons.append('Invalid development baseline schema');baseline={}
        if (selection.get('synthetic') is True or selection.get('training_anchor') is True or
            selection.get('diagnostic') is True or selection.get('synthetic_count',0) != 0):
            reasons.append('Synthetic/training-anchor selection is not public development evidence')
        if selection.get('domain') != 'oak1_dev' or type(selection.get('sample_count')) is not int or selection['sample_count'] < 1:
            reasons.append('Missing OAK-1 development selection')
        values = [selection.get('mean_iou'), selection.get('mean_abs_percentage_area_error'),
                  baseline.get('mean_iou'), baseline.get('mean_abs_percentage_area_error')]
        if not all(finite(v) for v in values) or not all(0 <= v <= 1 for v in (values[0], values[2]) if finite(v)) or any(v < 0 for v in (values[1],values[3]) if finite(v)):
            reasons.append('Missing/invalid finite development metrics')
        elif values[0] < values[2]+.03 or values[1] > .85*values[3]:
            reasons.append('Development IoU +0.03 / area error x0.85 gates not met')
        confirmations = report.get('confirmation') or []
        if not isinstance(confirmations,list):
            reasons.append('Invalid held-out confirmation schema');confirmations=[]
        count = 0
        for entry in confirmations:
            if not isinstance(entry,dict):
                reasons.append('Invalid held-out confirmation entry');continue
            if (entry.get('split') not in ('PUBLIC_TEST','EXTERNAL_TEST') or entry.get('group_disjoint') is not True or
                entry.get('synthetic') is True or entry.get('training_anchor') is True or
                'synthetic' in entry.get('domain','').lower() or 'diagnostic' in entry.get('domain','').lower() or
                type(entry.get('sample_count')) is not int or entry['sample_count'] < 10 or
                not finite(entry.get('mean_iou')) or not 0 <= entry['mean_iou'] <= 1 or
                not finite(entry.get('mean_abs_percentage_area_error')) or entry['mean_abs_percentage_area_error'] < 0):
                reasons.append('Invalid held-out public confirmation')
            else:
                count += entry['sample_count']
                reference=entry.get('baseline') or {}
                if not isinstance(reference,dict):reference={}
                baseline_iou=reference.get('mean_iou');baseline_mape=reference.get('mean_abs_percentage_area_error')
                if (not finite(baseline_iou) or not 0 <= baseline_iou <= 1 or
                    not finite(baseline_mape) or baseline_mape < 0):
                    reasons.append('Held-out confirmation requires matched frozen baseline metrics')
                elif entry['mean_iou'] < baseline_iou-.03 or entry['mean_abs_percentage_area_error'] > baseline_mape*1.15:
                    reasons.append('Held-out confirmation fails the predeclared stability veto')
        if count < 10:
            reasons.append('At least ten held-out public/external rows required')
        return reasons

    def _real_gate(self, record, report, current):
        reasons = []
        validation = report.get('validation') or {}
        if not isinstance(validation,dict):
            reasons.append('Invalid real validation schema');validation={}
        if (validation.get('kind') != 'real_untouched' or validation.get('plant_disjoint') is not True or
            any(validation.get(k) != 0 for k in ('synthetic_count','diagnostic_count','training_overlap_count')) or
            type(validation.get('mask_count')) is not int or validation['mask_count'] < 5 or
            type(validation.get('plant_group_count')) is not int or validation['plant_group_count'] < 2):
            reasons.append('Insufficient independent untouched real validation')
        ref = record['artifacts'].get('validation_manifest')
        if ref is None or validation.get('manifest_sha256') != ref['sha256']:
            reasons.append('Missing immutable real validation manifest')
        else:
            val = read_jsonl(self.root/ref['path'])
            train = read_jsonl(self.root/record['artifacts']['train_manifest']['path'])
            groups = {r.get('group_id') for r in val}
            images = {r.get('image_sha256') for r in val}
            if (None in groups or None in images or len(images) != len(val) or len(val) != validation.get('mask_count') or
                len(groups) != validation.get('plant_group_count') or
                groups & {r.get('group_id') for r in train} or images & {r.get('image_sha256') for r in train} or
                any(r.get('dataset') != 'real_approved' or r.get('split') != 'real_validation' or
                    r.get('synthetic') is True or r.get('diagnostic') is True for r in val)):
                reasons.append('Validation manifest contains duplicates, exposed data or training overlap')
        comparison = report.get('comparison') or {}
        if not isinstance(comparison,dict):
            reasons.append('Invalid real comparison schema');comparison={}
        if comparison.get('status') != 'COMPLETED' or comparison.get('current_champion_version') != current:
            reasons.append('Missing comparison against current champion')
        candidate = comparison.get('candidate') or {}
        if not isinstance(candidate,dict):candidate={}
        references = [comparison.get('baseline') or {}]
        if current is not None:
            references.append(comparison.get('champion', {}) or {})
        keys = ('iou','dice','area_mape','qc_failure_rate')
        for reference in references:
            if not isinstance(reference,dict):
                reasons.append('Invalid comparison reference schema');continue
            if not all(finite(item.get(k)) for item in (candidate,reference) for k in keys):
                reasons.append('Missing finite comparison metrics');continue
            if (any(not 0 <= item[k] <= 1 for item in (candidate,reference) for k in ('iou','dice','qc_failure_rate')) or
                any(item['area_mape'] < 0 for item in (candidate,reference)) or
                candidate['iou'] <= reference['iou'] or candidate['dice'] < reference['dice'] or
                candidate['area_mape'] > reference['area_mape'] or candidate['qc_failure_rate'] > reference['qc_failure_rate']):
                reasons.append('Real comparison improves neither safely nor consistently')
        return reasons

    def recommend(self, version):
        record = self.get(version)
        report = json.loads((self.root/record['artifacts']['evaluation']['path']).read_text(encoding='utf8'))
        champion = self.champion()
        current = champion['version'] if champion else None
        reasons = []
        if report.get('status') != 'COMPLETED' or report.get('checkpoint_sha256') != record['artifacts']['checkpoint']['sha256']:
            reasons.append('Evaluation incomplete or not bound to registered checkpoint')
        if report.get('scope') == 'EXP006_PUBLIC_RESEARCH':
            reasons += self._public_gate(report)
        elif report.get('scope') == 'REAL_RETRAIN' or (isinstance(report.get('validation'),dict) and report['validation'].get('kind') == 'real_untouched'):
            reasons += self._real_gate(record, report, current)
        else:
            reasons.append('Unsupported or diagnostic-only evaluation scope')
        return dict(version=version, eligible=not reasons, reasons=reasons, scope=report.get('scope'),
                    report_sha256=record['artifacts']['evaluation']['sha256'], current_champion=current,
                    human_approval_required=True, target_validation=report.get('target_validation','UNVALIDATED / REVIEW REQUIRED'))

    def promote(self, version, *, approval, report_sha256, expected_current):
        """Explicit human keyword plus reviewed report digest and compare-and-set."""
        if approval != 'APPROVE_MODEL':
            raise ValueError('Explicit human APPROVE_MODEL required')
        self.root.mkdir(parents=True, exist_ok=True)
        with mutation_lock(self.root):
            decision = self.recommend(version)
            if decision['current_champion'] != expected_current:
                raise ValueError('Champion changed; review again before promotion')
            if report_sha256 != decision['report_sha256'] or not decision['eligible']:
                raise ValueError('Exact eligible completed report required for promotion: '+ '; '.join(decision['reasons']))
            if expected_current == version:
                raise ValueError('Version is already champion')
            event = dict(schema_version=1, version=version, previous_version=expected_current,
                         approval=approval, report_sha256=report_sha256, promoted_at=stamp(),
                         scope=decision['scope'], target_validation=decision['target_validation'])
            history = bounded(self.root/'promotion_history');history.mkdir(exist_ok=True)
            event_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'_'+uuid.uuid4().hex
            write_json(history/(event_id+'.json'), event)
            temporary = bounded(self.root/('champion.'+uuid.uuid4().hex+'.tmp'))
            write_json(temporary, event)
            os.replace(temporary, bounded(self.root/'champion.json'))
        return self.champion()
