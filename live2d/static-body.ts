// Static original body under a genuine Cubism head. No replacement blink animation.
export class StaticBody {
  private image: HTMLCanvasElement;
  private target: HTMLCanvasElement;
  private lastView='';
  constructor(source: ImageBitmap, head: ImageBitmap, top: number, outline:number[][]) {
    if(source.width!==head.width||source.height!==head.height||top<0||top>=source.height)
      throw new Error('身体与头部遮罩必须使用相同原画坐标');
    this.image=document.createElement('canvas');
    this.image.width=source.width;this.image.height=source.height;
    const ctx=this.image.getContext('2d')!;
    if(!Array.isArray(outline)||outline.length<3||outline.some(p=>p.length!==2||p.some(v=>!Number.isFinite(v))))
      throw new Error('静态身体缺少有效轮廓');
    ctx.beginPath();outline.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.closePath();ctx.clip();
    ctx.drawImage(source,0,0);
    // Never retain original eyes, mouth or forehead behind the moving eye shapes.
    ctx.clearRect(0,0,source.width,top);
    ctx.globalCompositeOperation='destination-out';ctx.drawImage(head,0,0);
    this.target=document.createElement('canvas');this.target.id='staticBody';
    this.target.setAttribute('aria-label','原画静态身体，未绑定动作');
    document.querySelector('#stage')!.prepend(this.target);
  }
  draw(matrix:any, info:any, width:number, height:number) {
    const view=JSON.stringify([width,height,Array.from(matrix.getArray())]);
    if(view===this.lastView)return;
    this.lastView=view;
    this.target.width=width;this.target.height=height;
    const x0=-info.CanvasOriginX/info.PixelsPerUnit;
    const y0=info.CanvasOriginY/info.PixelsPerUnit;
    const left=(1+matrix.transformX(x0))*width/2;
    const top=(1-matrix.transformY(y0))*height/2;
    const right=(1+matrix.transformX(x0+info.CanvasWidth/info.PixelsPerUnit))*width/2;
    const bottom=(1-matrix.transformY(y0-info.CanvasHeight/info.PixelsPerUnit))*height/2;
    const ctx=this.target.getContext('2d')!;
    ctx.drawImage(this.image,left,top,right-left,bottom-top);
  }
}
