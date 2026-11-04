"""Human-reviewed visible-only lettuce masks; independent of automatic segmentation."""
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, PngImagePlugin

from .hydroponic.config import ROOT
from .manual_pot import cached_review_path
from .pot_scale import calibrate_pot

STATUS = 'MANUAL_GROUND_TRUTH'
OUTPUT_ROOT = ROOT / 'results/manual_ground_truth'
REVIEW_KEYS = ('visible_lettuce_only', 'visible_tissue_complete', 'moss_excluded',
               'growing_medium_excluded', 'dead_tissue_excluded', 'pot_and_background_excluded',
               'pot_plane_approximation_accepted')
EXCLUSIONS = ['moss', 'other vegetation', 'growing medium', 'dead tissue', 'pot', 'background', 'hidden/occluded tissue']


def _binary_mask(mask, shape):
    array = np.asarray(mask)
    if array.shape != shape or array.dtype.kind not in 'bui':
        raise ValueError('Mask must be a native-size, single-channel binary image')
    values = set(np.unique(array).tolist())
    if not (values <= {0, 1} or values <= {0, 255}):
        raise ValueError('Mask must contain only 0/1 or 0/255; no antialiasing or soft labels')
    return array != 0


def load_target_mask(path, native_size):
    """Import exact native binary PNG; never threshold, resize, crop or rotate."""
    with Image.open(path) as image:
        if image.format != 'PNG' or image.mode not in ('1', 'L'):
            raise ValueError('Provide a single-channel binary PNG (mode 1 or L)')
        if image.size != native_size or image.getexif().get(274, 1) != 1:
            raise ValueError('Target mask must match original image size and orientation')
        return _binary_mask(np.array(image), (native_size[1], native_size[0]))


def load_reviewed_calibration(source, calibration_path=None):
    source = Path(source)
    if not source.name.casefold().startswith('lettuce_'):
        raise ValueError('This first real-data ground-truth workflow requires a lettuce_ image')
    data = source.read_bytes(); digest = hashlib.sha256(data).hexdigest()
    with Image.open(io.BytesIO(data)) as image:
        if image.getexif().get(274, 1) != 1:
            raise ValueError('Normalize orientation and rebind calibration before target review')
        rgb = np.array(image.convert('RGB'))
    path = Path(calibration_path) if calibration_path is not None else cached_review_path(source)
    if not path.is_file():
        raise ValueError('No saved reviewed pot calibration for this image; review the pot first')
    raw = path.read_bytes()
    try:sidecar = json.loads(raw)
    except (ValueError, UnicodeError) as exc:raise ValueError('Invalid saved pot calibration JSON') from exc
    if not isinstance(sidecar, dict) or 'known_pot_diameter_cm' not in sidecar:
        raise ValueError('Saved calibration must include its reviewed physical outer diameter')
    # A sidecar is mandatory: this cannot enter the automatic proposal path.
    calibration = calibrate_pot(rgb, sidecar['known_pot_diameter_cm'], digest, sidecar)
    if calibration['status'] != 'ACCEPTED_MANUAL_REVIEW':
        raise ValueError('Saved pot calibration is not valid for this image: ' + '; '.join(calibration['reasons']))
    return dict(rgb=rgb, image_sha256=digest, calibration=calibration, calibration_path=path,
                calibration_bytes=raw, calibration_sha256=hashlib.sha256(raw).hexdigest())


def mask_overlay(rgb, mask):
    blended = rgb.copy()
    blended[mask] = np.round(.55*rgb[mask] + .45*np.array([0, 220, 255])).astype(np.uint8)
    return Image.fromarray(blended)


def save_ground_truth(source, mask, review, output_root=OUTPUT_ROOT, calibration_path=None,
                      expected_source_sha256=None, expected_calibration_sha256=None, imported_mask=None,
                      review_method='explicit_api_attestation', label_origin=None):
    """Persist a create-only reviewed sample, preserving image and calibration bytes."""
    if not isinstance(review, dict) or any(review.get(key) is not True for key in REVIEW_KEYS):
        raise ValueError('Explicit visible-target/exclusion/completeness/scale review required')
    context = load_reviewed_calibration(source, calibration_path)
    if expected_source_sha256 is not None and context['image_sha256'] != expected_source_sha256:
        raise ValueError('Image changed during target-mask review')
    if expected_calibration_sha256 is not None and context['calibration_sha256'] != expected_calibration_sha256:
        raise ValueError('Pot calibration changed during target-mask review; reopen to review current scale')
    return save_reviewed_sample(source, mask, review, context, output_root,
        imported_mask=imported_mask, review_method=review_method, label_origin=label_origin)


