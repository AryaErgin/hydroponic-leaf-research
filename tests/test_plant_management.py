"""Independent synthetic plants and the existing reviewed two-point validator."""
import base64,copy,hashlib,json,unittest
import shutil,subprocess
from unittest.mock import patch
from tests import test_webapp as fixtures

class TestPlantManagement(unittest.TestCase):
    setUp=fixtures.TestWebMVP.setUp
    profile=fixtures.TestWebMVP.profile
    upload=fixtures.TestWebMVP.upload
    analyze=fixtures.TestWebMVP.analyze
    approve=fixtures.TestWebMVP.approve
    def pot(self,diameter=5.82,points=None,reviewed=True):
        return self.client.post('/api/calibrations/two-point',data=dict(diameter_cm=str(diameter),points=json.dumps(points or [[10,50],[110,50]]),reviewed=str(reviewed).lower()),files={'image':('synthetic.png',self.image,'image/png')})
    def seed_default(self):
        r=self.pot();self.assertEqual(r.status_code,201,r.text);c=r.json()
        fix=patch('src.webapp.service.DEFAULT_SETUP_CALIBRATION_ID',c['id']);fix.start();self.addCleanup(fix.stop)
        return c
    def plant(self,name='Lettuce-01',species='lettuce',**updates):
        body=dict(name=name,species=species,calibration_choice='default');body.update(updates)
        r=self.client.post('/api/plants',json=body);self.assertEqual(r.status_code,201,r.text);return r.json()
    def test_add_first_plant_reuses_default_and_persists(self):
        c=self.seed_default();p=self.plant()
        self.assertEqual(p['display_name'],'Lettuce-01');self.assertEqual(p['calibration']['id'],c['id'])
        self.assertEqual(self.client.get('/api/profiles').json()[0]['display_name'],'Lettuce-01')
    def test_multiple_plants_have_independent_identity_species_and_history(self):
        self.seed_default();a=self.plant();b=self.plant('Sorrel-01','rumex_acetosa')
        self.assertNotEqual(a['id'],b['id']);self.assertNotEqual(a['plant_id'],b['plant_id'])
        self.assertEqual(self.client.get('/api/profiles').json()[1]['species'],'rumex_acetosa')
        for p in [a,b]:self.assertEqual(self.client.get('/api/profiles/'+p['id']+'/history').json()['measurements'],[])
    def test_custom_override_does_not_replace_default_or_other_plant(self):
        c=self.seed_default();a=self.plant();custom=self.pot(7.5).json()
        b=self.plant('Lettuce-02',calibration_choice='custom',calibration_id=custom['id'])
        self.assertEqual(a['cm_per_pixel'],.0582);self.assertEqual(b['cm_per_pixel'],.075)
        self.assertEqual(self.client.get('/api/setup').json()['default_calibration']['id'],c['id'])
        self.assertEqual(self.plant('Lettuce-03')['calibration']['id'],c['id'])
    def test_recalibration_override_changes_only_selected_plant(self):
        c=self.seed_default();a=self.plant();b=self.plant('Lettuce-02');custom=self.pot(7.5).json()
        r=self.client.put('/api/plants/'+b['id']+'/calibration',json={'calibration_id':custom['id']})
        self.assertEqual(r.status_code,200,r.text)
        profiles={p['id']:p for p in self.client.get('/api/profiles').json()}
        self.assertEqual(profiles[a['id']]['calibration']['id'],c['id']);self.assertEqual(profiles[b['id']]['cm_per_pixel'],.075)
    def test_two_point_native_geometry_and_provenance(self):
        r=self.pot(5.82,[[10,20],[70,100-1]])
        self.assertEqual(r.status_code,201,r.text);c=r.json()
        distance=((70-10)**2+(99-20)**2)**.5
        self.assertAlmostEqual(c['pot_diameter_pixels'],distance);self.assertAlmostEqual(c['cm_per_pixel'],5.82/distance)
        self.assertEqual(c['reference_size_px'],[120,100]);self.assertTrue(c['approximate'])
        self.assertEqual(c['provenance']['reference_image_sha256'],hashlib.sha256(self.image).hexdigest())
        self.assertEqual(c['provenance']['validated_calibration']['status'],'ACCEPTED_MANUAL_REVIEW')
    def test_bad_points_and_missing_review_cannot_create_scale(self):
        for points in [[[10,10]],[[10,10],[10,10]],[[-1,0],[20,20]],[[0,0],[120,100]]]:
            r=self.pot(points=points);self.assertEqual(r.status_code,400,r.text)
        self.assertEqual(self.pot(reviewed=False).status_code,400)
        self.assertEqual(self.pot(diameter=-1).status_code,400)
    def test_rename_and_species_persist_without_rewriting_historical_measurement(self):
        self.seed_default();p=self.plant();a=self.analyze(p);r=self.approve(a)
        self.assertEqual(r.status_code,201,r.text);before=self.client.get('/api/profiles/'+p['id']+'/history').json()
        edit=self.client.put('/api/plants/'+p['id'],json={'name':'Lettuce-A','species':'rumex_acetosa'})
        self.assertEqual(edit.status_code,200,edit.text);self.assertEqual(edit.json()['display_name'],'Lettuce-A')
        self.assertEqual(edit.json()['species'],'rumex_acetosa');self.assertEqual(edit.json()['plant_id'],p['plant_id'])
        self.assertEqual(self.client.get('/api/profiles/'+p['id']+'/history').json(),before)
    def test_independent_growth_masks_csv_and_registry(self):
        self.seed_default();a=self.plant();custom=self.pot(7.5).json();b=self.plant('Lettuce-02',calibration_choice='custom',calibration_id=custom['id'])
        ids=[]
        for p,areas in [(a,[100,200]),(b,[300,350])]:
            for day,area in zip(['2026-10-08','2026-10-09'],areas):
                r=self.approve(self.analyze(p,day),area=area);self.assertEqual(r.status_code,201,r.text);ids.append(r.json()['id'])
            h=self.client.get('/api/profiles/'+p['id']+'/history').json()
            self.assertEqual([r['pixel_area']for r in h['measurements']],areas)
            self.assertAlmostEqual(h['growth']['daily_growth_cm2'],(areas[1]-areas[0])*p['cm_per_pixel']**2)
            self.assertTrue(all(m['plant_id']==p['plant_id']for m in h['measurements']))
            csv=self.client.get('/api/profiles/'+p['id']+'/export.csv').text
            self.assertNotIn((b if p==a else a)['plant_id'],csv)
            for m in h['measurements']:
                registry=self.app.state.service.db.one(self.app.state.service.db.approved_masks,m['id'])
                self.assertEqual(registry['profile_id'],p['id'])
        self.assertEqual(len(set(ids)),4)
    def test_missing_default_allows_pixels_only_without_fabricating_scale(self):
        p=self.plant();self.assertIsNone(p['calibration'])
        a=self.analyze(p);r=self.approve(a);self.assertEqual(r.status_code,201,r.text)
        self.assertIsNone(r.json()['projected_area_cm2'])
    def test_professor_setup_has_no_json_manual_scale_or_internal_experiment_fields(self):
        html=self.client.get('/').text
        for field in ['calibration-json','reference-image','reference-width','reference-height','id="scale"','id="experiment-id"']:
            self.assertNotIn(field,html)
        self.assertIn('+ Add plant',html);self.assertIn('Recalibrate this plant/pot',html)

    @unittest.skipUnless(shutil.which('node'),'Requires Node for frontend coordinate test')
    def test_browser_picker_preserves_native_coordinates_and_exact_two_points(self):
        r=subprocess.run(['node','tests/pot_picker_checks.cjs'],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
