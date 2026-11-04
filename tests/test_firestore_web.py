"""The unchanged web contracts exercised on a real, local Firestore emulator.

Set FIRESTORE_EMULATOR_HOST to run. No cloud credentials or cloud writes.
Every fixture gets a fresh namespace; synthetic masks never become real data.
"""
import os
import uuid
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

import tests.test_webapp as web
import tests.test_exp006_web as learned
import tests.test_production_safeguards as production
import tests.test_plant_management as plants


def setup(self):
    from src.webapp.settings import Settings
    original = Settings
    def configured(*args, **kwargs):
        kwargs.update(firestore_project='hydroponic-leaf-research-aryae',
                      firestore_prefix='test_'+uuid.uuid4().hex)
        return original(*args, **kwargs)
    with patch('src.webapp.settings.Settings', side_effect=configured):
        web.TestWebMVP.setUp(self)
    self.addCleanup(self.app.state.service.db.engine.dispose)


@unittest.skipUnless(os.getenv('FIRESTORE_EMULATOR_HOST'), 'Requires local Firestore emulator')
class TestFirestoreWeb(web.TestWebMVP):
    setUp = setup

    def test_metadata_limit_rolls_back_and_no_ephemeral_sqlite_is_created(self):
        from sqlalchemy import insert
        db=self.app.state.service.db
        with self.assertRaisesRegex(ValueError,'document size'):
            with db.engine.begin() as conn:
                conn.execute(insert(db.calibrations).values(id='oversized',payload={'text':'x'*900001}))
        with self.assertRaises(KeyError):db.one(db.calibrations,'oversized')
        self.assertFalse((self.settings.data_dir/'hydroponic.sqlite3').exists())


@unittest.skipUnless(os.getenv('FIRESTORE_EMULATOR_HOST'), 'Requires local Firestore emulator')
class TestFirestoreLearned(learned.TestExp006Web):
    setUp = setup


@unittest.skipUnless(os.getenv('FIRESTORE_EMULATOR_HOST'), 'Requires local Firestore emulator')
class TestFirestoreProduction(production.TestProductionSafeguards):
    setUp = setup


@unittest.skipUnless(os.getenv('FIRESTORE_EMULATOR_HOST'), 'Requires local Firestore emulator')
class TestFirestorePlants(plants.TestPlantManagement):
    setUp = setup

from tests.test_owner_notifications import TestOwnerIntegration

@unittest.skipUnless(os.getenv('FIRESTORE_EMULATOR_HOST'), 'Requires local Firestore emulator')
class TestFirestoreOwnerNotifications(TestOwnerIntegration):
    setUp = setup

import tests.test_species_notifications as species_notifications

@unittest.skipUnless(os.getenv('FIRESTORE_EMULATOR_HOST'), 'Requires local Firestore emulator')
class TestFirestoreSpeciesRegistry(species_notifications.TestSpeciesRegistry):
    setUp = setup


class TestFirestoreDeploymentGuards(unittest.TestCase):
    def setUp(self):
        from src.hydroponic.config import ROOT
        self.temp=tempfile.TemporaryDirectory(dir=ROOT);self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name)/'runtime'

    def test_unrelated_project_is_rejected_before_any_client_is_created(self):
        from src.webapp.app import create_app
        from src.webapp.settings import Settings
        with patch('google.cloud.firestore.Client') as client:
            with self.assertRaisesRegex(ValueError,'dedicated Hydroponic'):
                create_app(Settings(data_dir=self.data,firestore_project='unrelated-project'))
            client.assert_not_called()

    def test_production_cannot_point_at_an_emulator(self):
        from src.webapp.settings import Settings
        with patch.dict(os.environ,{'FIRESTORE_EMULATOR_HOST':'localhost:8990'}):
            with self.assertRaisesRegex(ValueError,'emulator'):
                Settings(data_dir=self.data,cloud_run=True,firestore_project='hydroponic-leaf-research-aryae',gcs_bucket='dedicated-bucket',auth_token='x'*64).validate()



import tests.test_calibration_reuse_web as calibration_reuse_web

@unittest.skipUnless(os.getenv('FIRESTORE_EMULATOR_HOST'), 'Requires local Firestore emulator')
class TestFirestoreCalibrationReuse(calibration_reuse_web.TestCalibrationReuseWeb):
    setUp = setup
