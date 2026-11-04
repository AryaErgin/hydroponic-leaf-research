"""Technical mask labels, stable identities and a separate compact display order.

Legacy identities are deterministic within an analysis. New editor identities are
UUIDs. Retired technical labels remain reserved; display numbers are never keys.
"""
import numpy as np

MAX_ID=4096

def resolve(labels,analysis_id,provided=None,previous=None,strict=False):
    active=set(map(int,np.unique(labels[labels>0])))
    records={int(r['label_id']):dict(r) for r in (previous or {}).get('instances',[])}
    source=provided if provided is not None else previous
    if source is not None:
        if not isinstance(source,dict) or source.get('version')!=1:raise ValueError('Invalid instance identity version')
        entries=source.get('instances');order=source.get('display_order')
        if not isinstance(entries,list) or len(entries)>MAX_ID or not isinstance(order,list):raise ValueError('Invalid instance identity registry')
        seen=set()
        for entry in entries:
            if not isinstance(entry,dict):raise ValueError('Invalid instance identity')
            label=entry.get('label_id');identity=entry.get('internal_instance_id')
            if type(label) is not int or not 1<=label<=MAX_ID or label in seen:raise ValueError('Invalid or repeated technical instance label')
            if not isinstance(identity,str) or not 1<=len(identity)<=160 or any(not(c.isascii() and (c.isalnum() or c in ':._-')) for c in identity):raise ValueError('Invalid internal instance identity')
            if label in records and identity!=records[label]['internal_instance_id']:raise ValueError('Existing internal instance identity must remain stable')
            records[label]=dict(label_id=label,internal_instance_id=identity);seen.add(label)
        if any(type(i) is not int or not 1<=i<=MAX_ID for i in order) or len(order)!=len(set(order)):raise ValueError('Invalid instance display order')
        if strict and (set(order)!=active or not active.issubset(seen)):raise ValueError('Display order must contain every current leaf exactly once')
    else:order=[]
    for label in active:
        records.setdefault(label,dict(label_id=label,internal_instance_id=f'{analysis_id or "unscoped"}:instance:{label}'))
    if len({r['internal_instance_id'] for r in records.values()})!=len(records):raise ValueError('Internal instance identities must be unique')
    order=[i for i in order if i in active];order.extend(sorted(active-set(order)))
    minimum=max(records,default=0)+1
    requested=(source or {}).get('next_label_id',minimum)
    if type(requested) is not int or not 1<=requested<=MAX_ID+1 or (strict and requested<minimum):raise ValueError('Invalid next technical instance label')
    return dict(version=1,instances=[dict(records[i],active=i in active) for i in sorted(records)],display_order=order,next_label_id=max(minimum,requested,(previous or {}).get('next_label_id',1)))

def retain_history(labels,analysis_id,metadata,events):
    """Recover old-editor snapshot omissions without inventing active leaves.

    Native labels and strict active identity/order validation precede this step.
    Historical UUIDs come from interaction provenance; legacy events without a
    UUID use the same analysis-scoped technical identity as legacy mask labels.
    Re-resolving validates aliases, conflicting UUIDs and all registry bounds.
    """
    if not isinstance(events,list):raise ValueError('Invalid instance identity history')
    records={r['label_id']:dict(r) for r in metadata['instances']}
    for event in events:
        if not isinstance(event,dict):raise ValueError('Invalid instance identity history')
        for labels_key,identity_key in [('leaf_ids','internal_instance_ids'),('source_leaf_ids','source_internal_instance_ids')]:
            ids=event.get(labels_key,[]);identities=event.get(identity_key)
            # Earlier trace writers emitted [] for unspecified Add source UUIDs.
            # Recover those known source identities just like an omitted field.
            if identity_key=='source_internal_instance_ids' and identities==[]:identities=None
            if not isinstance(ids,list) or len(ids)>MAX_ID or any(type(i) is not int or not 1<=i<=MAX_ID for i in ids):raise ValueError('Invalid instance identity history')
            if identities is not None and (not isinstance(identities,list) or len(identities)!=len(ids)):raise ValueError('Invalid instance identity history')
            for n,label in enumerate(ids):
                identity=identities[n] if identities is not None else None
                if identity is None:
                    identity=records.get(label,{}).get('internal_instance_id',f'{analysis_id}:instance:{label}')
                if label in records and identity!=records[label]['internal_instance_id']:raise ValueError('Existing internal instance identity must remain stable')
                records[label]=dict(label_id=label,internal_instance_id=identity)
    return resolve(labels,analysis_id,dict(metadata,instances=list(records.values())),previous=metadata)

def decorate(leaves,metadata):
    by_label={r['label_id']:r['internal_instance_id'] for r in metadata['instances']}
    rows={r['leaf_id']:r for r in leaves}
    return [dict(rows[label],display_leaf_number=number,internal_instance_id=by_label[label]) for number,label in enumerate(metadata['display_order'],1)]
