"""Procedural distractors and tissue appearance; synthetic data never validate."""
import io
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from .datasets import letterbox,target_crop

CONFIG=dict(version='exp006-augmentation-v1',scope='TRAINING_AUGMENTATION_ONLY',composite_probability=.7,
    appearance_modes=['healthy','pale','yellow_green','chlorotic','uneven'],foreground_fraction=[.25,.82],
    rotation_degrees=[-25,25],perspective_coefficient=[-.0001,.0001],noise_sigma=[0,4],jpeg_quality=[55,95],
    distractors=['moss_like_texture','soil_medium','dark_pot','pot_rim','metal_neutral_surface','green_weeds','crown_clutter'],
    label_rule='Image and binary mask share geometric transforms; appearance never removes valid foreground')


def foreground_appearance(rgb,mask,rng,mode):
    output=rgb.copy();pixels=rgb.astype(np.float32)
    gray=pixels.mean(axis=2)
    height,width=mask.shape
    field=np.array(Image.fromarray(rng.uniform(.7,1.25,(8,8)).astype(np.float32)).resize((width,height),Image.Resampling.BILINEAR))
    if mode=='healthy':new=pixels*rng.uniform(.75,1.25)
    else:
        colors={'pale':[.96,1.08,.75],'yellow_green':[1.15,1.13,.35],'chlorotic':[1.13,1.11,.82],'uneven':[.84,1.12,.6]}
        if mode not in colors:raise ValueError('Unknown tissue appearance')
        light=np.clip(gray*1.35+45,0,245)*field
        new=light[...,None]*np.array(colors[mode])[None,None,:]
        if mode=='uneven':new=.5*new+.5*pixels
    output[mask]=np.clip(new[mask],0,255).astype(np.uint8)
    return output


