"""Two-click, image-bound reviewed outer-pot diameter; no segmentation changes."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image
from .hydroponic.config import ROOT
from .pot_scale import calibrate_pot, pot_overlay, validate_diameter

CACHE_ROOT = ROOT/'results/calibration/pot'


def cached_review_path(source, cache_root=CACHE_ROOT):
    digest = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    return Path(cache_root)/f'{digest}.pot.json'


def save_reviewed_diameter(source, points, diameter_cm, reviewed=False, cache_root=CACHE_ROOT, expected_sha256=None):
    if reviewed is not True:
        raise ValueError('Explicit outer-rim/opposite-point/approximation review required')
    source = Path(source);data=source.read_bytes();digest=hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError('Image changed during diameter selection')
    with Image.open(io.BytesIO(data)) as image:
        if image.getexif().get(274,1)!=1:
            raise ValueError('Normalize image orientation before selecting endpoints')
        rgb=np.array(image.convert('RGB'));w,h=image.size
    payload=dict(schema_version=1,image_sha256=digest,image_size_px=[w,h],known_pot_diameter_cm=diameter_cm,
        calibration_method='manual_two_point_diameter',diameter_points_px=points,
        review=dict(outer_rim_confirmed=True,opposite_points_confirmed=True,pot_plane_approximation_accepted=True))
    result=calibrate_pot(rgb,diameter_cm,digest,payload)
    if result['status']!='ACCEPTED_MANUAL_REVIEW':
        raise ValueError('; '.join(result['reasons']))
    payload['diameter_points_px']=result['candidate']['diameter_points_px']
    cache=Path(cache_root).resolve()
    if ROOT not in cache.parents:
        raise ValueError('Calibration cache must be below project root')
    cache.mkdir(parents=True,exist_ok=True)
    history=cache/'history';history.mkdir(exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    payload.update(reviewed_at_utc=stamp,source_path=str(source.resolve()))
    serialized=json.dumps(payload,indent=2,allow_nan=False)+'\n'
    immutable=history/f'{digest}_{stamp}.pot.json'
    with immutable.open('x',encoding='utf-8') as stream:stream.write(serialized)
    overlay=pot_overlay(rgb,result)
    overlay.save(immutable.with_suffix('.overlay.png'))
    path=cache/f'{digest}.pot.json'
    temporary=cache/f'{digest}_{stamp}.tmp'
    temporary.write_text(serialized,encoding='utf-8');temporary.replace(path)
    overlay.save(path.with_suffix('.overlay.png'))
    return path


def display_to_native(x, y, native_size, display_size):
    """Map resized picker clicks to fractional native coordinates without resizing inference."""
    w,h=native_size;dw,dh=display_size
    if not (0<=x<dw and 0<=y<dh):raise ValueError('Click outside displayed image')
    return [min(w-1,x*w/dw),min(h-1,y*h/dh)]


def select_manual_diameter(source, diameter_cm):
    """Visible Tk picker; two clicks + explicit review; cancel returns no calibration."""
    validate_diameter(diameter_cm)
    import tkinter as tk
    from tkinter import messagebox
    from PIL import ImageTk
    source=Path(source);data=source.read_bytes();digest=hashlib.sha256(data).hexdigest()
    with Image.open(io.BytesIO(data)) as image:
        if image.getexif().get(274,1)!=1:raise ValueError('Normalize image orientation before selecting endpoints')
        original=image.convert('RGB')
    try:window=tk.Tk()
    except tk.TclError as exc:raise ValueError('Manual picker needs an interactive desktop; use a reviewed two-point .pot.json instead') from exc
    window.title(f'Manual OUTER pot diameter - {diameter_cm} cm')
    instruction=f'Click TWO OPPOSITE OUTER-rim points. Known physical diameter: {diameter_cm} cm.\nRight click undoes a point. No automatic circle will override this diameter.'
    tk.Label(window,text=instruction,justify='left').pack()
    scale=min(1.,(window.winfo_screenwidth()-100)/original.width,(window.winfo_screenheight()-300)/original.height)
    size=(max(1,round(original.width*scale)),max(1,round(original.height*scale)))
    photo=ImageTk.PhotoImage(original.resize(size,Image.Resampling.LANCZOS))
    canvas=tk.Canvas(window,width=size[0],height=size[1],highlightthickness=0)
    canvas.pack();canvas.create_image(0,0,image=photo,anchor='nw')
    points=[];status=tk.StringVar(value='Select point 1.');result=[None]
    tk.Label(window,textvariable=status).pack()
    confirmations=[tk.BooleanVar(value=False) for _ in range(3)]
    for variable,text in zip(confirmations,('These points are on the OUTER rim.','These are opposite points across the full diameter, not a chord.',
                                          'I accept an approximate pot-plane scale (view/lens/height effects remain).')):
        tk.Checkbutton(window,text=text,variable=variable).pack(anchor='w')

    def redraw():
        canvas.delete('selection')
        for number,(x,y) in enumerate(points,1):
            dx=x*size[0]/original.width;dy=y*size[1]/original.height
            canvas.create_oval(dx-4,dy-4,dx+4,dy+4,outline='cyan',width=2,tags='selection')
            canvas.create_text(dx+10,dy+10,text=f'P{number}',fill='yellow',tags='selection')
        if len(points)==2:
            a,b=points
            canvas.create_line(a[0]*size[0]/original.width,a[1]*size[1]/original.height,
                               b[0]*size[0]/original.width,b[1]*size[1]/original.height,fill='lime',width=2,tags='selection')
            distance=float(np.linalg.norm(np.array(b)-a))
            status.set(f'Native points: {a}, {b}; diameter={distance:.3f} px. Confirm review, then save.')
        else:status.set(f'Select point {len(points)+1}.')

    def click(event):
        if len(points)<2:
            try:points.append(display_to_native(event.x,event.y,original.size,size))
            except ValueError:return
            for flag in confirmations:flag.set(False)
            redraw()

    def undo(event=None):
        if points:points.pop()
        for flag in confirmations:flag.set(False)
        redraw()

    def save():
        if len(points)!=2 or not all(flag.get() for flag in confirmations):
            messagebox.showerror('Review needed','Select two points and confirm all three statements.',parent=window);return
        try:result[0]=save_reviewed_diameter(source,points,diameter_cm,reviewed=True,expected_sha256=digest)
        except (ValueError,OSError) as exc:
            messagebox.showerror('Cannot save calibration',str(exc),parent=window);return
        window.destroy()

    canvas.bind('<Button-1>',click);canvas.bind('<Button-3>',undo)
    buttons=tk.Frame(window);buttons.pack()
    tk.Button(buttons,text='Undo point',command=undo).pack(side='left')
    tk.Button(buttons,text='Save reviewed diameter',command=save).pack(side='left')
    tk.Button(buttons,text='Cancel (withhold scale)',command=window.destroy).pack(side='left')
    window.mainloop()
    return result[0]
