"""python -m src.hydroponic {single,batch} --help"""
import argparse
import json
from .config import load_profiles
from .pipeline import process_file, save_result
from .batch import run_batch


def main(argv=None):
    parser=argparse.ArgumentParser(description='Classical hydroponic projected foreground area; no target-species accuracy claim')
    modes=parser.add_subparsers(dest='mode',required=True)
    single=modes.add_parser('single');batch=modes.add_parser('batch')
    for p in (single,batch):
        p.add_argument('--output',required=True,help='New directory below project root; never overwrite')
        p.add_argument('--acquisition',help='External acquisition JSON; default frozen EXP-003/OAK-1 development profile')
    single.add_argument('--image',required=True)
    single.add_argument('--species',required=True,choices=['lettuce','rumex_acetosa'])
    single.add_argument('--species-profile');single.add_argument('--color-config')
    single.add_argument('--calibration',help='Image-bound validated planar marker JSON')
    batch.add_argument('--manifest',required=True,help='CSV with image_path,species,plant_id,captured_at')
    args=parser.parse_args(argv)
    try:
        if args.mode=='batch':summary=run_batch(args.manifest,args.output,args.acquisition)
        else:
            s,a=load_profiles(args.species,args.acquisition,args.species_profile,args.color_config)
            result=process_file(args.image,s,a,args.calibration);save_result(result,args.output)
            summary={k:result['metadata'][k] for k in ('species','qc_status','confidence','qc_reasons','raw_pixel_area','reported_pixel_area','projected_area_cm2','scale_status')}
    except (ValueError,OSError) as exc:parser.exit(2,f'Error: {exc}\n')
    print(json.dumps(summary,indent=2,allow_nan=False))


if __name__=='__main__':main()
