// One controller for image-space coordinates and gestures. Touch handling is
// scoped to the viewport; the rest of the page retains native scrolling.
globalThis.ImageViewport=class ImageViewport {
 constructor(wrapper,stage,options={}){
  this.wrapper=wrapper;this.stage=stage;this.options=options;this.scale=1;this.x=0;this.y=0;this.pointers=new Map();this.gesture=false;this.width=1;this.height=1;
  Object.assign(wrapper.style,{position:'relative',overflow:'hidden',touchAction:'none'});
  Object.assign(stage.style,{position:'absolute',left:'0',top:'0',margin:'0',maxWidth:'none',maxHeight:'none',transformOrigin:'0 0'});
  wrapper.addEventListener('pointerdown',e=>this.down(e));wrapper.addEventListener('pointermove',e=>this.move(e));
  wrapper.addEventListener('pointerup',e=>this.up(e));wrapper.addEventListener('pointercancel',e=>this.cancel(e));
  wrapper.addEventListener('lostpointercapture',e=>{if(this.pointers.has(e.pointerId))this.cancel(e);});
  this.viewWidth=wrapper.clientWidth;this.viewHeight=wrapper.clientHeight;
  if(globalThis.ResizeObserver){this.resizeObserver=new ResizeObserver(()=>this.resize());this.resizeObserver.observe(wrapper);}
 }
 setSize(width,height){this.abort();this.width=width;this.height=height;this.fit();}
 bounds(){const r=this.wrapper.getBoundingClientRect();return {left:r.left+(this.wrapper.clientLeft||0),top:r.top+(this.wrapper.clientTop||0),width:this.wrapper.clientWidth||r.width,height:this.wrapper.clientHeight||r.height};}
 point(event,element=this.stage){const r=element.getBoundingClientRect();if(!r.width||!r.height)return null;const x=(event.clientX-r.left)*this.width/r.width,y=(event.clientY-r.top)*this.height/r.height;return x<0||y<0||x>=this.width||y>=this.height?null:[x,y];}
 apply(){Object.assign(this.stage.style,{width:this.width*this.scale+'px',height:this.height*this.scale+'px',transform:`translate(${this.x}px, ${this.y}px)`});this.options.onChange?.(this);}
 fit(){const r=this.bounds(),w=this.wrapper.clientWidth||r.width,h=this.wrapper.clientHeight||r.height;this.viewWidth=w;this.viewHeight=h;this.fitted=true;this.scale=Math.min(w/this.width,h/this.height)||1;this.x=(w-this.width*this.scale)/2;this.y=(h-this.height*this.scale)/2;this.apply();}
 resize(){const w=this.wrapper.clientWidth,h=this.wrapper.clientHeight;if(!w||!h||w===this.viewWidth&&h===this.viewHeight)return;this.abort();if(this.fitted){this.fit();return;}this.x+=(w-(this.viewWidth||w))/2;this.y+=(h-(this.viewHeight||h))/2;this.viewWidth=w;this.viewHeight=h;this.apply();}
 reset(){this.fit();}
 zoom(scale,anchor){const r=this.bounds(),a=anchor||{clientX:r.left+r.width/2,clientY:r.top+r.height/2},x=a.clientX-r.left,y=a.clientY-r.top,next=Math.max(.02,Math.min(32,scale)),ratio=next/this.scale;this.fitted=false;this.x=x-(x-this.x)*ratio;this.y=y-(y-this.y)*ratio;this.scale=next;this.apply();}
 zoomBy(factor){this.zoom(this.scale*factor);}
 panBy(x,y){this.fitted=false;this.x+=x;this.y+=y;this.apply();}
 pair(){const [a,b]=[...this.pointers.values()];return {x:(a.x+b.x)/2,y:(a.y+b.y)/2,d:Math.max(1,Math.hypot(a.x-b.x,a.y-b.y))};}
 down(e){
  if(e.button!==undefined&&e.button!==0||this.options.canInteract?.()===false)return;
  e.preventDefault();this.pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});this.wrapper.setPointerCapture?.(e.pointerId);
  if(this.pointers.size===2){this.gesture=true;this.options.onGestureStart?.();this.pinch=this.pair();return;}
  if(this.pointers.size>2)return;
  this.pan=!!this.options.panMode?.();this.last={x:e.clientX,y:e.clientY};
  if(!this.pan&&!this.gesture)this.options.onPointerDown?.(e);
 }
 move(e){
  if(!this.pointers.has(e.pointerId)){this.options.onHover?.(e);return;}e.preventDefault();this.pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
  if(this.pointers.size>=2){const next=this.pair(),old=this.pinch;if(old){this.zoom(this.scale*next.d/old.d,{clientX:old.x,clientY:old.y});this.panBy(next.x-old.x,next.y-old.y);}this.pinch=next;return;}
  if(this.gesture)return;
  if(this.pan){this.panBy(e.clientX-this.last.x,e.clientY-this.last.y);this.last={x:e.clientX,y:e.clientY};}else this.options.onPointerMove?.(e);
 }
 up(e){if(!this.pointers.has(e.pointerId))return;e.preventDefault();const transformed=this.gesture||this.pan;if(!this.gesture&&!this.pan)this.options.onPointerUp?.(e);this.pointers.delete(e.pointerId);if(!this.pointers.size){this.gesture=false;this.pan=false;this.pinch=null;if(transformed)this.options.onGestureEnd?.();}else if(this.pointers.size>=2)this.pinch=this.pair();this.wrapper.releasePointerCapture?.(e.pointerId);}
 cancel(e){if(e&&!this.pointers.has(e.pointerId))return;this.options.onPointerCancel?.(e);this.pointers.clear();this.gesture=false;this.pan=false;this.pinch=null;}
 abort(){if(this.pointers.size)this.cancel();}
};
