"""Inspect local unused-source plant identity before any confirmation score."""
import argparse
import csv
import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from PIL import Image,ImageDraw
from .exp001 import ROOT,OUT as BASE,read_csv,write_csv,write_json,sha

OUT=ROOT/'experiments/EXP-003/source_audit'
TAG=re.compile(r'(?<![A-Z0-9])((?:50|75)[A-Z]{2}[1-5])(?![A-Z0-9])')


def canonical_capture(filename):
    return re.sub(r'^oakd?_rgb_','rgb_',Path(filename).stem)


def match_capture(filename,entries):
    matches=[n for n in entries if canonical_capture(n)==canonical_capture(filename)]
    if len(matches)>1:raise ValueError('Ambiguous normalized capture: '+filename)
    return matches[0] if matches else None


def row_ids(row):
    value=row.get('tag') or row.get('sample')
    if value:return {value.strip()}
    # SPECIM filenames encode physical sample tags; OAK-D timestamps do not.
    return set(TAG.findall(row.get('folder','')))


def identity_overlap(rows,exposed_ids):
    ids=set();unknown=0
    for row in rows:
        found=row_ids(row);ids.update(found)
        if not found:unknown+=1
    overlaps=sorted(ids & set(exposed_ids))
    return dict(rows=len(rows),known_ids=sorted(ids),known_id_count=len(ids),unknown_identity_rows=unknown,
        overlap_ids=overlaps,known_overlap_count=len(overlaps),overlap_count=None if unknown else len(overlaps),
        unused_ids=sorted(ids-set(exposed_ids)),identity_complete=unknown==0)


