"""Create EXP-003 interpretation from frozen numerical artifacts (no inference)."""
import json
from .exp001 import read_csv
from .exp003 import OUT


def table(header,rows):
    return '\n'.join(['| '+' | '.join(header)+' |','| '+' | '.join(['---']*len(header))+' |']+
                     ['| '+' | '.join(map(str,row))+' |' for row in rows])


def fmt(row,key,digits=2):
    return f'{float(row[key]):.{digits}f}'


def build():
    summaries=read_csv(OUT/'global_metrics.csv');plants=read_csv(OUT/'plant_metrics.csv')
    stages=read_csv(OUT/'stage_metrics.csv');qc=json.loads((OUT/'QC_failure_detection.json').read_text())
    dev={r['method']:r for r in summaries if r['split']=='development'}
    tune={r['method']:r for r in summaries if r['split']=='tuning'}
    metrics=read_csv(OUT/'metrics.csv')
    plantrows=[]
    for tag in sorted({r['tag'] for r in plants if r['split']=='development'}):
        pp={r['method']:r for r in plants if r['split']=='development' and r['tag']==tag}
        plantrows.append([tag,pp['A']['images']]+[fmt(pp[m],'mean_iou',4) for m in 'ABC']+
                         [fmt(pp[m],'mean_area_error')+'%' for m in 'ABC'])
    failure=read_csv(OUT/'known_failure_investigation.csv');failure_rows=[]
    for name in sorted({r['filename'] for r in failure}):
        mm={r['method']:r for r in failure if r['filename']==name}
        failure_rows.append([name.removeprefix('oak_rgb_').removesuffix('.png')]+[
            f"{float(mm[m]['signed_percentage_area_error']):+.2f}%" for m in 'ABC']+
            [fmt(mm['C'],'iou',3),fmt(mm['C'],'gt_outside_roi_percent')+'%'])
    qrows=[]
    for split in ['development','tuning']:
        for m in ['B','C']:
            for truth,label in [('area_failure','Catastrophic area'),('ROI_clipping','GT outside ROI >5%')]:
                s=qc[split][m][truth]
                qrows.append([split,m,label,'/'.join(str(s[k]) for k in ['true_positive','false_positive','false_negative','true_negative']),
                              'N/A' if s['precision'] is None else f"{s['precision']*100:.2f}%",
                              'N/A' if s['recall'] is None else f"{s['recall']*100:.2f}%",
                              f"{s['coverage']*100:.2f}%"])
    headers=['Method','Mean IoU','Mean Dice','Mean area error','Median','P90','P95','Mean absolute pixel error','Catastrophic over / under']
    def summary_rows(ss):
        return [[m,fmt(ss[m],'mean_iou',4),fmt(ss[m],'mean_dice',4)]+[
            fmt(ss[m],k)+'%' for k in ['mean_area_error','median_area_error','p90_area_error','p95_area_error']]+
            [fmt(ss[m],'mean_absolute_pixel_area_error',1)+' px',ss[m]['catastrophic_false_positives']+' / '+ss[m]['catastrophic_underestimation']] for m in 'ABC']
    report='''# EXP-003: robust lettuce ROI and canopy overhang

Completed by Codex, 2026-10-03. **C passes all nine predeclared DEVELOPMENT criteria. Independent confirmation is unavailable.** It reduces catastrophic background contamination and resolves the six severe EXP-002 underestimates on the exposed 100-image benchmark. Classical CV is worth continuing, but raw clipping is not universally eliminated and QC is not a validated safety guarantee.

## Benchmark and inference boundaries

The **100 images / 34 plant tags are a development benchmark, NOT an unbiased final test set**. Their failures have informed method development. The separate **27 tuning images / nine plant tags** select parameters; no same-plant date crosses these groups. All 127 inherited images are retained. The development strata remain 47 early / 42 mid / 11 late, defined by within-tag capture-time thirds, not independently measured biological stages.

OAK-1 has 1522 image/label pairs, including 248 empty labels that are unannotated rather than clean negatives. Neighboring-pot, debris and questionable annotations remain excluded under the EXP-001 rules. This is a single-agent visual screen, not expert-adjudicated pixel truth. `selected_images.csv` adds development/tuning roles; original selection bytes and exclusion reasons are preserved. There are no performance-driven exclusions or GT edits. No new independent confirmation images were scored.

Inference takes **RGB and common configuration only**. GT, plant tag, filename and stage do not define the ROI or QC. Measurement is total binary projected foreground **pixels**, with no physical calibration, leaf count, deep learning or watershed.

## Methods and tuning

A exactly reproduces EXP-001 HSV+ExG. B exactly reproduces EXP-002 Method B, including its frozen pot ellipse and fallback. EXP-002's failed adaptive Method C is not promoted.

New C preserves A's color thresholds. On ordinary backgrounds it finds coherent central canopy evidence, builds a buffered convex canopy envelope and retains A inside it. A partially visible pot rim no longer defines a hard canopy crop. On green-perimeter scenes it combines neutral RGB support fragments, anchors the central canopy there and admits connected contrast evidence beyond that support. The support hull is a heuristic, not a verified physical card rectangle. Low-confidence localization uses an explicit central or contrast fallback; area is withheld when QC fails.

Eight C configurations were scored only on tuning plants with equal-weight plant-macro IoU. Locked winner: peripheral normalized-ExG quantile **0.95**, contrast margin **0.03**, canopy envelope/attachment margin **0.02 of image short axis**; tuning objective **0.829962**. First-declared exact ties win. Source, configuration, selection and protocol hashes were locked before C development scoring. QC threshold **0.70** was declared in advance. No inference or threshold changed after development results.

## All 100 development images: raw predictions

Absolute percentage area error = 100*abs(P-G)/G; signed error = 100*(P-G)/G. IoU and Dice compare native binary masks. P90/P95 use linear empirical percentiles. Catastrophic overestimate means signed error >100%; catastrophic underestimate means signed error <-50%. All headline results retain flagged raw predictions.

'''+table(headers,summary_rows(dev))+'''

Mean GT area is **192,920.35 px** for each method. Mean predicted area A/B/C is **258,218.85 / 184,637.79 / 191,190.92 px**. Every GT/predicted pixel area, absolute pixel error, signed/absolute percentage error, FP/FN, confidence and withheld reading is recorded in `metrics.csv` (381 rows, with tuning distinguished).

'''+table(['Method','Over / under / exact','Mean signed error','Conditional mean overestimate','Conditional mean underestimate'],[
        [m,f"{dev[m]['overestimated_images']} / {dev[m]['underestimated_images']} / {dev[m]['exact_area_images']}",
         f"{float(dev[m]['mean_signed_area_error']):+.2f}%",fmt(dev[m],'mean_overestimation')+'%',fmt(dev[m],'mean_underestimation')+'%'] for m in 'ABC'])+'''

The original mean/median gap remains explained by seven turf frames from seven plants: they contributed 89.32% of A's summed percentage error and averaged 480.44% error; the other 93 averaged 4.32%. C reduces FP pixels on those seven frames **94.19% versus A**. On the unchanged 93-frame non-turf diagnostic stratum, mean error A/B/C is **4.32 / 8.53 / 4.29%**; medians **1.98 / 3.00 / 2.91%**. That stratum is diagnostic only; all 100 remain in headline metrics. C's full median is 0.84 points above A and 0.14 points below B.

## Clipping, overhang and known failures

The table covers all six B underestimates below -50%, both B overestimates above +100%, and all seven original A turf catastrophes: 11 unique frames after overlap. Complete RGB/GT/ROI/card/A/B/C panels are in `known_failure_cases/`.

'''+table(['Capture (oak_rgb_ prefix omitted)','A signed error','B signed error','C signed error','C IoU','GT outside C ROI'],failure_rows)+'''

There are **no remaining C catastrophic overestimates or underestimates on development**. B's wrong crescent/fragment ellipse crops are replaced with full connected canopy envelopes or card-plus-overhang support. Maximum development GT outside C's ROI is **2.75696%**, meeting the predeclared <=5% material-clipping criterion. This is not literally zero clipping. All per-frame residuals are in `roi_diagnostics.csv` and `metrics.csv`.

C still accepts moderate turf contamination: **75RB2_2024-10-3T13_7_27Z +45.48%**, **75RA2_2024-10-3T14_2_40Z +43.43%**, and **50GB2_2024-10-3T10_24_17Z +39.34%**, all at confidence 0.95. Neutral fragments can extend the hull into non-card background; turf then survives inside support. C also retains hue/ExG misses on bright or shadowed lettuce, disconnected fringes and grid/substrate contamination. Panels for the lowest-IoU cases are in `additional_diagnostics/` and the known-case folder. `remaining_failures/` is empty because no development case crosses the declared catastrophic or >5% ROI-loss thresholds, not because every prediction is correct.

## Separate tuning results and unresolved failures

Tuning is not confirmation. All 27 raw predictions, including the new C catastrophe, remain in these scores:

'''+table(headers,summary_rows(tune))+'''

**C fails on 75GA1_2024-09-12T17_14_57Z: empty mask, -100% area error, IoU 0.** A/B are accurate there (-1.79/-3.35%). The green-periphery test misclassifies this dark grid/shadow scene: 36.54% of border pixels meet its green test; among those, 38.41% have V<15 and 65.97% V<30. Their 95th-percentile normalized ExG is 2.00, so C's contrast cutoff becomes 2.03, above the mathematical maximum 2. No contrast seed can pass. The central fallback contains all GT, so this is a color/background-branch failure rather than ROI clipping. QC score 0 withholds C's area; the same empty C reference also falsely flags the accurate A/B predictions.

**50GB1_2024-09-6T18_52_59Z retains 7.76% GT outside C ROI**, with -9.23% area error. Its displaced canopy lacks an eligible central anchor, triggering the central-acquisition fallback (confidence 0.25). QC withholds the reading, but the raw clipping goal is not achieved universally. Correctly labeled panels are in `QC_flagged_tuning_cases_corrected/`; supporting dark-periphery statistics are in `tuning_failure_diagnostics.csv`. These post-hoc findings did not trigger retuning.

## Automatic QC before reporting area

Confidence is an interpretable heuristic, not a calibrated probability. Geometry confidence is capped by localization fallback and ROI-boundary contact; mask retention/purity against RGB-derived C provide additional disagreement checks for A/B. If score <0.70, `reported_pixel_area` is blank while raw pixels/masks remain available. Consumers must use the reported field and honor the flag.

**C is compared with itself for retention/purity**, which are therefore 1 for nonempty predictions. Its QC chiefly measures localization geometry and cannot detect its own semantic contamination. No GT enters inference; GT defines failure truth only afterward.

'''+table(['Subset','Method','Failure truth','TP / FP / FN / TN','Precision','Recall','Unflagged coverage'],qrows)+'''

B's QC flags all eight catastrophic development failures, but seven additional flags reduce precision to 53.33%. For material ROI loss it catches 13/14; the miss is **75RC4_2024-09-7T8_34_25Z**, with 5.52% GT outside ROI and -8.87% area error. These are descriptive development scores using new C as the reference, not independently validated EXP-002 confidence results.

C flags none of the 100 development images and has no threshold-defined failures there. Precision/recall/F1 are **undefined (N/A), not 100%**. On tuning, two C flags catch the one catastrophic error and the one material clipping failure; each truth separately gives precision 50% / recall 100%, with only one positive. This is too little evidence to establish a safety detector. Accepted-only metrics and coverage are saved separately and never replace the raw headline results.

## Performance across plants and capture stages

C improves mean IoU versus B on **33/34 plants**. The small regression is 75RA2: 0.810407 -> 0.809856. Plant-macro mean IoU A/B/C is **0.7990 / 0.8089 / 0.8913**; plant-macro mean area error **54.94 / 20.63 / 7.11%**. Area error improves on **19/34 versus B** (15 regress), and **20/34 versus A** (14 regress). Improvements extend across plants, but are not uniform. The 100 repeated images are correlated observations, not 100 independent plants.

'''+table(['Stage','Images','Method','Mean IoU','Mean area error','Median','P90','P95'],[
        [r['stage'],r['images'],r['method'],fmt(r,'mean_iou',4)]+[fmt(r,k)+'%' for k in ['mean_area_error','median_area_error','p90_area_error','p95_area_error']]
        for r in stages if r['split']=='development'])+'''

Late frames retain the largest tail, with only 11 images. Full per-plant results follow; CSVs retain Dice, pixel errors, signed bias and tail percentiles as well.

'''+table(['Plant','Images','A IoU','B IoU','C IoU','A mean area error','B mean area error','C mean area error'],plantrows)+'''

## Independent confirmation source audit

**No adequate genuinely independent plant-separated lettuce holdout was found; zero confirmation images were scored.** Unknown plant identity is not zero overlap.

'''+table(['Source','Available metadata rows / scenes','Physical-ID overlap with prior exposed plants','Confirmation decision'],[
    ['OAK-1','1522 / 45 IDs','45/45; all screened previously','No unused physical IDs'],
    ['OAK-D S2','441 RGB scenes, 441 labels/sidecars','UNKNOWN; no tag/instance identity mapping','Not eligible yet'],
    ['Flexx','670 / 45 IDs','45/45','Point-cloud color projections; overlapping plants'],
    ['SPECIM','54 / 27 IDs','27/27','Overlapping NIR hyperspectral samples'],
    ['Livox','2504','UNKNOWN','Depth/intensity, no verified identity'],
    ['Manual','360 / 45 IDs','45/45','Biometrics, no segmentation masks'],
    ['RumexLeaves','Different species','Not applicable to lettuce','Cannot confirm lettuce performance']])+'''

OAK-D metadata uses `oakd_rgb_`, while archive images/labels use `oak_rgb_`; canonical timestamp pairing uniquely resolves all 441 scenes. All 441 sidecars were inspected without finding physical tags. Labels include six empty files and often 44/45 polygons. Three representative RGB scenes show whole beds with many pots; no polygon-to-physical-ID map exists. Different cameras or dates do not establish independence. The two OAK-1 tags absent from the selected 43 tags (75RB5,75RC2) were already exposed during the original screen and are not a fresh holdout.

Evidence: `source_audit/source_overlap.json`, `OAKD_pairs.csv`, all sidecar hashes, copied metadata/readmes and `OAKD_layout_preview.jpg`. Archive central-directory and read-entry hashes/CRCs are recorded; no full 39GB archive SHA is claimed. An initial interrupted prefix-mismatch audit is retained in `source_audit_attempt001/`; only `source_audit/` is authoritative. OAK-D could support future work after physical identity mapping, verified disjoint plants, a frozen instance/crop protocol and annotation review. It is not used as confirmation now.

## Verification, outputs and decision

**30 tests pass. Independent verifier PASS: 8,125 checks**, including 381 native masks, all 216 C tuning trials, independently recomputed metrics/plant/stage/QC/gates, unique source pairing, all 1522 raw pair hashes and every EXP-001/002 artifact unchanged. A/B masks exactly match their earlier runs. A read-only reviewer separately checked **3,429 native pixel/ROI quantities**, the known failures, residual panels, QC confusion counts and source identity evidence. See `verification.json`, `test_results_final.txt`, `review_resolution.md` and final artifact manifest.

Reusable code is under `src/`: `robust_roi.py`, `exp003.py`, `confirmation_audit.py`, `verify_exp003.py`, `exp003_diagnostics.py` and this report generator. Outputs include frozen protocol/source/config/provenance, inherited manifests, all raw masks and ROI/card masks, metrics/plant/stage/QC tables, known failures and post-hoc diagnostics. Prior LATEST_REPORT is archived byte-for-byte as `EXP-002_LATEST_REPORT.md`.

Presentation erratum: the frozen panel renderer hardcodes DEVELOPMENT in its footer. This correctly describes development panels but mislabeled the original two tuning panels in `QC_flagged_tuning_cases/`. Those originals are preserved; the supplemental renderer produces correct TUNING footers in `QC_flagged_tuning_cases_corrected/`. Numerical rows always had correct split labels. Locked inference sources remain unchanged.

The predeclared result is **PASS_DEVELOPMENT_CRITERIA**, not an unbiased scientific confirmation or readiness for unattended daily growth measurement. Classical CV is viable enough to continue under acquisition controls and QC.

**Recommended EXP-004:** correct the dark-periphery/impossible-contrast branch and displaced-canopy fallback on tuning plants; constrain neutral support with interpretable card geometry; add independent semantic QC evidence rather than C self-agreement. Freeze the new method before scoring a newly collected cohort with recorded disjoint plant IDs, neutral backgrounds, stable lighting and independently adjudicated masks. Validate withheld-area detection and longitudinal area consistency on that cohort. Do not claim physical area without scale calibration. No deep learning yet. EXP-004 is recommended, not started.
'''
    with (OUT/'summary.md').open('x',encoding='utf-8',newline='\n') as f:f.write(report)
    print('Created comprehensive development report from frozen metrics')


if __name__=='__main__':build()
