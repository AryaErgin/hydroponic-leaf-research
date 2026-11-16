// Native-coordinate picker. The server revalidates with the existing reviewed
// pot calibrator; this preview never changes measurement mathematics.
export class PotCalibration {
  constructor(canvas,changed,wrapper=canvas.parentElement){
    this.canvas=canvas;this.changed=changed;this.points=[];this.image=null;this.file=null;this.serial=0;this.panMode=false;
    if(globalThis.ImageViewport&&wrapper?.addEventListener){
      this.viewport=new globalThis.ImageViewport(wrapper,canvas,{canInteract:()=>!!this.image&&!this.locked,panMode:()=>this.panMode,onChange:()=>this.draw(),onGestureStart:()=>this.cancelPoint(),onPointerDown:e=>this.startPoint(e),onPointerMove:e=>this.movePoint(e),onPointerUp:e=>this.finishPoint(e),onPointerCancel:()=>this.cancelPoint()});
    }else canvas.addEventListener('pointerdown',event=>this.click(event));
  }
  async load(file){
    if(!file)return;const serial=++this.serial,url=URL.createObjectURL(file),image=new Image();
    let timer;try{image.src=url;await Promise.race([image.decode(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Scale preview timed out. Retry calibration or continue with pixels-only analysis.')),20000);})]);if(serial!==this.serial)return;
      this.viewport?.abort();this.file=file;this.image=image;this.canvas.width=image.naturalWidth;this.canvas.height=image.naturalHeight;this.points=[];this.viewport?.setSize(image.naturalWidth,image.naturalHeight);this.redo();
    }finally{clearTimeout(timer);URL.revokeObjectURL(url);}
  }
  clear(){++this.serial;this.viewport?.abort();this.file=null;this.image=null;this.points=[];this.canvas.getContext('2d').clearRect?.(0,0,this.canvas.width,this.canvas.height);this.changed();}
  get locked(){return !!this._locked;}
  set locked(value){this._locked=!!value;if(value)this.viewport?.abort();}
  redo(){if(this.locked)return;this.viewport?.abort();this.points=[];this.pointDraft=null;this.draw();this.changed();}
  undoLastPoint(){if(this.locked)return;this.viewport?.abort();this.points.pop();this.draw();this.changed();}
  fit(){this.viewport?.fit();}
  zoom(scale){this.viewport?.zoom(scale);}
  startPoint(event){
    const point=this.viewport.point(event);if(!point)return;
    const r=this.canvas.getBoundingClientRect(),hit=24*this.canvas.width/r.width;
    let at=-1,best=hit;this.points.forEach((p,i)=>{const d=Math.hypot(p[0]-point[0],p[1]-point[1]);if(d<best){at=i;best=d;}});
    if(at<0&&this.points.length===2)return;
    this.pointDraft={before:this.points.map(p=>[...p]),at:at<0?this.points.length:at};this.points[this.pointDraft.at]=point;this.draw();
  }
  movePoint(event){if(!this.pointDraft)return;const point=this.viewport.point(event);if(point){this.points[this.pointDraft.at]=point;this.draw();}}
  finishPoint(event){if(!this.pointDraft)return;this.movePoint(event);this.pointDraft=null;this.draw();this.changed();}
  cancelPoint(){if(!this.pointDraft)return;this.points=this.pointDraft.before;this.pointDraft=null;this.draw();}
  click(event){
    if(!this.image||this.locked||this.points.length===2)return;
    const r=this.canvas.getBoundingClientRect();
    const x=(event.clientX-r.left)*this.canvas.width/r.width,y=(event.clientY-r.top)*this.canvas.height/r.height;
    if(x<0||y<0||x>=this.canvas.width||y>=this.canvas.height)return;
    this.points.push([Math.min(this.canvas.width-1,x),Math.min(this.canvas.height-1,y)]);this.draw();this.changed();
  }
  distance(){return this.points.length===2?Math.hypot(this.points[1][0]-this.points[0][0],this.points[1][1]-this.points[0][1]):null;}
  draw(){
    if(!this.image)return;const c=this.canvas.getContext('2d');c.drawImage(this.image,0,0);
    const scale=this.viewport?.scale||this.canvas.getBoundingClientRect().width/this.canvas.width||1;
    c.strokeStyle='#00e5ff';c.fillStyle='#00e5ff';c.lineWidth=3/scale;
    this.points.forEach(([x,y],i)=>{c.beginPath();c.arc(x,y,9/scale,0,2*Math.PI);c.fill();c.strokeStyle='#153a31';c.lineWidth=2/scale;c.stroke();c.strokeStyle='#00e5ff';c.lineWidth=3/scale;if(c.fillText){c.font=`bold ${13/scale}px system-ui`;c.textAlign='center';c.textBaseline='middle';c.fillStyle='#153a31';c.fillText(String(i+1),x,y);c.fillStyle='#00e5ff';}});
    if(this.points.length===2){c.beginPath();c.moveTo(...this.points[0]);c.lineTo(...this.points[1]);c.stroke();}
  }
}