def inspect(destination=OUT):
    if (destination/'source_overlap.json').exists():raise FileExistsError('Existing source audit')
    destination.mkdir(parents=True,exist_ok=True)
    selected=read_csv(BASE/'selected_images.csv');reviewed=read_csv(BASE/'review_decisions.csv')
    inventory=read_csv(BASE/'inventory.csv');exposed={r['tag'] for r in inventory if r['filename'] in {x['filename'] for x in reviewed}}
    evidence=[];reports={};entries=[]
    archive=ROOT/'IoT4Lettuce.zip'
    with zipfile.ZipFile(archive) as z:
        infos=z.infolist();names={i.filename:i for i in infos}
        central=[dict(name=i.filename,crc=i.CRC,bytes=i.file_size,compressed_bytes=i.compress_size,offset=i.header_offset) for i in infos]
        central_hash=hashlib.sha256(json.dumps(central,separators=(',',':')).encode()).hexdigest()
        for module in ['04_OAK-1','02_OAK-D_S2','05_Flexx_3D_camera','01_Livox_Mid-70_LiDAR','10_SPECIM_FX17','13_Manual_measurements']:
            ending='full_db.csv' if module=='13_Manual_measurements' else 'metadata.csv'
            name=f'IoT4Lettuce/{module}/{ending}'
            if name not in names:continue
            raw=z.read(name);rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
            report=identity_overlap(rows,exposed);report['columns']=list(rows[0]);reports[module]=report
            with (destination/(module+'_metadata.csv')).open('xb') as f:f.write(raw)
            evidence.append(dict(archive_entry=name,crc=names[name].CRC,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw)))
            rn=f'IoT4Lettuce/{module}/readme.md'
            if rn in names:
                raw=z.read(rn)
                with (destination/(module+'_readme.md')).open('xb') as f:f.write(raw)
                evidence.append(dict(archive_entry=rn,crc=names[rn].CRC,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw)))
        oakd=reports['02_OAK-D_S2']
        rows=list(csv.DictReader(io.StringIO(z.read('IoT4Lettuce/02_OAK-D_S2/metadata.csv').decode('utf-8-sig'))))
        sidecars=[i for i in infos if i.filename.startswith('IoT4Lettuce/02_OAK-D_S2/metadata/') and i.filename.endswith('.txt')]
        side_ids=set();side_hashes=[]
        for entry in sidecars:
            raw=z.read(entry);side_ids.update(TAG.findall(raw.decode('utf-8-sig')))
            side_hashes.append(dict(archive_entry=entry.filename,crc=entry.CRC,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw)))
        oakd['sidecar_files_inspected']=len(sidecars);oakd['sidecar_tag_ids_found']=sorted(side_ids)
        oakd['sidecar_known_overlap_ids']=sorted(side_ids & exposed)
        write_csv(destination/'OAKD_sidecar_hashes.csv',side_hashes)
        label_infos=[i for i in infos if i.filename.startswith('IoT4Lettuce/02_OAK-D_S2/segmentation/labels/') and i.filename.endswith('.txt')]
        label_map={}
        for entry in label_infos:label_map.setdefault(canonical_capture(entry.filename),[]).append(entry)
        pairings=[]
        image_names=[n for n in names if n.startswith('IoT4Lettuce/02_OAK-D_S2/images/') and n.endswith('.png')]
        for row in rows:
            name=match_capture(row['filename'],image_names)
            matches=label_map.get(canonical_capture(row['filename']),[])
            entry=matches[0] if len(matches)==1 else None
            raw=z.read(entry) if entry else b''
            polygons=[line for line in raw.decode('utf-8-sig').splitlines() if line.strip()]
            pairings.append(dict(filename=row['filename'],image_entry=name or '',image_exists=name is not None,
                label_entry=entry.filename if entry else '',label_match_count=len(matches),
                label_crc=entry.CRC if entry else '',polygons=len(polygons) if entry else '',
                physical_plant_ids='UNKNOWN',confirmation_eligible=False))
        write_csv(destination/'OAKD_pairs.csv',pairings)
        oakd.update(images=sum(n.startswith('IoT4Lettuce/02_OAK-D_S2/images/') and n.endswith('.png') for n in names),
            label_files=len(label_infos),unique_paired_rows=sum(r['image_exists'] and r['label_match_count']==1 for r in pairings),
            empty_labels=sum(r['polygons']==0 for r in pairings),polygon_counts=dict(sorted(Counter(r['polygons'] for r in pairings).items())))
        previews=Image.new('RGB',(600,780),'white');draw=ImageDraw.Draw(previews)
        for pos,index in enumerate([0,len(rows)//2,len(rows)-1]):
            pair=pairings[index];raw=z.read(pair['image_entry']);name=Path(pair['filename']).name
            with (destination/name).open('xb') as f:f.write(raw)
            evidence.append(dict(archive_entry=pair['image_entry'],crc=names[pair['image_entry']].CRC,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw)))
            im=Image.open(io.BytesIO(raw)).convert('RGB');im.thumbnail((600,235))
            previews.paste(im,(0,pos*260));draw.text((4,pos*260+239),name+'; plant identities UNKNOWN',fill='black')
        with (destination/'OAKD_layout_preview.jpg').open('xb') as f:previews.save(f,format='JPEG',quality=90)
        write_csv(destination/'archive_evidence.csv',evidence)
    with zipfile.ZipFile(ROOT/'RumexLeaves.zip') as z:
        rumex_names=z.namelist()
        reports['RumexLeaves']=dict(plant_id_overlap='not_applicable_to_lettuce',species='Rumex',confirmation_eligible=False,
            reason='Different taxon; cannot confirm lettuce segmentation performance.',archive_files=sum(not n.endswith('/') for n in rumex_names))
    reasons=dict(
        OAKD='441 timestamped RGB scenes have no tag column; all 441 metadata sidecars inspected. Different camera/time does not prove different plants. Without an instance-to-physical-ID mapping, overlap is UNKNOWN, not zero; not scored as confirmation.',
        OAK1='All 45 physical tags were exposed during prior annotation screening, including the two tags absent from the selected 43-tag manifests; remaining dates are not independent plants.',
        Flexx='All 45 known tags overlap exposed OAK-1 tags. Images are YlGn point-cloud projections, not native RGB.',
        Livox='No plant identity field; depth/intensity modalities, not the required RGB/lettuce-mask confirmation.',
        SPECIM='Known sample IDs overlap prior cohort; NIR hyperspectral cubes, not native RGB/YOLO-mask confirmation.',
        Manual='Biometrics share existing plant IDs and are not pixel masks.',
        Rumex='Different taxon, not an independent lettuce confirmation source.')
    result=dict(status='NO_ADEQUATE_INDEPENDENT_HOLDOUT',confirmation_images_scored=0,development_images=100,
        tuning_tags=sorted({r['tag'] for r in selected if r['split']=='tuning'}),
        development_tags=sorted({r['tag'] for r in selected if r['split']=='evaluation'}),
        previously_screened_exposed_tags=sorted(exposed),sources=reports,reasons=reasons,
        archive_bytes=archive.stat().st_size,archive_mtime_ns=archive.stat().st_mtime_ns,central_directory_sha256=central_hash,
        note='Archive central directory and read-entry hashes/CRC recorded; full 39GB archive SHA256 not claimed.')
    write_json(destination/'source_overlap.json',result)
    print('Source audit: no verifiable independent lettuce holdout; OAK-D physical-ID overlap UNKNOWN; Flexx overlap45/45')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=OUT);args=ap.parse_args();d=args.output.resolve()
    if not d.is_relative_to(ROOT.resolve()):raise ValueError('Outputs must stay inside project root')
    inspect(d)


if __name__=='__main__':main()
