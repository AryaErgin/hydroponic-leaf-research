"""Read-only RumexLeaves archive audit. No extraction, inference or training."""
import argparse
import collections
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET
import zipfile
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
ARCHIVE=ROOT/'RumexLeaves.zip'
DEFAULT=ROOT/'docs/dataset_inspection/rumex_inventory'


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def audit(destination):
    destination=destination.resolve()
    if not destination.is_relative_to(ROOT):raise ValueError('Outputs must remain inside project')
    if destination.exists():raise FileExistsError('Choose a new inspection destination')
    before=digest(ARCHIVE);initial_stat=ARCHIVE.stat();rows=[];xml_reports=[];split_reports={}
    dimensions=collections.defaultdict(collections.Counter);modes=collections.defaultdict(collections.Counter)
    annmap={};image_hashes=collections.defaultdict(list);sequence_rows=[]
    with zipfile.ZipFile(ARCHIVE) as z:
        names=[i.filename for i in z.infolist() if not i.is_dir()]
        assert len(names)==len(set(names)),'Duplicate archive paths'
        # Original annotations, not the duplicate copies with added OBB shapes.
        for n in names:
            if not n.endswith('/annotations.xml'):continue
            root=ET.fromstring(z.read(n));images=root.findall('image');polys=lines=0
            counterpart=ET.fromstring(z.read(n.replace('/annotations.xml','/annotations_oriented_bb.xml')))
            boxes=sum(len(im.findall('box')) for im in counterpart.findall('image'))
            for im in counterpart.findall('image'):
                for box in im.findall('box'):im.remove(box)
            assert [ET.tostring(im) for im in images]==[ET.tostring(im) for im in counterpart.findall('image')],'Unexpected OBB annotation differences'
            for im in images:
                name=im.attrib['name'];assert name not in annmap,'Nonunique annotation image name'
                pp=im.findall('polygon');ll=im.findall('polyline');assert len(pp)==len(ll)>0
                w,h=int(im.attrib['width']),int(im.attrib['height']);edge=0
                for p in pp:
                    assert p.attrib['label']=='leaf_blade'
                    xy=[tuple(map(float,v.split(','))) for v in p.attrib['points'].split(';')]
                    assert len(xy)>=3 and all(0<=x<=w and 0<=y<=h for x,y in xy),'Coordinates outside continuous canvas'
                    edge+=int(any(x>w-1 or y>h-1 for x,y in xy))
                annmap[name]=dict(annotation_xml=n,annotation_width=w,annotation_height=h,
                    leaf_polygons=len(pp),stem_vein_polylines=len(ll),canvas_edge_polygons=edge)
                polys+=len(pp);lines+=len(ll)
            assert boxes==polys
            xml_reports.append(dict(path=n,images=len(images),leaf_polygons=polys,stem_vein_polylines=lines,
                derived_OBB_boxes=boxes,OBB_adds_boxes_only=True,tracks=len(root.findall('track'))))
        for source in ['iNaturalist','RoboRumex']:
            prefix=f'RumexLeaves/{source}/';splits={}
            for split in ['train','val','test']:
                values=[line.strip() for line in z.read(prefix+'dataset_splits/random_'+split+'.txt').decode().splitlines() if line.strip()]
                assert len(values)==len(set(values))
                splits[split]={prefix+v for v in values};assert splits[split]<=set(names)
            assert all(not (splits[a]&splits[b]) for a,b in [('train','val'),('train','test'),('val','test')])
            rgb={n for n in names if n.startswith(prefix) and (n.endswith('.jpg') or n.endswith('.png') and '_rgb_' in n)}
            assert set.union(*splits.values())==rgb,'Splits do not cover exactly RGB images'
            split_reports[source]=dict(counts={k:len(v) for k,v in splits.items()},filename_overlap=0,all_RGB_covered=True)
            if source=='RoboRumex':
                for folder in sorted({str(PurePosixPath(n).parent.parent) for n in rgb}):
                    nn=sorted(n for n in rgb if n.startswith(folder+'/imgs/'))
                    stamps=sorted(int(PurePosixPath(n).stem.rsplit('_',1)[-1]) for n in nn)
                    counts={s:sum(n in splits[s] for n in nn) for s in splits}
                    sequence_rows.append(dict(sequence=folder,RGB=len(nn),**counts,
                        minimum_adjacent_seconds=min(b-a for a,b in zip(stamps,stamps[1:]))/1e9))
            for n in sorted(rgb):
                raw=z.read(n);im=Image.open(io.BytesIO(raw));im.load()
                key=source+'_RGB';dimensions[key][f'{im.width}x{im.height}']+=1;modes[key][im.mode]+=1
                ann=annmap[PurePosixPath(n).name]
                assert (im.width,im.height)==(ann['annotation_width'],ann['annotation_height'])
                image_hash=hashlib.sha256(raw).hexdigest();image_hashes[image_hash].append(n)
                split=next(s for s in splits if n in splits[s])
                rows.append(dict(archive_entry=n,subset=source,split=split,width=im.width,height=im.height,
                    image_mode=im.mode,image_sha256=image_hash,**ann))
        depths=[n for n in names if n.endswith('.png') and '_depth_' in n]
        for n in depths:
            with z.open(n) as f:
                im=Image.open(f);dimensions['RoboRumex_depth'][f'{im.width}x{im.height}']+=1;modes['RoboRumex_depth'][im.mode]+=1
        rgb_keys={PurePosixPath(r['archive_entry']).stem.replace('_rgb_','_') for r in rows if r['subset']=='RoboRumex'}
        depth_keys={PurePosixPath(n).stem.replace('_depth_','_') for n in depths}
        ref=list(csv.DictReader(io.StringIO(z.read('RumexLeaves/iNaturalist/references.txt').decode('utf-8-sig')),delimiter=';'))
        assert len(ref)==690 and len({r['File Name'].strip() for r in ref})==690
        references=dict(rows=len(ref),unique_photo_urls=len({r[' Link to image'].strip() for r in ref}),
            unique_creators=len({r[' Name of Creator'].strip() for r in ref}),
            fields=list(ref[0]),has_observation_or_physical_plant_ID=False)
        report=dict(archive=ARCHIVE.name,archive_bytes=initial_stat.st_size,archive_sha256=before,
            file_count=len(names),file_extensions=dict(collections.Counter(PurePosixPath(n).suffix for n in names)),
            folder_counts=dict(collections.Counter(str(PurePosixPath(n).parent) for n in names)),
            RGB_images=len(rows),depth_images=len(depths),RGB_depth_timestamp_matches=len(rgb_keys&depth_keys),
            depth_without_RGB=len(depth_keys-rgb_keys),RGB_without_depth=len(rgb_keys-depth_keys),
            leaf_instances=sum(r['leaf_polygons'] for r in rows),
            stem_vein_polylines=sum(r['stem_vein_polylines'] for r in rows),
            image_annotation_resolution_mismatches=0,empty_annotated_images=0,
            byte_identical_RGB_duplicate_groups=[v for v in image_hashes.values() if len(v)>1],
            resolutions={k:dict(v) for k,v in dimensions.items()},image_modes={k:dict(v) for k,v in modes.items()},
            XML_files=xml_reports,splits=split_reports,sequences=sequence_rows,iNaturalist_references=references,
            polygons_at_continuous_canvas_last_edge=sum(r['canvas_edge_polygons'] for r in rows),
            images_with_continuous_canvas_last_edge=sum(r['canvas_edge_polygons']>0 for r in rows),
            species_field_present_in_XML=False,physical_plant_IDs_present=False,
            scope='Archive audit and label validation only; no model or segmentation prediction executed')
    after=digest(ARCHIVE);assert before==after and ARCHIVE.stat().st_mtime_ns==initial_stat.st_mtime_ns,'Raw archive changed'
    report['archive_unchanged']=True
    destination.mkdir(parents=True)
    with (destination/'inventory.csv').open('x',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    with (destination/'audit.json').open('x',encoding='utf-8') as f:json.dump(report,f,indent=2)
    print(f"PASS read-only audit: {len(rows)} RGB images, {len(depths)} depth PNGs, {report['leaf_instances']} leaves; all dimensions/splits/duplicate OBB annotations checked; archive unchanged")


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=DEFAULT);args=ap.parse_args();audit(args.output)
