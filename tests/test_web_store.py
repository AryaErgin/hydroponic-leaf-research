"""Storage contract tests; fake GCS transport is not a live cloud integration test."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from src.hydroponic.config import ROOT
from src.webapp.settings import Settings
from src.webapp.store import MediaStore


class TestMediaStore(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT);self.addCleanup(self.temp.cleanup)
        self.settings=Settings(data_dir=Path(self.temp.name)/'runtime').validate()

    def test_local_media_is_create_only_and_hash_checked(self):
        store=MediaStore(self.settings);record=store.put('sample/image.png',b'original','image/png')
        with self.assertRaises(FileExistsError):store.put(record['key'],b'replacement')
        (store.root/record['key']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):store.read(record)
        for key in ('../escape','/absolute'):
            with self.assertRaises(ValueError):store.put(key,b'data')

    def test_gcs_uses_create_only_precondition_and_checks_download_hash(self):
        self.settings.gcs_bucket='synthetic-private-bucket'
        with patch('google.cloud.storage.Client') as client:
            blob=client.return_value.bucket.return_value.blob.return_value
            store=MediaStore(self.settings);record=store.put('sample/image.png',b'original','image/png')
            blob.upload_from_string.assert_called_once_with(b'original',content_type='image/png',if_generation_match=0,timeout=60)
            self.assertEqual(list(store.root.rglob('*')),[])
            blob.download_as_bytes.return_value=b'original';self.assertEqual(store.read(record),b'original')
            blob.download_as_bytes.return_value=b'changed'
            with self.assertRaisesRegex(ValueError,'hash mismatch'):store.read(record)


if __name__=='__main__':unittest.main()
