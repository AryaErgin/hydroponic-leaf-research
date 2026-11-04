"""Standalone SVG/PNG scientific charts using available NumPy/Pillow.

Matplotlib installation was blocked by network policy. Geometry is shared by
the vector and raster export; no curve fitting/smoothing or interpolation.
"""
from html import escape
from PIL import Image, ImageDraw
from .exp005 import font


class Canvas:
    def __init__(self,width,height):
        self.width,self.height=width,height
        self.im=Image.new('RGB',(width,height),'white');self.draw=ImageDraw.Draw(self.im)
        self.svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">', '<rect width="100%" height="100%" fill="white"/>']
    def text(self,x,y,s,size=18,color='#222222'):
        self.draw.text((x,y),str(s),font=font(size),fill=color)
        self.svg.append(f'<text x="{x}" y="{y+size}" font-family="Arial,sans-serif" font-size="{size}" fill="{color}">{escape(str(s))}</text>')
    def line(self,points,color='#cccccc',width=1):
        if len(points)<2:return
        self.draw.line(points,fill=color,width=width)
        p=' '.join(f'{x:.2f},{y:.2f}' for x,y in points)
        self.svg.append(f'<polyline points="{p}" fill="none" stroke="{color}" stroke-width="{width}"/>')
    def point(self,x,y,color='#2065a6',radius=4):
        self.draw.ellipse((x-radius,y-radius,x+radius,y+radius),fill=color)
        self.svg.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius}" fill="{color}"/>')
    def save(self,path):
        if path.with_suffix('.png').exists() or path.with_suffix('.svg').exists():raise FileExistsError(path)
        self.im.save(path.with_suffix('.png'))
        path.with_suffix('.svg').write_text('\n'.join(self.svg+['</svg>']),encoding='utf-8')


def axes(canvas,box,xlim,ylim,title,xlabel,ylabel):
    left,top,width,height=box
    canvas.text(left,top-30,title,20)
    for i in range(5):
        y=top+height*i/4;v=ylim[1]-(ylim[1]-ylim[0])*i/4
        canvas.line([(left,y),(left+width,y)])
        canvas.text(left-65,y-10,f'{v:,.0f}',15)
    for i in range(4):
        x=left+width*i/3;v=xlim[0]+(xlim[1]-xlim[0])*i/3
        canvas.text(x-12,top+height+8,f'{v:,.0f}',15)
    canvas.line([(left,top),(left,top+height),(left+width,top+height)],'#555555',2)
    canvas.text(left+width*.20,top+height+32,xlabel,16)
    canvas.text(left,top-53,ylabel,16)
    return lambda x,y:(left+width*(x-xlim[0])/(xlim[1]-xlim[0]),top+height-height*(y-ylim[0])/(ylim[1]-ylim[0]))


def trajectory_chart(path,positions,daily,within,title):
    c=Canvas(1860,2300);c.text(30,12,title,27)
    c.text(30,50,'Stored dates: 2022-02-21 to 03-23; actual year unresolved (README/environment 2024). DAT is source-relative.',19)
    c.text(30,80,'Blue: flagged raw | green: original QC accepted | red: QC withheld | gray: all-capture daily min/max and median.',19)
    c.text(30,108,'Fixed-cell pixels; overlap/frame censoring unresolved. Blank segments preserve missing presence and QC gaps.',19)
    ymax=max(r['raw_pixel_area'] for rows in daily.values() for r in rows)*1.05
    for i,p in enumerate(positions):
        x=90+(i%3)*615;y=205+(i//3)*345
        xy=axes(c,(x,y,490,220),(1,31),(0,max(1,ymax)),p,'relative DAT','projected cell area (px)')
        for r in within[p]:
            c.line([xy(r['dat'],r['min_area']),xy(r['dat'],r['max_area'])],'#dddddd',3)
            c.point(*xy(r['dat'],r['median_area']),'#aaaaaa',2)
        prev=None;prevqc=None
        for r in daily[p]:
            valid=r['presence']=='present';point=xy(r['dat'],r['raw_pixel_area'])
            if valid:
                if prev is not None and r['dat']==prev[0]+1:c.line([prev[1],point],'#2065a6',2)
                c.point(*point,'#c74040' if r['qc_flag'] else '#2065a6',4);prev=(r['dat'],point)
            else:prev=None
            if valid and not r['qc_flag']:
                if prevqc is not None and r['dat']==prevqc[0]+1:c.line([prevqc[1],point],'#138044',3)
                c.point(*point,'#138044',3);prevqc=(r['dat'],point)
            else:prevqc=None
    c.text(30,2270,'No masks/physical scale: this chart cannot establish segmentation accuracy, cm2, individual leaf ownership or causality.',18)
    c.save(path)


def scatter_chart(path,joined,title,position=None):
    c=Canvas(1100,850);c.text(25,10,title,25)
    c.text(25,47,'Fixed-cell area vs supplied fresh mass; repeated cohort/time dependence, no causal inference.',18)
    xlim=(0,max(1,max(r['raw_pixel_area'] for r in joined)*1.08))
    ylim=(0,max(r['fresh_mass_g'] for r in joined)*1.08)
    xy=axes(c,(115,150,910,555),xlim,ylim,position or f'{len(joined)} source-matched day/position pairs',
            'projected cell area (pixels)','fresh mass (g)')
    for r in joined:c.point(*xy(r['raw_pixel_area'],r['fresh_mass_g']),'#c74040' if r['qc_flag'] else '#2065a6',4)
    c.text(115,775,'Blue: original QC accepted. Red: withheld diagnostic raw area. No fitted calibration/regression.',18)
    c.text(115,806,'Stored image/biomass year 2022; README/environment 2024 conflict remains unresolved.',17)
    c.save(path)


def capture_chart(path,rows,position):
    c=Canvas(1100,850);c.text(25,10,f'EXP-005: {position}, all 727 capture-order signals',25)
    c.text(25,47,'Raw fixed-cell foreground, including removal/handling events. Non-flagged plant presence is unverified.',18)
    xy=axes(c,(115,150,910,555),(1,727),(0,max(1,max(r['raw_pixel_area'] for r in rows)*1.08)),
        'Every capture retained; no smoothing','capture order (not clock hours)','projected cell area (pixels)')
    c.line([xy(r['serial'],r['raw_pixel_area']) for r in rows],'#bbbbbb',1)
    for r in rows:c.point(*xy(r['serial'],r['raw_pixel_area']),'#c74040' if r['qc_flag'] else '#2065a6',2)
    for r in rows:
        if r['biomass_flagged']:c.point(*xy(r['serial'],r['raw_pixel_area']),'#000000',4)
    c.text(115,775,'Blue: QC accepted; red: withheld; black: designated weighing image. Empty controls are diagnostic.',18)
    c.text(115,806,'Stored dates 2022 versus documented 2024 unresolved; no segmentation masks/physical scale.',17)
    c.save(path)
