import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from src.local_setup import model_assets as assets


class ModelBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source'
        self.target = self.root/'target'
        self.target.mkdir()
        self.bundle = self.root/'bundle.zip'
        self.version = 'exp006-segformer-001'
        base = 'models/lettuce/versions/'+self.version+'/'
        def encoded(value):
            return json.dumps(value).encode()
        config = dict(architecture='segformer_b0', input_size=512, threshold=.5,
                      normalization=dict(mean=[.485,.456,.406],std=[.229,.224,.225]))
        checkpoint = b'registered fixture bytes; never used as a predictor'
        digest = lambda value: hashlib.sha256(value).hexdigest()
        evaluation = encoded(dict(checkpoint_sha256=digest(checkpoint),status='COMPLETED'))
        provenance = encoded(dict(registered_version=self.version,actual_completed_training=dict(checkpoint_sha256=digest(checkpoint))))
        records = dict(checkpoint=('model.safetensors',checkpoint),config=('config.json',encoded(config)),
                       evaluation=('evaluation.json',evaluation),provenance=('provenance.json',provenance),
                       train_manifest=('train_manifest.jsonl',b'{"dataset":"fixture"}\n'))
        registry = dict(schema_version=1,version=self.version,species='lettuce',architecture='segformer_b0',
                        artifacts={k:dict(path='versions/'+self.version+'/'+name,sha256=digest(data),bytes=len(data)) for k,(name,data) in records.items()})
        self.contents = {base+name:data for name,data in records.values()}
        self.contents[base+'registry.json'] = encoded(registry)
        self.contents['models/lettuce/champion.json'] = encoded(dict(schema_version=1,version=self.version,report_sha256=digest(evaluation)))
        self.contents['models/lettuce/notices/SegFormer_LICENSE'] = b'Complete fixture license'
        self.policy = dict(schema_version=1,version=self.version,architecture='segformer_b0',species='lettuce',
                           checkpoint_sha256=digest(checkpoint),files={p:dict(bytes=len(data),sha256=digest(data)) for p,data in self.contents.items()})
        for path,data in self.contents.items():
            destination=self.source/path
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(data)
        self.policy_patch = patch.object(assets,'_load_policy',return_value=self.policy)
        self.policy_patch.start()
        self.addCleanup(self.policy_patch.stop)

    def export(self):
        assets.export_bundle(self.source,self.bundle)

    def test_roundtrip_keeps_registry_exact_and_install_is_idempotent(self):
        self.export()
        result=assets.install_bundle(self.bundle,self.target)
        self.assertEqual(result['checkpoint_sha256'],self.policy['checkpoint_sha256'])
        for path,data in self.contents.items():
            self.assertEqual((self.target/path).read_bytes(),data)
        self.assertEqual(assets.install_bundle(self.bundle,self.target)['status'],'ALREADY_INSTALLED')
        self.assertEqual(assets.verify_installed(self.target)['status'],'VERIFIED')

    def test_export_rejects_changed_checkpoint_and_does_not_create_archive(self):
        next(self.source.rglob('model.safetensors')).write_bytes(b'changed')
        with self.assertRaises(ValueError): self.export()
        self.assertFalse(self.bundle.exists())

    def test_install_preserves_conflicting_existing_models(self):
        self.export()
        marker=self.target/'models/lettuce/owner-data'
        marker.parent.mkdir(parents=True)
        marker.write_bytes(b'keep')
        with self.assertRaises(ValueError): assets.install_bundle(self.bundle,self.target)
        self.assertEqual(marker.read_bytes(),b'keep')
        self.assertFalse((marker.parent/'champion.json').exists())

    def test_hostile_zip_paths_duplicate_links_and_unknown_files(self):
        self.export()
        with zipfile.ZipFile(self.bundle) as stream:
            contents={i.filename:stream.read(i) for i in stream.infolist()}
        for name,symlink in [('../outside',False),('C:/outside',False),('models\\lettuce\\outside',False),('unlisted',False),('models/lettuce/champion.json',True)]:
            with self.subTest(name=name,symlink=symlink):
                bad=self.root/'bad.zip'
                with zipfile.ZipFile(bad,'w') as stream:
                    for key,value in contents.items(): stream.writestr(key,value)
                    info=zipfile.ZipInfo(name)
                    if symlink:
                        info.create_system=3
                        info.external_attr=0o120777<<16
                    stream.writestr(info,b'bad')
                with self.assertRaises(ValueError): assets.install_bundle(bad,self.target)
                self.assertFalse((self.target/'models/lettuce').exists())

    def test_tampered_archive_digest_and_missing_notice_rejected(self):
        self.export()
        with zipfile.ZipFile(self.bundle) as stream:
            contents={i.filename:stream.read(i) for i in stream.infolist()}
        for operation in ('tamper','missing'):
            bad=self.root/'bad.zip'
            changed=copy.deepcopy(contents)
            if operation=='tamper': changed[next(p for p in changed if p.endswith('model.safetensors'))]=b'changed'
            else: changed.pop('models/lettuce/notices/SegFormer_LICENSE')
            with zipfile.ZipFile(bad,'w') as stream:
                for path,data in changed.items(): stream.writestr(path,data)
            with self.assertRaises(ValueError): assets.install_bundle(bad,self.target)

    def test_registry_config_architecture_disagreement_rejected_even_with_matching_hashes(self):
        path=next(p for p in self.contents if p.endswith('config.json'))
        data=json.loads(self.contents[path]);data['architecture']='unet_resnet18'
        data=json.dumps(data).encode()
        (self.source/path).write_bytes(data)
        self.policy['files'][path]=dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        with self.assertRaises(ValueError): self.export()

    def test_symlink_source_and_installed_extra_files_rejected(self):
        self.export()
        assets.install_bundle(self.bundle,self.target)
        (self.target/'models/lettuce/unexpected').write_bytes(b'new')
        with self.assertRaises(ValueError): assets.verify_installed(self.target)

    def test_failed_commit_rolls_back_and_removes_staging(self):
        self.export()
        with patch.object(assets.os,'rename',side_effect=OSError('simulated commit failure')):
            with self.assertRaises(OSError): assets.install_bundle(self.bundle,self.target)
        self.assertFalse((self.target/'models/lettuce').exists())
        self.assertEqual(list((self.target/'models').iterdir()),[])

    def test_forged_manifest_cannot_replace_checked_in_trust_anchor(self):
        self.export()
        with zipfile.ZipFile(self.bundle) as stream:
            contents={i.filename:stream.read(i) for i in stream.infolist()}
        manifest=json.loads(contents['manifest.json'])
        manifest['files']['models/lettuce/champion.json']['sha256']='0'*64
        contents['manifest.json']=json.dumps(manifest).encode()
        with zipfile.ZipFile(self.root/'forged.zip','w') as stream:
            for name,data in contents.items(): stream.writestr(name,data)
        with self.assertRaises(ValueError): assets.install_bundle(self.root/'forged.zip',self.target)

    def test_high_compression_ratio_is_rejected_before_extraction(self):
        self.export()
        with zipfile.ZipFile(self.bundle) as stream:
            contents={i.filename:stream.read(i) for i in stream.infolist()}
        path='models/lettuce/versions/'+self.version+'/train_manifest.jsonl'
        contents[path]=b'0'*30000
        self.policy['files'][path]['bytes']=30000
        self.policy['files'][path]['sha256']=hashlib.sha256(contents[path]).hexdigest()
        contents['manifest.json']=json.dumps(dict(schema_version=1,version=self.version,files=self.policy['files'])).encode()
        with zipfile.ZipFile(self.root/'bomb.zip','w',compression=zipfile.ZIP_DEFLATED) as stream:
            for name,data in contents.items(): stream.writestr(name,data)
        with self.assertRaisesRegex(ValueError,'compression'):
            assets.install_bundle(self.root/'bomb.zip',self.target)

    def test_symlink_export_is_rejected(self):
        target=next(self.source.rglob('model.safetensors'))
        original=self.root/'original.safetensors'
        target.rename(original)
        try:
            target.symlink_to(original)
        except OSError:
            self.skipTest('Creating symlinks is unavailable for this account')
        with self.assertRaisesRegex(ValueError,'Symlink'):
            self.export()

    def test_existing_bundle_is_never_overwritten(self):
        self.export()
        before=self.bundle.read_bytes()
        with self.assertRaises(FileExistsError): self.export()
        self.assertEqual(self.bundle.read_bytes(),before)


if __name__=='__main__': unittest.main()