def save_reviewed_sample(source, mask, review, context, output_root=OUTPUT_ROOT,
                         species='lettuce', review_keys=REVIEW_KEYS, imported_mask=None,
                         review_method='explicit_api_attestation', label_origin=None):
    """Shared native reviewed-sample writer; context comes from validated calibration adapters.

    The legacy image-bound pot path above and the web app's explicitly attested
    reusable-profile path share the same binary/review/count/overlay/provenance logic.
    """
    if species not in ('lettuce','rumex_acetosa'):
        raise ValueError('Unsupported target species')
    if not isinstance(review,dict) or any(review.get(key) is not True for key in review_keys):
        raise ValueError('Explicit target/exclusion/completeness/scale review required')
    if hashlib.sha256(Path(source).read_bytes()).hexdigest()!=context['image_sha256']:
        raise ValueError('Image changed during review')
    rgb = context['rgb']; mask = _binary_mask(mask, rgb.shape[:2])
    area = int(mask.sum())
    if not area:raise ValueError('An empty mask cannot be this visible-lettuce validation sample')
    root = Path(output_root).resolve()
    # Windows extended paths address the same resolved files. Compare canonical
    # ordinary paths for containment, retaining the extended form for long IO.
    comparison=Path(str(root).removeprefix('\\\\?\\'))
    project=Path(str(ROOT.resolve()).removeprefix('\\\\?\\'))
    if project not in comparison.parents:raise ValueError('Ground-truth outputs must be below project root')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    output = root / stamp / Path(source).stem
    output.mkdir(parents=True, exist_ok=False)
    png_info = PngImagePlugin.PngInfo()
    png_info.add_text('measurement_status', STATUS)
    png_info.add_text('image_sha256', context['image_sha256'])
    png_info.add_text('target_definition', f'ONLY visible living {species} tissue; excludes moss/medium/dead tissue/pot/background')
    Image.fromarray(mask.astype(np.uint8)*255).save(output/'reviewed_mask.png', pnginfo=png_info)
    calibration = context['calibration']; scale = calibration['cm_per_pixel']
    physical_area = area*scale**2 if scale is not None else None
    overlay = mask_overlay(rgb, mask)
    # Native image and mask coordinates remain aligned; labels occupy a separate footer.
    physical_label = f'{physical_area:.6f} cm2' if physical_area is not None else 'unavailable (pixels only)'
    label = f'{STATUS} | {area} pixels | approximate projected area: {physical_label}\nVisible {species} only; manually reviewed, not automated prediction'
    draw = ImageDraw.Draw(overlay); bounds = draw.multiline_textbbox((0, 0), label)
    labeled = Image.new('RGB', (max(overlay.width, bounds[2]+12), overlay.height+bounds[3]+12))
    labeled.paste(overlay, (0, 0))
    ImageDraw.Draw(labeled).multiline_text((6, overlay.height+6), label, fill='white')
    labeled.save(output/'overlay.png', pnginfo=png_info)
    (output/'reviewed_pot_calibration.json').write_bytes(context['calibration_bytes'])
    (output/'pot_calibration.json').write_text(json.dumps(calibration, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    meta = dict(schema_version=1, measurement_status=STATUS, qc_status='MANUAL_REVIEWED',
        species=species, filename=Path(source).name, source_path=str(Path(source).resolve()),
        image_sha256=context['image_sha256'], image_size_px=[rgb.shape[1], rgb.shape[0]],
        pixel_area=area, projected_area_cm2=physical_area, cm_per_pixel=scale,
        pot_diameter_cm=calibration['known_pot_diameter_cm'], pot_diameter_pixels=calibration['diameter_pixels'],
        calibration_status=calibration['status'], calibration_path=str(context['calibration_path'].resolve()) if context['calibration_path'] else None,
        calibration_sha256=context['calibration_sha256'], reviewed_at_utc=stamp,
        mask_path='reviewed_mask.png', mask_sha256=hashlib.sha256((output/'reviewed_mask.png').read_bytes()).hexdigest(),
        review=dict(review), review_method=review_method,
        target_definition=f'Only visible living {species} tissue; union of visible surfaces, no hidden tissue reconstruction',
        exclusions=EXCLUSIONS, scale_interpretation=calibration.get('interpretation',
            'Approximate projected area at the reviewed pot-rim plane; not true 3D surface area'),
        label_origin=label_origin or 'human-provided/edited and explicitly reviewed; no automatic mask initialization',
        validation_scope=f'Single hydroponic {species} development sample; no population segmentation accuracy claim',
        source_sha256={'src/manual_target.py':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       'src/pot_scale.py':hashlib.sha256((ROOT/'src/pot_scale.py').read_bytes()).hexdigest()})
    if imported_mask is not None:meta['imported_mask'] = imported_mask
    (output/'measurement.json').write_text(json.dumps(meta, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    fields = ('filename', 'species', 'measurement_status', 'qc_status', 'pixel_area', 'projected_area_cm2',
              'pot_diameter_cm', 'pot_diameter_pixels', 'cm_per_pixel', 'calibration_status')
    with (output/'measurements.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        writer.writerow({key:meta[key] for key in fields})
    (output/'qc_status.txt').write_text('MANUAL_GROUND_TRUTH / MANUAL_REVIEWED\nNot an automated prediction or algorithmic QC confidence.\n', encoding='utf-8')
    return output


def canvas_to_native(x, y, native_size, display_size):
    w, h = native_size; dw, dh = display_size
    if not (0 <= x < dw and 0 <= y < dh):raise ValueError('Point outside image canvas')
    return [min(w-1, x*w/dw), min(h-1, y*h/dh)]


def edit_polygon(mask, points, include):
    points = np.asarray(points, dtype=float)
    h, w = mask.shape
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 3 or not np.isfinite(points).all():
        raise ValueError('Polygon needs at least three finite native points')
    if (points < 0).any() or (points[:, 0] > w-1).any() or (points[:, 1] > h-1).any():
        raise ValueError('Polygon outside native image')
    layer = Image.fromarray(mask.astype(np.uint8)*255)
    ImageDraw.Draw(layer).polygon([tuple(point) for point in points], fill=255 if include else 0)
    return np.array(layer) != 0


def edit_brush(mask, start, end, radius, include):
    if not np.isfinite(radius) or radius < 1:raise ValueError('Native brush radius must be at least one pixel')
    h, w = mask.shape
    for x, y in (start, end):
        if not (np.isfinite(x) and np.isfinite(y) and 0 <= x < w and 0 <= y < h):
            raise ValueError('Brush outside native image')
    layer = Image.fromarray(mask.astype(np.uint8)*255); draw = ImageDraw.Draw(layer)
    fill = 255 if include else 0
    draw.line(tuple(start)+tuple(end), fill=fill, width=round(2*radius))
    for x, y in (start, end):draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=fill)
    return np.array(layer) != 0


def edit_target_mask(rgb, initial_mask, calibration):
    """Native-coordinate Tk brush/polygon editor; cancel returns no reviewed data."""
    import tkinter as tk
    from tkinter import messagebox
    from PIL import ImageTk
    try:window = tk.Tk()
    except tk.TclError as exc:
        raise ValueError('Cannot start target editor: '+str(exc)+'\nUse a working Tcl/Tk desktop or supply a reviewed binary PNG with --target-mask and --confirm-target-mask-review.') from exc
    window.title('Manual visible lettuce ground truth - reviewed pot scale')
    h, w = rgb.shape[:2]; state = dict(mask=initial_mask.copy(), polygon=[], history=[], last=None, photo=None)
    result = [None]
    tk.Label(window, text='ONLY visible lettuce tissue. EXCLUDE moss, medium, dead tissue, pot and background.\n'
        'Brush: drag to add/erase. Polygon: click vertices then Apply (Enter). Zoom/scroll for native detail.').pack()
    controls = tk.Frame(window); controls.pack(fill='x')
    mode = tk.StringVar(value='add_polygon'); radius = tk.IntVar(value=6); visibility = tk.BooleanVar(value=True)
    fit = min(1., max(.1, (window.winfo_screenwidth()-150)/w), max(.1, (window.winfo_screenheight()-380)/h))
    zoom = tk.DoubleVar(value=fit); status = tk.StringVar()
    for value, text in [('add_polygon','Add polygon'), ('erase_polygon','Erase polygon'), ('paint','Paint'), ('erase','Erase')]:
        tk.Radiobutton(controls, text=text, value=value, variable=mode).pack(side='left')
    tk.Label(controls, text='Brush radius (native px)').pack(side='left')
    tk.Spinbox(controls, from_=1, to=100, width=4, textvariable=radius).pack(side='left')
    panel = tk.Frame(window); panel.pack(fill='both', expand=True)
    canvas = tk.Canvas(panel, width=max(100, round(w*fit)), height=max(100, round(h*fit)), highlightthickness=0)
    sx = tk.Scrollbar(panel, orient='horizontal', command=canvas.xview)
    sy = tk.Scrollbar(panel, orient='vertical', command=canvas.yview)
    canvas.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
    canvas.grid(row=0, column=0, sticky='nsew'); sy.grid(row=0, column=1, sticky='ns'); sx.grid(row=1, column=0, sticky='ew')
    panel.rowconfigure(0, weight=1); panel.columnconfigure(0, weight=1)
    confirmations = {key:tk.BooleanVar(value=False) for key in REVIEW_KEYS}

    def reset_review():
        for flag in confirmations.values():flag.set(False)

    def checkpoint():
        state['history'].append(state['mask'].copy()); state['history'] = state['history'][-20:]
        reset_review()

    def redraw():
        size = (max(1, round(w*zoom.get())), max(1, round(h*zoom.get())))
        original = Image.fromarray(rgb).resize(size, Image.Resampling.LANCZOS)
        if visibility.get():
            shown = np.array(Image.fromarray(state['mask']).resize(size, Image.Resampling.NEAREST))
            original = mask_overlay(np.array(original), shown)
        state['photo'] = ImageTk.PhotoImage(original)
        canvas.delete('all'); canvas.create_image(0, 0, image=state['photo'], anchor='nw')
        canvas.configure(scrollregion=(0, 0, *size))
        vertices = [(x*size[0]/w, y*size[1]/h) for x,y in state['polygon']]
        if len(vertices)>1:canvas.create_line([v for point in vertices for v in point], fill='yellow', width=2)
        for x,y in vertices:canvas.create_oval(x-3,y-3,x+3,y+3, fill='yellow')
        area = int(state['mask'].sum())
        status.set(f'Unreviewed draft: {area} px | approx {area*calibration["cm_per_pixel"]**2:.6f} cm2 | zoom {zoom.get():.2f}x')

    def position(event):
        return canvas_to_native(canvas.canvasx(event.x), canvas.canvasy(event.y), (w,h),
            (max(1,round(w*zoom.get())), max(1,round(h*zoom.get()))))

    def click(event):
        try:point = position(event)
        except ValueError:return
        if mode.get().endswith('polygon'):
            state['polygon'].append(point); reset_review(); redraw()
        else:
            try:
                brush = radius.get()
                updated = edit_brush(state['mask'], point, point, brush, mode.get()=='paint')
            except (ValueError, tk.TclError):return
            checkpoint(); state['mask'] = updated; state['last'] = point; redraw()

    def drag(event):
        if state['last'] is None:return
        try:
            point = position(event)
            state['mask'] = edit_brush(state['mask'], state['last'], point, radius.get(), mode.get()=='paint')
        except (ValueError, tk.TclError):return
        state['last'] = point; reset_review(); redraw()

    def apply_polygon(event=None):
        if not mode.get().endswith('polygon'):return
        try:updated = edit_polygon(state['mask'], state['polygon'], mode.get()=='add_polygon')
        except ValueError as exc:messagebox.showerror('Polygon', str(exc), parent=window); return
        checkpoint(); state['mask'] = updated; state['polygon'] = []; redraw()

    def undo(event=None):
        if state['polygon']:state['polygon'].pop()
        elif state['history']:state['mask'] = state['history'].pop()
        reset_review(); redraw()

    def clear():
        checkpoint(); state['mask'][:] = False; state['polygon'] = []; redraw()

    def discard_polygon(event=None):
        state['polygon'] = []; state['last'] = None; reset_review(); redraw()

    def save():
        if state['polygon']:
            messagebox.showerror('Unfinished polygon', 'Apply or discard the unfinished polygon first.', parent=window); return
        if not state['mask'].any() or not all(flag.get() for flag in confirmations.values()):
            messagebox.showerror('Review required', 'Finish a nonempty target mask and confirm every review statement.', parent=window); return
        result[0] = (state['mask'].copy(), {key:flag.get() for key,flag in confirmations.items()})
        window.destroy()

    toolbar = tk.Frame(window); toolbar.pack(fill='x')
    for text, command in [('Apply polygon',apply_polygon), ('Discard polygon',discard_polygon), ('Undo',undo), ('Clear mask',clear)]:
        tk.Button(toolbar, text=text, command=command).pack(side='left')
    tk.Checkbutton(toolbar, text='Show mask', variable=visibility, command=redraw).pack(side='left')
    tk.Label(toolbar, text='Zoom').pack(side='left')
    tk.Spinbox(toolbar, from_=.1, to=3., increment=.1, textvariable=zoom, width=5,
               command=redraw, state='readonly').pack(side='left')
    tk.Label(window, textvariable=status).pack()
    texts = ('Mask contains only visible living lettuce tissue.', 'All identifiable visible lettuce tissue is included; no hidden tissue inferred.',
        'Moss and other vegetation are excluded.', 'Growing medium is excluded.', 'Dead tissue is excluded (uncertain tissue adjudicated manually).',
        'Pot and background are excluded.', 'I accept the reviewed pot-plane scale approximation, not true 3D leaf area.')
    for key,text in zip(REVIEW_KEYS,texts):tk.Checkbutton(window, text=text, variable=confirmations[key]).pack(anchor='w')
    buttons = tk.Frame(window); buttons.pack()
    tk.Button(buttons, text='Save MANUAL_GROUND_TRUTH', command=save).pack(side='left')
    tk.Button(buttons, text='Cancel (no ground truth)', command=window.destroy).pack(side='left')
    canvas.bind('<Button-1>',click); canvas.bind('<B1-Motion>',drag)
    canvas.bind('<ButtonRelease-1>',lambda event:state.update(last=None))
    window.bind('<Control-z>',undo); window.bind('<Return>',apply_polygon)
    window.bind('<Escape>',discard_polygon)
    redraw(); window.mainloop()
    return result[0]


def review_target_image(source, mask_path=None, output_root=OUTPUT_ROOT, confirm_external_review=False):
    if confirm_external_review and mask_path is None:
        raise ValueError('--confirm-target-mask-review requires a provided --target-mask PNG')
    context = load_reviewed_calibration(source)
    rgb = context['rgb']; initial = np.zeros(rgb.shape[:2], bool); imported = None
    if mask_path is not None:
        mask_bytes = Path(mask_path).read_bytes()
        initial = load_target_mask(io.BytesIO(mask_bytes), (rgb.shape[1], rgb.shape[0]))
        imported = dict(path=str(Path(mask_path).resolve()), sha256=hashlib.sha256(mask_bytes).hexdigest())
    selection = (initial, dict.fromkeys(REVIEW_KEYS, True)) if confirm_external_review else edit_target_mask(rgb, initial, context['calibration'])
    if selection is None:
        print('Target-mask review cancelled; no MANUAL_GROUND_TRUTH sample saved.')
        return None
    mask, review = selection
    output = save_ground_truth(source, mask, review, output_root, context['calibration_path'],
        context['image_sha256'], context['calibration_sha256'], imported,
        'explicit_cli_external_mask_attestation' if confirm_external_review else 'interactive_editor_attestation')
    meta = json.loads((output/'measurement.json').read_text(encoding='utf-8'))
    print(f'{Path(source).name} | lettuce | {meta["pixel_area"]} px | {meta["projected_area_cm2"]:.6f} cm2 | {STATUS}')
    print(f'Reviewed outputs: {output}')
    return output
