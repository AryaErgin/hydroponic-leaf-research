"""External species/acquisition configuration; no implicit Rumex thresholds."""
import copy
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
CONFIGS=ROOT/'configs'
COLOR_KEYS={'h_min','h_max','s_min','v_min','exg_min','morphology_radius'}
ROI_KEYS={'working_size_px','buffer_min_radius','coherence_radius','anchor_min_pixels','anchor_min_fraction',
    'anchor_center_radius','anchor_score_radius','support_seed_min_pixels','nearby_fragment_min_fraction',
    'envelope_margin','fallback_radius','local_edge_radius','boundary_erosion_radius','boundary_fraction_limit','unsupported_anchor_weight'}
BG_KEYS={'perimeter_band_fraction','h_min','h_max','s_min','normalized_exg_min','green_fraction_threshold','background_quantile','contrast_margin'}
SUPPORT_KEYS={'median_radius','closing_radius','s_max','v_max','piece_min_fraction','piece_max_fraction',
    'piece_max_span_fraction','center_radius','hull_min_fraction','hull_max_fraction','aspect_min','aspect_max','coverage_min'}
CONF_KEYS={'supported','contrast_fallback','unlocalized','boundary_cap','dominance_for_full'}


def number(value,lo,hi,name,integer=False):
    if type(value) not in (int,float) or not lo<=value<=hi or not math.isfinite(value) or integer and type(value)!=int:
        raise ValueError(f'Invalid {name}: expected finite {lo}..{hi}'+(' integer' if integer else ''))


def keys(obj,expected,label):
    if not isinstance(obj,dict) or set(obj)!=expected: raise ValueError(f'Unexpected/missing {label} keys')


def validation(value):
    keys(value,{'status','evidence_id'},'validation')
    if value['status'] not in ('unvalidated','development_only','validated'): raise ValueError('Unknown validation status')
    if value['evidence_id'] is not None and not isinstance(value['evidence_id'],str):raise ValueError('Evidence ID must be string or null')
    if value['status']!='unvalidated' and (not isinstance(value['evidence_id'],str) or not value['evidence_id'].strip()):
        raise ValueError('Validation/development evidence ID required')


def validate_profiles(species,acquisition):
    keys(species,{'schema_version','species','measurement_target','validation','color','seed_color'},'species')
    keys(acquisition,{'schema_version','acquisition_id','validation','longitudinal','roi','background','support','confidence','qc'},'acquisition')
    if type(species['schema_version']) is not int or type(acquisition['schema_version']) is not int or species['schema_version']!=1 or acquisition['schema_version']!=1: raise ValueError('Unsupported schema')
    if species['species'] not in ('lettuce','rumex_acetosa'): raise ValueError('Unsupported species')
    if species['measurement_target']!='visible_projected_plant_foreground': raise ValueError('Unsupported measurement target')
    if not isinstance(acquisition['acquisition_id'],str) or not acquisition['acquisition_id'].strip(): raise ValueError('Acquisition ID required')
    validation(species['validation']);validation(acquisition['validation'])
    color,seed=species['color'],species['seed_color']
    if (color is None)!=(seed is None): raise ValueError('Color and seed settings must both be configured')
    if color is None and species['validation']['status']!='unvalidated': raise ValueError('Configured colors required for validated/development profile')
    if color is not None:
        keys(color,COLOR_KEYS,'color');keys(seed,{'h_max','normalized_exg_min'},'seed_color')
        for k in ('h_min','h_max'):number(color[k],0,179,k)
        if color['h_min']>color['h_max']:raise ValueError('Inverted hue interval')
        for k in ('s_min','v_min'):number(color[k],0,255,k)
        number(color['exg_min'],-510,510,'exg_min');number(color['morphology_radius'],0,16,'morphology_radius',True)
        number(seed['h_max'],0,179,'seed h_max');number(seed['normalized_exg_min'],-1,2,'seed normalized ExG')
    for section,expected in [('roi',ROI_KEYS),('background',BG_KEYS),('support',SUPPORT_KEYS),('confidence',CONF_KEYS)]:
        values=acquisition[section];keys(values,expected,section)
        for k,v in values.items():
            if k in ('working_size_px','anchor_min_pixels','support_seed_min_pixels'):number(v,1,4096,k,True)
            elif k.endswith('radius') and k in ('buffer_min_radius','coherence_radius','local_edge_radius','boundary_erosion_radius','median_radius','closing_radius'):
                number(v,1,16,k,True)
            elif k in ('h_min','h_max'):number(v,0,179,k)
            elif k in ('s_min','s_max','v_max'):number(v,0,255,k)
            elif k.startswith('aspect_'):number(v,.01,100,k)
            else:number(v,0,1,k)
    r=acquisition['roi'];b=acquisition['background'];s=acquisition['support'];c=acquisition['confidence']
    for value in (r['anchor_score_radius'],r['fallback_radius'],c['dominance_for_full']):
        if value<=0:raise ValueError('Zero geometry denominator')
    if b['h_min']>b['h_max'] or b['perimeter_band_fraction']<=0 or b['green_fraction_threshold']<=0:raise ValueError('Invalid background bounds')
    if s['aspect_min']>s['aspect_max'] or s['piece_min_fraction']>s['piece_max_fraction'] or s['hull_min_fraction']>s['hull_max_fraction']:raise ValueError('Inverted support bounds')
    keys(acquisition['qc'],{'threshold','reference_mode'},'qc');number(acquisition['qc']['threshold'],0,1,'QC threshold')
    if acquisition['qc']['threshold']<=0 or acquisition['qc']['reference_mode']!='self_consistency_exp003':raise ValueError('Unsupported QC')
    longitudinal=acquisition['longitudinal'];keys(longitudinal,{'stable_geometry_validated','geometry_validation_id'},'longitudinal')
    if type(longitudinal['stable_geometry_validated']) is not bool:raise ValueError('Stable geometry requires boolean evidence flag')
    if longitudinal['geometry_validation_id'] is not None and not isinstance(longitudinal['geometry_validation_id'],str):raise ValueError('Geometry evidence ID must be string or null')
    if longitudinal['stable_geometry_validated'] and (not isinstance(longitudinal['geometry_validation_id'],str) or not longitudinal['geometry_validation_id'].strip()):
        raise ValueError('Stable geometry evidence ID required')


def load_profiles(name,acquisition_path=None,species_path=None,color_path=None):
    if name not in ('lettuce','rumex_acetosa'):raise ValueError('Unsupported species')
    species=json.loads(Path(species_path or CONFIGS/'species'/f'{name}.json').read_text(encoding='utf-8'))
    acquisition=json.loads(Path(acquisition_path or CONFIGS/'acquisition/exp003_oak1.json').read_text(encoding='utf-8'))
    if species.get('species')!=name:raise ValueError('Profile species mismatch')
    if color_path:
        override=json.loads(Path(color_path).read_text(encoding='utf-8'));keys(override,{'color','seed_color'},'explicit color override')
        species.update(override)
        # Any modified colors invalidate inherited development/validation evidence.
        species['validation']={'status':'unvalidated','evidence_id':None}
    validate_profiles(species,acquisition)
    return copy.deepcopy(species),copy.deepcopy(acquisition)