def background(size,rng):
    # Every scene contains several distractor types; generated objects have zero target label.
    texture=np.array(Image.fromarray(rng.normal(0,1,(32,32)).astype(np.float32)).resize((size,size),Image.Resampling.BICUBIC))
    fine=rng.normal(0,7,(size,size,1));base=np.array([70,60,45])+texture[...,None]*18+fine
    image=Image.fromarray(np.clip(base,0,255).astype(np.uint8));draw=ImageDraw.Draw(image)
    border=int(size*rng.uniform(.1,.24));draw.rectangle((0,0,size,border),fill=(160,162,159))
    for y in range(4,border,9):draw.line((0,y,size,y),fill=(137,140,136),width=1)
    center=(size//2,size//2);radius=int(size*rng.uniform(.28,.43))
    ellipse=(center[0]-radius,center[1]-radius,center[0]+radius,center[1]+radius)
    draw.ellipse(ellipse,fill=(30,31,28),outline=(67,66,62),width=max(2,size//32))
    inset=max(4,size//25);draw.ellipse(tuple(v+(inset if i<2 else -inset) for i,v in enumerate(ellipse)),fill=(56,43,29))
    for _ in range(5):
        mx,my=rng.integers(int(size*.25),int(size*.75),2);spread=size*rng.uniform(.025,.1)
        for _ in range(150):
            x,y=np.array([mx,my])+rng.normal(0,spread,2);r=int(rng.integers(1,max(3,size//90)))
            draw.ellipse((x-r,y-r,x+r,y+r),fill=(int(rng.integers(30,80)),int(rng.integers(75,145)),int(rng.integers(15,55))))
    for _ in range(12):
        x,y=rng.uniform(.16*size,.86*size,2);length=rng.uniform(.025,.14)*size;angle=rng.uniform(0,2*math.pi)
        dx,dy=math.cos(angle)*length,math.sin(angle)*length
        color=(int(rng.integers(30,80)),int(rng.integers(90,170)),int(rng.integers(20,70)))
        draw.line((x,y,x+dx,y+dy),fill=color,width=max(1,size//200))
        for t in (.2,.4,.6,.8):
            sx,sy=x+t*dx,y+t*dy
            for sign in (-1,1):
                nx,ny=-dy*.15*sign,dx*.15*sign
                draw.polygon([(sx,sy),(sx+nx+dx*.13,sy+ny+dy*.13),(sx+dx*.17,sy+dy*.17)],fill=color)
    for _ in range(10):
        x,y=rng.uniform(0,size,2);draw.line((x,y,x+rng.uniform(-50,50),y+rng.uniform(-50,50)),fill=(117,88,55),width=2)
    return np.array(image)


def augment(rgb,mask,seed,size=512,config=None):
    config=config or CONFIG;rng=np.random.default_rng(seed)
    mode=str(rng.choice(config['appearance_modes'],p=[.15,.25,.2,.25,.15]))
    rgb=foreground_appearance(rgb,mask,rng,mode);composite=bool(rng.random()<config['composite_probability'])
    if composite:
        plant,target,_=target_crop(rgb,mask,0)
        desired=int(size*rng.uniform(*config['foreground_fraction']));scale=desired/max(target.shape)
        width=max(1,round(target.shape[1]*scale));height=max(1,round(target.shape[0]*scale))
        plant=np.array(Image.fromarray(plant).resize((width,height),Image.Resampling.BILINEAR))
        target=np.array(Image.fromarray(target.astype(np.uint8)*255).resize((width,height),Image.Resampling.NEAREST))>0
        image=background(size,rng);label=np.zeros((size,size),bool)
        x=int(np.clip((size-width)/2+rng.uniform(-.08,.08)*size,0,size-width))
        y=int(np.clip((size-height)/2+rng.uniform(-.08,.08)*size,0,size-height))
        image[y:y+height,x:x+width][target]=plant[target];label[y:y+height,x:x+width]=target
    else:image,label,_=letterbox(rgb,mask,size)
    angle=math.radians(rng.uniform(*config['rotation_degrees']));scale=rng.uniform(.93,1.07);c,s=math.cos(angle)/scale,math.sin(angle)/scale
    cx=cy=size/2;tx,ty=rng.uniform(-.03,.03,2)*size
    coeff=(c,s,cx-c*cx-s*cy-tx,-s,c,cy+s*cx-c*cy-ty,*rng.uniform(*config['perspective_coefficient'],2))
    image=np.array(Image.fromarray(image).transform((size,size),Image.Transform.PERSPECTIVE,coeff,Image.Resampling.BILINEAR,fillcolor=(40,40,40)))
    label=np.array(Image.fromarray(label.astype(np.uint8)*255).transform((size,size),Image.Transform.PERSPECTIVE,coeff,Image.Resampling.NEAREST,fillcolor=0))>0
    # Exposure/white balance/shadowing are global photometry, preserving all surviving tissue labels.
    gain=rng.uniform(.78,1.25);wb=rng.uniform(.88,1.12,3);gamma=rng.uniform(.85,1.15)
    gradient=np.linspace(rng.uniform(.55,1),rng.uniform(.9,1.1),size,dtype=np.float32)
    image=np.clip(255*(image.astype(np.float32)/255)**gamma*gain*wb[None,None,:]*gradient[:,None,None],0,255).astype(np.uint8)
    blur=float(rng.uniform(0,.7))
    if blur>.25:image=np.array(Image.fromarray(image).filter(ImageFilter.GaussianBlur(blur)))
    sigma=float(rng.uniform(*config['noise_sigma']));image=np.clip(image.astype(np.float32)+rng.normal(0,sigma,image.shape),0,255).astype(np.uint8)
    quality=int(rng.integers(config['jpeg_quality'][0],config['jpeg_quality'][1]+1))
    if rng.random()<.5:
        stream=io.BytesIO();Image.fromarray(image).save(stream,format='JPEG',quality=quality)
        image=np.array(Image.open(io.BytesIO(stream.getvalue())).convert('RGB'))
    provenance=dict(scope=config['scope'],config_version=config['version'],seed=int(seed),appearance=mode,
        composited=composite,geometric_inverse_coefficients=list(coeff),gain=float(gain),white_balance=wb.tolist(),
        gamma=float(gamma),blur_radius=blur,noise_sigma=sigma,jpeg_quality=quality,
        target_pixels=int(label.sum()),label_policy=config['label_rule'])
    return image,label,provenance
