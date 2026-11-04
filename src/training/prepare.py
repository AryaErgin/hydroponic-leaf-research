"""Create-only EXP-006 native manifests, splits and train-only augmentation gallery."""
import argparse
import csv
from datetime import datetime,timezone
import json
from pathlib import Path
import zipfile
import numpy as np
from PIL import Image
from ..hydroponic.config import ROOT
from .common import bounded,relative,sha,write_json,write_manifest
from .datasets import grouped_split,near_duplicate_pairs,rasterize_yolo,target_crop,load_sample
from .augmentation import CONFIG,augment


def csv_rows(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as stream:return list(csv.DictReader(stream))


def save_mask(path,mask):
    path=bounded(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as stream:Image.fromarray(mask.astype(np.uint8)*255).save(stream,format='PNG')


def sample(id,dataset,group,image,mask,**extra):
    with Image.open(image) as source:size=list(source.size)
    return dict(id=id,dataset=dataset,source_id=dataset,group_id=group,image_path=relative(image),mask_path=relative(mask),
        image_sha256=sha(image),mask_sha256=sha(mask),native_size_px=size,species='lettuce',synthetic=False,
        target_definition='Visible annotated lettuce canopy; no hidden tissue reconstruction',**extra)


def prepare(output,seed=6006,anchor=True):
    output=bounded(output);output.mkdir(parents=True,exist_ok=False);excluded=[];rows=[]
    license_path=ROOT/'experiments/EXP-006/acquisition/existing_sources/IoT4Lettuce_zenodo_record.json'
    record=json.loads(license_path.read_text(encoding='utf-8'))
    if record['metadata']['license']['id']!='cc-by-4.0':raise ValueError('OAK source research license not verified')
    raw=ROOT/'data/raw/IoT4Lettuce/04_OAK-1'
    reviewed={r['filename']:r for r in csv_rows(ROOT/'experiments/EXP-003/selected_images.csv')}
    for metadata in csv_rows(raw/'metadata.csv'):
        name=metadata['filename'];image=raw/'images'/name;annotation=raw/'segmentation/labels/train'/(Path(name).stem+'.txt')
        id='oak1/'+Path(name).stem
        if name not in reviewed:
            polygons=len([line for line in annotation.read_text().splitlines() if line.strip()])
            reason='empty_unannotated_not_background' if not polygons else 'outside_previously_visually_screened_complete_single_target_subset'
            excluded.append(dict(id=id,dataset='oak1',image_path=relative(image),annotation_path=relative(annotation),included=False,reason=reason));continue
        review=reviewed[name]
        if sha(image)!=review['image_sha256'] or sha(annotation)!=review['label_sha256']:raise ValueError('Historical reviewed source changed')
        with Image.open(image) as source:
            mask=rasterize_yolo(annotation.read_text(),*source.size)
        if int(mask.sum())!=int(review['gt_pixel_area']):raise ValueError('Native rasterization differs from archived convention')
        mask_path=output/'masks/oak1'/(Path(name).stem+'.png');save_mask(mask_path,mask)
        rows.append(sample(id,'oak1','oak1/'+metadata['tag'],image,mask_path,license='CC-BY-4.0',license_permits_research=True,
            annotation_provenance=dict(format='YOLO polygon class 0',path=relative(annotation),sha256=sha(annotation),
                rasterization='round(normalized_coordinate*(native_dimension-1)); inclusive Pillow union',
                historical_review=review['review_note'],review_source='experiments/EXP-003/selected_images.csv',
                benchmark_exposure='Previously exposed classical development; new learned group holdout only'),
            stage=review['stage'],domain='hydroponic_public',included=True))
    duplicate_audit=near_duplicate_pairs(rows)
    rows=grouped_split(rows,seed,merge_groups=[r['groups'] for r in duplicate_audit])
    write_json(output/'near_duplicate_audit.json',dict(method='Exact hashes plus conservative 64-bit dHash <=2 and RGB thumbnail MAD <=1.5; plant tags always grouped',pairs=duplicate_audit,
        limitation='Cannot certify all cross-view identity; accepted review subset contains no clear neighbors'))
    # Held-out field source: keep full-frame partial-target diagnostics AND declared annotation-localized crops.
    raw=ROOT/'data/raw/AgRibot'
    for image in sorted(raw.glob('*/*/color_*.png')):
        if image.name.startswith('color_aligned_'):continue
        date,plot=image.parent.parent.name,image.parent.name;tags=image.stem.removeprefix('color_').split('-')
        with Image.open(image) as source:rgb=np.array(source.convert('RGB'))
        union=np.zeros(rgb.shape[:2],bool);masks=[]
        for tag in tags:
            annotation=image.parent/('plant_mask_'+tag+'.png')
            with Image.open(annotation) as source:mask=np.array(source)>0
            if mask.shape!=rgb.shape[:2] or not mask.any():raise ValueError('Invalid native AgRibot target')
            union|=mask;masks.append((tag,annotation,mask))
        name=f'{date}_{plot}_{image.stem}';mask_path=output/'masks/agribot_frame'/(name+'.png');save_mask(mask_path,union)
        rows.append(sample('agribot_frame/'+name,'agribot_frame','agribot/'+plot,image,mask_path,split='EXTERNAL_STRESS_DIAGNOSTIC',
            domain='field_partial_target_labels',license='CC0-1.0',license_permits_research=True,included=True,
            annotation_provenance=dict(format='native sampled-target binary PNG union',paths=[relative(a) for _,a,_ in masks],
                hashes=[sha(a) for _,a,_ in masks],origin='SAM2 with manual boxes; reference contour uncertainty',
                warning='Unannotated visible neighbors are background in supplied labels; not complete all-lettuce canopy truth'),
            independent_observation_id='agribot/'+name))
        for tag,annotation,mask in masks:
            cropped,target,bounds=target_crop(rgb,mask,.08);crop_name=f'{date}_{plot}_{image.stem}_target_{tag}'
            crop_image=output/'images/agribot_target_crop'/(crop_name+'.png');crop_image.parent.mkdir(parents=True,exist_ok=True)
            with crop_image.open('xb') as stream:Image.fromarray(cropped).save(stream,format='PNG')
            crop_mask=output/'masks/agribot_target_crop'/(crop_name+'.png');save_mask(crop_mask,target)
            rows.append(sample('agribot_target_crop/'+crop_name,'agribot_target_crop','agribot/'+plot,crop_image,crop_mask,
                split='EXTERNAL_TEST',domain='field_annotation_localized_target',license='CC0-1.0',license_permits_research=True,included=True,
                annotation_provenance=dict(format='native target PNG; GT-bbox localized crop with 8 percent padding',
                    source_image=relative(image),source_image_sha256=sha(image),source_mask=relative(annotation),source_mask_sha256=sha(annotation),
                    crop_bounds_xyxy=bounds,origin='SAM2 with manual boxes',warning='Oracle annotation-based localization; no localization accuracy claim; neighbors can remain inside crop'),
                independent_observation_id='agribot/'+name))
    real_image=ROOT/'data/incoming/lettuce_test.jpeg'
    ground_truths=sorted((ROOT/'results/manual_ground_truth').glob('*/lettuce_test/measurement.json'))
    if not ground_truths:raise ValueError('Human-approved real development mask missing')
    metadata_path=ground_truths[-1];meta=json.loads(metadata_path.read_text(encoding='utf-8'));mask=metadata_path.parent/'reviewed_mask.png'
    if meta['measurement_status']!='MANUAL_GROUND_TRUTH' or meta['image_sha256']!=sha(real_image) or meta['mask_sha256']!=sha(mask):
        raise ValueError('Approved development source binding changed')
    if meta['review_method']!='explicit_browser_APPROVE' or not all(meta['review'].values()):
        raise ValueError('Development annotation lacks explicit human review')
    with Image.open(mask) as approved:
        approved_pixels=np.array(approved)
        if approved.size!=(1200,1600) or not set(np.unique(approved_pixels)).issubset({0,255}) or int((approved_pixels>0).sum())!=36625:
            raise ValueError('Development reference dimensions or approved pixel area changed')
    rows.append(sample('real_development/lettuce_test','real_development','real_development/lettuce_test',real_image,mask,
        split='TRAIN_ANCHOR' if anchor else 'DEVELOPMENT_DIAGNOSTIC',domain='own_hydroponic',diagnostic=True,
        license='OWN_HUMAN_APPROVED_RESEARCH',license_permits_research=True,included=True,training_anchor=anchor,
        annotation_provenance=dict(metadata_path=relative(metadata_path),sha256=sha(metadata_path),review_method=meta['review_method'],
            warning='Exposed development image, never independent test. Use final approved mask pixels; embedded first-pass candidate notes do not define final reviewed inclusion. Final mask includes the pale upper blade.')))
    # Every local image of unavailable sources has an explicit exclusion reason.
    mot=ROOT/'data/raw/LettuceMOTS'
    for image in sorted(mot.glob('canonical/*/images/*/*.png')):
        excluded.append(dict(id='lettucemots/'+image.relative_to(mot).as_posix(),dataset='lettucemots',image_path=relative(image),
            included=False,reason='original_dataset_license_not_explicit; public availability/code GPL does not establish data permission'))
    field=ROOT/'data/raw/FieldCropWeed'
    inspection_path=field/'acquisition_metadata/inspection.json'
    if inspection_path.exists():
        # Unique source images are retained; duplicate archive/repository copies are acquisition evidence.
        for image in sorted(field.glob('huggingface_snapshot/**/*.jpg')):
            excluded.append(dict(id='fieldcropweed/'+image.relative_to(field).as_posix(),dataset='fieldcropweed',image_path=relative(image),
                included=False,reason='canonical_public_sample_has_no_segmentation_annotations; full_access_contact_only'))
    for archive,dataset,reason in [('Lettuce_growth.zip','aalto','no_segmentation_masks'),('RumexLeaves.zip','rumex','different_species_R_obtusifolius; not_lettuce')]:
        with zipfile.ZipFile(ROOT/archive) as source:
            for name in source.namelist():
                lower=name.lower()
                if dataset=='aalto' and lower.endswith('.png') and '/6_canopy cover/' in lower:
                    excluded.append(dict(id=dataset+'/'+name,dataset=dataset,archive=archive,source_entry=name,included=False,reason=reason))
                if dataset=='rumex' and lower.endswith(('.jpg','.png')) and ('/depth_' not in lower and '_depth_' not in lower):
                    excluded.append(dict(id=dataset+'/'+name,dataset=dataset,archive=archive,source_entry=name,included=False,reason=reason))
    write_manifest(output/'manifest.jsonl',rows);write_manifest(output/'exclusions.jsonl',excluded)
    write_json(output/'augmentation_config.json',CONFIG)
    gallery=output/'augmentation_gallery';gallery.mkdir()
    train=[r for r in rows if r['split'] in ('TRAIN','TRAIN_ANCHOR')]
    provenance=[]
    for i in range(48):
        row=train[i%len(train)];rgb,mask=load_sample(row);aug_seed=seed*1000+i
        image,target,info=augment(rgb,mask,aug_seed,512)
        image_path=gallery/f'{i:03d}_image.png';mask_path=gallery/f'{i:03d}_mask.png'
        with image_path.open('xb') as stream:Image.fromarray(image).save(stream,format='PNG')
        save_mask(mask_path,target)
        provenance.append(dict(source_sample=row['id'],source_image_sha256=row['image_sha256'],source_mask_sha256=row['mask_sha256'],
            image_path=relative(image_path),mask_path=relative(mask_path),image_sha256=sha(image_path),mask_sha256=sha(mask_path),**info))
    write_manifest(gallery/'provenance.jsonl',provenance)
    counts={split:sum(r['split']==split for r in rows) for split in sorted({r['split'] for r in rows})}
    groups={split:sorted({r['group_id'] for r in rows if r['split']==split}) for split in counts}
    summary=dict(created_at=datetime.now(timezone.utc).isoformat(),seed=seed,counts=counts,groups=groups,
        excluded_count=len(excluded),near_duplicate_pairs=len(duplicate_audit),training_anchor=anchor,
        native_mask_coordinate_convention='Existing round(x*(W-1)), inclusive fill',synthetic_validation_count=0,
        manifest_sha256=sha(output/'manifest.jsonl'),exclusions_sha256=sha(output/'exclusions.jsonl'),augmentation_config_sha256=sha(output/'augmentation_config.json'))
    write_json(output/'dataset_summary.json',summary)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',default='data/processed/exp006/v1')
    parser.add_argument('--seed',type=int,default=6006);parser.add_argument('--no-real-anchor',action='store_true')
    args=parser.parse_args();print(json.dumps(prepare(args.output,args.seed,not args.no_real_anchor),indent=2))


if __name__=='__main__':main()
