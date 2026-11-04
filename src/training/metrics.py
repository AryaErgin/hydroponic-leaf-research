import numpy as np


def binary_metrics(prediction,reference):
    pred=np.asarray(prediction);gt=np.asarray(reference)
    if pred.shape!=gt.shape or pred.ndim!=2 or not np.isin(pred,[0,1]).all() or not np.isin(gt,[0,1]).all():
        raise ValueError('Metrics require aligned native binary masks')
    pred,gt=pred.astype(bool),gt.astype(bool)
    tp=int((pred&gt).sum());fp=int((pred&~gt).sum());fn=int((~pred&gt).sum());truth=int(gt.sum());area=int(pred.sum())
    union=tp+fp+fn;total=area+truth;delta=area-truth
    return dict(predicted_pixel_area=area,true_pixel_area=truth,true_positive_pixels=tp,false_positive_pixels=fp,
        false_negative_pixels=fn,iou=tp/union if union else 1.,dice=2*tp/total if total else 1.,
        pixel_area_abs_error=abs(delta),signed_percentage_area_error=100*delta/truth if truth else None,
        absolute_percentage_area_error=100*abs(delta)/truth if truth else None)


def aggregate(rows):
    if not rows:raise ValueError('No evaluation rows')
    percentage=[r['absolute_percentage_area_error'] for r in rows if r['absolute_percentage_area_error'] is not None]
    mean=lambda key:float(np.mean([r[key] for r in rows]))
    result=dict(sample_count=len(rows),mean_iou=mean('iou'),mean_dice=mean('dice'),pixel_area_mae=mean('pixel_area_abs_error'),
        mean_false_positive_pixels=mean('false_positive_pixels'),mean_false_negative_pixels=mean('false_negative_pixels'))
    result.update(mean_abs_percentage_area_error=float(np.mean(percentage)) if percentage else None,
        median_abs_percentage_area_error=float(np.median(percentage)) if percentage else None,
        p90_area_error_percent=float(np.percentile(percentage,90)) if percentage else None,
        p95_area_error_percent=float(np.percentile(percentage,95)) if percentage else None)
    if all('inference_seconds' in row for row in rows):result['mean_inference_seconds']=mean('inference_seconds')
    return result
