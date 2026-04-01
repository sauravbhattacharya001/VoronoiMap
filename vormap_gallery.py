"""Voronoi Style Gallery — interactive HTML showcase of VoronoiMap art styles.

Generates a self-contained HTML page that demonstrates all visual Voronoi
art styles available in the project.  Each style gets a live, randomised
Canvas-rendered preview with controls to regenerate, adjust seed count,
and compare styles side by side.

Features
--------
- **14 art styles** rendered live in the browser via Canvas 2D
  (stained-glass, watercolour, low-poly, mosaic, pixel-art, halftone,
   stipple, cross-stitch, emboss, kaleidoscope, string-art, spiral,
   texture, gradient)
- **Interactive controls**: regenerate seeds, adjust cell count, toggle
  dark/light background, resize previews
- **Comparison mode**: select two styles and view them side-by-side with
  the same seed layout
- **Export**: download any preview as a PNG
- **Zero dependencies** — single self-contained HTML file
- **Responsive grid** — works on desktop and mobile

CLI usage
---------
::

    python vormap_gallery.py gallery.html
    python vormap_gallery.py gallery.html --title "My Voronoi Gallery"
    python vormap_gallery.py gallery.html --cols 3
    python vormap_gallery.py gallery.html --size 400
    python vormap_gallery.py gallery.html --dark

Programmatic usage
------------------
::

    import vormap_gallery
    html = vormap_gallery.generate(title="Art Gallery", cols=3, cell_size=300)
    vormap_gallery.save(html, "gallery.html")

"""

import argparse
import os
import sys


def generate(*, title="Voronoi Style Gallery", cols=3, cell_size=350, dark=False):
    """Return a self-contained HTML string with the interactive gallery."""

    bg = "#1a1a2e" if dark else "#f5f5f5"
    fg = "#e0e0e0" if dark else "#333"
    card_bg = "#16213e" if dark else "#fff"
    card_border = "#0f3460" if dark else "#ddd"
    accent = "#e94560"
    btn_bg = "#0f3460" if dark else "#4a6fa5"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',system-ui,sans-serif;background:{bg};color:{fg};min-height:100vh}}
header{{text-align:center;padding:2rem 1rem .5rem}}
header h1{{font-size:2rem;font-weight:700}}
header p{{opacity:.7;margin-top:.3rem;font-size:.95rem}}
.controls{{display:flex;flex-wrap:wrap;justify-content:center;gap:.6rem;padding:.8rem 1rem}}
.controls label{{font-size:.85rem;display:flex;align-items:center;gap:.3rem}}
.controls input[type=range]{{width:100px}}
.controls button,.controls select{{
  background:{btn_bg};color:#fff;border:none;padding:.4rem .9rem;border-radius:6px;
  cursor:pointer;font-size:.85rem;transition:opacity .2s}}
.controls button:hover{{opacity:.8}}
.grid{{display:grid;grid-template-columns:repeat({cols},1fr);gap:1.2rem;
  padding:1rem 2rem 2rem;max-width:{cols * (cell_size + 40)}px;margin:0 auto}}
@media(max-width:900px){{.grid{{grid-template-columns:repeat(2,1fr);padding:1rem}}}}
@media(max-width:550px){{.grid{{grid-template-columns:1fr}}}}
.card{{background:{card_bg};border:1px solid {card_border};border-radius:12px;
  overflow:hidden;transition:transform .2s,box-shadow .2s}}
.card:hover{{transform:translateY(-3px);box-shadow:0 8px 24px rgba(0,0,0,.15)}}
.card canvas{{display:block;width:100%;cursor:pointer}}
.card .info{{padding:.7rem .9rem;display:flex;justify-content:space-between;align-items:center}}
.card .info h3{{font-size:.95rem;font-weight:600}}
.card .info .btns{{display:flex;gap:.3rem}}
.card .info .btns button{{background:none;border:1px solid {card_border};
  color:{fg};padding:.25rem .5rem;border-radius:4px;cursor:pointer;font-size:.75rem}}
.card .info .btns button:hover{{background:{accent};color:#fff;border-color:{accent}}}
.compare-panel{{display:none;padding:1rem 2rem;text-align:center}}
.compare-panel.active{{display:block}}
.compare-panel .pair{{display:flex;justify-content:center;gap:1.5rem;flex-wrap:wrap;margin-top:.8rem}}
.compare-panel canvas{{border-radius:10px;border:2px solid {accent}}}
footer{{text-align:center;padding:1.5rem;opacity:.5;font-size:.8rem}}
</style>
</head>
<body>
<header>
  <h1>🎨 {_esc(title)}</h1>
  <p>Interactive gallery of Voronoi diagram art styles — click any preview to download PNG</p>
</header>

<div class="controls">
  <label>Seeds: <input type="range" id="seedSlider" min="8" max="120" value="30">
    <span id="seedVal">30</span></label>
  <label>Size: <input type="range" id="sizeSlider" min="200" max="600" value="{cell_size}">
    <span id="sizeVal">{cell_size}</span></label>
  <button id="regenAll">⟳ Regenerate All</button>
  <button id="toggleCompare">Compare Mode</button>
  <select id="compareA"></select>
  <select id="compareB"></select>
</div>

<div class="compare-panel" id="comparePanel">
  <h2>Side-by-Side Comparison</h2>
  <div class="pair">
    <div><canvas id="cmpA" width="{cell_size}" height="{cell_size}"></canvas><p id="cmpALabel"></p></div>
    <div><canvas id="cmpB" width="{cell_size}" height="{cell_size}"></canvas><p id="cmpBLabel"></p></div>
  </div>
</div>

<div class="grid" id="grid"></div>

<footer>Generated by <strong>vormap_gallery.py</strong> — VoronoiMap project</footer>

<script>
"use strict";
const STYLES=[
  {{name:"Stained Glass",key:"stainedglass",lead:3,palettes:[["#8B0000","#FFD700","#00008B","#006400","#FF4500","#4B0082"],["#E6194B","#3CB44B","#FFE119","#4363D8","#F58231","#911EB4"]]}},
  {{name:"Watercolour",key:"watercolor",bleed:8,palettes:[["#FF6B6B","#FEC89A","#FFD93D","#6BCB77","#4D96FF","#9B59B6"],["#F8B4B4","#FCDAB7","#B5EAD7","#C7CEEA","#E2C2F8","#FFE0AC"]]}},
  {{name:"Low Poly",key:"lowpoly",palettes:[["#2C3E50","#3498DB","#1ABC9C","#F39C12","#E74C3C","#9B59B6"],["#34495E","#2980B9","#27AE60","#F1C40F","#E67E22","#8E44AD"]]}},
  {{name:"Mosaic",key:"mosaic",gap:2,palettes:[["#264653","#2A9D8F","#E9C46A","#F4A261","#E76F51","#606C38"],["#023E8A","#0077B6","#0096C7","#00B4D8","#48CAE4","#90E0EF"]]}},
  {{name:"Pixel Art",key:"pixelart",px:6,palettes:[["#0F380F","#306230","#8BAC0F","#9BBC0F","#C4CFA1","#E0E0E0"],["#1A1C2C","#5D275D","#B13E53","#EF7D57","#FFCD75","#A7F070"]]}},
  {{name:"Halftone",key:"halftone",palettes:[["#000","#333","#666","#999","#CCC","#FFF"]]}},
  {{name:"Stipple",key:"stipple",dotR:1.5,palettes:[["#1a1a1a"]]}},
  {{name:"Cross Stitch",key:"crossstitch",stitch:8,palettes:[["#CC3333","#336699","#339966","#CC9933","#9933CC","#CC6633"],["#993333","#334466","#336644","#996633","#663399","#CC3366"]]}},
  {{name:"Emboss",key:"emboss",palettes:[["#B0B0B0","#C8C8C8","#A0A0A0","#D0D0D0","#909090","#E0E0E0"]]}},
  {{name:"Kaleidoscope",key:"kaleidoscope",segments:6,palettes:[["#FF1493","#00CED1","#FFD700","#7B68EE","#FF6347","#00FA9A"],["#FF69B4","#40E0D0","#FF4500","#8A2BE2","#32CD32","#FF8C00"]]}},
  {{name:"String Art",key:"stringart",palettes:[["#FF6B6B","#4ECDC4","#FFE66D","#95E1D3","#F38181","#AA96DA"]]}},
  {{name:"Spiral",key:"spiral",palettes:[["#2C3E50","#E74C3C","#3498DB","#2ECC71","#F39C12","#9B59B6"]]}},
  {{name:"Texture",key:"texture",palettes:[["#8D6E63","#A1887F","#BCAAA4","#D7CCC8","#795548","#6D4C41"]]}},
  {{name:"Gradient",key:"gradient",palettes:[["#667eea","#764ba2","#f093fb","#f5576c","#4facfe","#00f2fe"]]}}
];

let seeds=[], numSeeds=30, cellSize={cell_size};
const grid=document.getElementById("grid");
const selA=document.getElementById("compareA"), selB=document.getElementById("compareB");

function genSeeds(n,w,h){{let s=[];for(let i=0;i<n;i++)s.push([Math.random()*w,Math.random()*h]);return s}}
function voronoi(x,y,seeds){{let mi=0,md=1e9;for(let i=0;i<seeds.length;i++){{let dx=x-seeds[i][0],dy=y-seeds[i][1],d=dx*dx+dy*dy;if(d<md){{md=d;mi=i}}}}return[mi,Math.sqrt(md)]}}
function distToEdge(x,y,seeds){{let ds=[];for(let i=0;i<seeds.length;i++){{let dx=x-seeds[i][0],dy=y-seeds[i][1];ds.push(dx*dx+dy*dy)}}ds.sort((a,b)=>a-b);return Math.sqrt(ds[1])-Math.sqrt(ds[0])}}

const renderers={{
  stainedglass(ctx,w,h,s,st){{
    let img=ctx.createImageData(w,h),d=img.data,pal=st.palettes[0],lw=st.lead;
    for(let y=0;y<h;y++)for(let x=0;x<w;x++){{
      let[ci,dist]=voronoi(x,y,s),de=distToEdge(x,y,s),o=(y*w+x)*4;
      if(de<lw){{d[o]=30;d[o+1]=30;d[o+2]=30;d[o+3]=255}}
      else{{let c=hexRGB(pal[ci%pal.length]),bright=.7+.3*Math.min(de/30,1);
        d[o]=c[0]*bright|0;d[o+1]=c[1]*bright|0;d[o+2]=c[2]*bright|0;d[o+3]=230}}
    }}ctx.putImageData(img,0,0)}},
  watercolor(ctx,w,h,s,st){{
    let img=ctx.createImageData(w,h),d=img.data,pal=st.palettes[0],bl=st.bleed;
    for(let y=0;y<h;y++)for(let x=0;x<w;x++){{
      let[ci]=voronoi(x,y,s),de=distToEdge(x,y,s),o=(y*w+x)*4;
      let c=hexRGB(pal[ci%pal.length]),a=Math.min(1,de/bl)*(.75+Math.random()*.25);
      let paper=245+Math.random()*10|0;
      d[o]=c[0]*a+paper*(1-a)|0;d[o+1]=c[1]*a+paper*(1-a)|0;d[o+2]=c[2]*a+paper*(1-a)|0;d[o+3]=255}}
    ctx.putImageData(img,0,0)}},
  lowpoly(ctx,w,h,s,st){{
    ctx.clearRect(0,0,w,h);let pal=st.palettes[0];
    for(let i=0;i<s.length;i++){{
      let neighbors=[];for(let j=0;j<s.length;j++)if(i!==j)neighbors.push({{idx:j,d:Math.hypot(s[i][0]-s[j][0],s[i][1]-s[j][1])}});
      neighbors.sort((a,b)=>a.d-b.d);let near=neighbors.slice(0,6);
      near.sort((a,b)=>Math.atan2(s[a.idx][1]-s[i][1],s[a.idx][0]-s[i][0])-Math.atan2(s[b.idx][1]-s[i][1],s[b.idx][0]-s[i][0]));
      for(let k=0;k<near.length;k++){{let n1=near[k].idx,n2=near[(k+1)%near.length].idx;
        ctx.beginPath();ctx.moveTo(s[i][0],s[i][1]);ctx.lineTo(s[n1][0],s[n1][1]);ctx.lineTo(s[n2][0],s[n2][1]);ctx.closePath();
        let avg=[(s[i][0]+s[n1][0]+s[n2][0])/3,(s[i][1]+s[n1][1]+s[n2][1])/3];
        let shade=.6+.4*(avg[1]/h);let c=hexRGB(pal[i%pal.length]);
        ctx.fillStyle=`rgb(${{c[0]*shade|0}},${{c[1]*shade|0}},${{c[2]*shade|0}})`;ctx.fill();
        ctx.strokeStyle="rgba(0,0,0,.08)";ctx.lineWidth=.5;ctx.stroke()}}
    }}}},
  mosaic(ctx,w,h,s,st){{
    let img=ctx.createImageData(w,h),d=img.data,pal=st.palettes[0],gap=st.gap;
    for(let y=0;y<h;y++)for(let x=0;x<w;x++){{
      let[ci]=voronoi(x,y,s),de=distToEdge(x,y,s),o=(y*w+x)*4;
      if(de<gap){{d[o]=40;d[o+1]=40;d[o+2]=40;d[o+3]=255}}
      else{{let c=hexRGB(pal[ci%pal.length]);d[o]=c[0];d[o+1]=c[1];d[o+2]=c[2];d[o+3]=255}}
    }}ctx.putImageData(img,0,0)}},
  pixelart(ctx,w,h,s,st){{
    let px=st.px,pal=st.palettes[0];ctx.clearRect(0,0,w,h);
    for(let y=0;y<h;y+=px)for(let x=0;x<w;x+=px){{
      let[ci]=voronoi(x+px/2,y+px/2,s);let c=hexRGB(pal[ci%pal.length]);
      ctx.fillStyle=`rgb(${{c[0]}},${{c[1]}},${{c[2]}})`;ctx.fillRect(x,y,px-1,px-1)}}
  }},
  halftone(ctx,w,h,s,st){{
    ctx.fillStyle="#fff";ctx.fillRect(0,0,w,h);ctx.fillStyle="#000";
    let step=6;for(let y=0;y<h;y+=step)for(let x=0;x<w;x+=step){{
      let[ci,dist]=voronoi(x,y,s);let r=Math.max(.5,step/2*(1-dist/Math.max(w,h)*2));
      ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.fill()}}
  }},
  stipple(ctx,w,h,s,st){{
    ctx.fillStyle="#f8f8f0";ctx.fillRect(0,0,w,h);ctx.fillStyle="#1a1a1a";
    for(let i=0;i<w*h/15;i++){{let x=Math.random()*w,y=Math.random()*h;
      let[ci,dist]=voronoi(x,y,s);let p=Math.max(0,1-dist/(Math.max(w,h)*.3));
      if(Math.random()<p){{ctx.beginPath();ctx.arc(x,y,st.dotR,0,Math.PI*2);ctx.fill()}}}}
  }},
  crossstitch(ctx,w,h,s,st){{
    let sz=st.stitch,pal=st.palettes[0];ctx.fillStyle="#f5e6d0";ctx.fillRect(0,0,w,h);
    for(let y=0;y<h;y+=sz)for(let x=0;x<w;x+=sz){{
      let[ci]=voronoi(x+sz/2,y+sz/2,s);let c=pal[ci%pal.length];ctx.strokeStyle=c;ctx.lineWidth=1.5;
      ctx.beginPath();ctx.moveTo(x+1,y+1);ctx.lineTo(x+sz-1,y+sz-1);ctx.moveTo(x+sz-1,y+1);ctx.lineTo(x+1,y+sz-1);ctx.stroke()}}
  }},
  emboss(ctx,w,h,s,st){{
    let img=ctx.createImageData(w,h),d=img.data,pal=st.palettes[0];
    for(let y=0;y<h;y++)for(let x=0;x<w;x++){{
      let[ci]=voronoi(x,y,s),de=distToEdge(x,y,s),o=(y*w+x)*4;
      let c=hexRGB(pal[ci%pal.length]);let hi=Math.min(1,de/15);
      let[ci2]=voronoi(x+1,y+1,s);let edge=ci!==ci2?30:0;
      d[o]=Math.min(255,c[0]*hi+edge)|0;d[o+1]=Math.min(255,c[1]*hi+edge)|0;d[o+2]=Math.min(255,c[2]*hi+edge)|0;d[o+3]=255}}
    ctx.putImageData(img,0,0)}},
  kaleidoscope(ctx,w,h,s,st){{
    let seg=st.segments,pal=st.palettes[0],cx=w/2,cy=h/2;
    ctx.clearRect(0,0,w,h);
    let img=ctx.createImageData(w,h),d=img.data;
    for(let y=0;y<h;y++)for(let x=0;x<w;x++){{
      let dx=x-cx,dy=y-cy,a=Math.atan2(dy,dx),r=Math.hypot(dx,dy);
      let sa=2*Math.PI/seg,ma=((a%sa)+sa)%sa;if(ma>sa/2)ma=sa-ma;
      let mx=cx+r*Math.cos(ma),my=cy+r*Math.sin(ma);
      let[ci]=voronoi(mx,my,s);let c=hexRGB(pal[ci%pal.length]),o=(y*w+x)*4;
      d[o]=c[0];d[o+1]=c[1];d[o+2]=c[2];d[o+3]=255}}
    ctx.putImageData(img,0,0)}},
  stringart(ctx,w,h,s,st){{
    ctx.fillStyle="#111";ctx.fillRect(0,0,w,h);let pal=st.palettes[0];
    for(let i=0;i<s.length;i++){{let near=[];
      for(let j=0;j<s.length;j++)if(i!==j)near.push({{j,d:Math.hypot(s[i][0]-s[j][0],s[i][1]-s[j][1])}});
      near.sort((a,b)=>a.d-b.d);
      for(let k=0;k<Math.min(4,near.length);k++){{ctx.strokeStyle=pal[i%pal.length]+"60";ctx.lineWidth=.7;
        ctx.beginPath();ctx.moveTo(s[i][0],s[i][1]);ctx.lineTo(s[near[k].j][0],s[near[k].j][1]);ctx.stroke()}}}}
  }},
  spiral(ctx,w,h,s,st){{
    ctx.fillStyle="#f5f0eb";ctx.fillRect(0,0,w,h);let pal=st.palettes[0];
    for(let i=0;i<s.length;i++){{ctx.strokeStyle=pal[i%pal.length];ctx.lineWidth=1;ctx.beginPath();
      for(let t=0;t<50;t+=.3){{let r=t*1.5,x=s[i][0]+r*Math.cos(t),y=s[i][1]+r*Math.sin(t);
        t===0?ctx.moveTo(x,y):ctx.lineTo(x,y)}}ctx.stroke()}}
  }},
  texture(ctx,w,h,s,st){{
    let img=ctx.createImageData(w,h),d=img.data,pal=st.palettes[0];
    for(let y=0;y<h;y++)for(let x=0;x<w;x++){{
      let[ci]=voronoi(x,y,s),o=(y*w+x)*4;let c=hexRGB(pal[ci%pal.length]);
      let n=(Math.sin(x*.3)*Math.cos(y*.3)+1)/2*.3;
      d[o]=Math.min(255,c[0]+n*60)|0;d[o+1]=Math.min(255,c[1]+n*40)|0;d[o+2]=Math.min(255,c[2]+n*20)|0;d[o+3]=255}}
    ctx.putImageData(img,0,0)}},
  gradient(ctx,w,h,s,st){{
    let pal=st.palettes[0];ctx.clearRect(0,0,w,h);
    for(let i=0;i<s.length;i++){{let c=hexRGB(pal[i%pal.length]);
      let g=ctx.createRadialGradient(s[i][0],s[i][1],0,s[i][0],s[i][1],Math.max(w,h)*.3);
      g.addColorStop(0,`rgba(${{c[0]}},${{c[1]}},${{c[2]}},.7)`);g.addColorStop(1,`rgba(${{c[0]}},${{c[1]}},${{c[2]}},0)`);
      ctx.fillStyle=g;ctx.fillRect(0,0,w,h)}}
  }}
}};

function hexRGB(h){{h=h.replace("#","");if(h.length===3)h=h[0]+h[0]+h[1]+h[1]+h[2]+h[2];
  return[parseInt(h.slice(0,2),16),parseInt(h.slice(2,4),16),parseInt(h.slice(4,6),16)]}}

function renderCard(st,s,sz){{
  let c=document.createElement("canvas");c.width=sz;c.height=sz;
  let ctx=c.getContext("2d");if(renderers[st.key])renderers[st.key](ctx,sz,sz,s,st);return c}}

function buildGrid(){{
  grid.innerHTML="";seeds=genSeeds(numSeeds,cellSize,cellSize);
  STYLES.forEach((st,i)=>{{
    let card=document.createElement("div");card.className="card";card.dataset.idx=i;
    let cv=renderCard(st,seeds,cellSize);card.appendChild(cv);
    let info=document.createElement("div");info.className="info";
    info.innerHTML=`<h3>${{st.name}}</h3><div class="btns"><button class="regen" title="Regenerate">⟳</button><button class="dl" title="Download PNG">⬇</button></div>`;
    card.appendChild(info);grid.appendChild(card);
    cv.onclick=()=>dlCanvas(cv,st.key);
    info.querySelector(".regen").onclick=()=>{{let ns=genSeeds(numSeeds,cellSize,cellSize);
      let nc=renderCard(st,ns,cellSize);card.replaceChild(nc,card.firstChild);
      nc.onclick=()=>dlCanvas(nc,st.key)}};
    info.querySelector(".dl").onclick=()=>dlCanvas(card.querySelector("canvas"),st.key)}})}}

function dlCanvas(cv,name){{let a=document.createElement("a");a.download="voronoi-"+name+".png";a.href=cv.toDataURL();a.click()}}

document.getElementById("seedSlider").oninput=function(){{numSeeds=+this.value;document.getElementById("seedVal").textContent=this.value;buildGrid()}};
document.getElementById("sizeSlider").oninput=function(){{cellSize=+this.value;document.getElementById("sizeVal").textContent=this.value;buildGrid()}};
document.getElementById("regenAll").onclick=buildGrid;

STYLES.forEach((st,i)=>{{selA.innerHTML+=`<option value="${{i}}">${{st.name}}</option>`;selB.innerHTML+=`<option value="${{i}}" ${{i===1?"selected":""}}>${{st.name}}</option>`}});

let compareOn=false;
document.getElementById("toggleCompare").onclick=function(){{compareOn=!compareOn;
  document.getElementById("comparePanel").classList.toggle("active",compareOn);
  this.textContent=compareOn?"Hide Compare":"Compare Mode";if(compareOn)renderCompare()}};
function renderCompare(){{
  let ia=+selA.value,ib=+selB.value,sa=STYLES[ia],sb=STYLES[ib];
  let shared=genSeeds(numSeeds,cellSize,cellSize);
  let cA=document.getElementById("cmpA"),cB=document.getElementById("cmpB");
  cA.width=cB.width=cellSize;cA.height=cB.height=cellSize;
  if(renderers[sa.key])renderers[sa.key](cA.getContext("2d"),cellSize,cellSize,shared,sa);
  if(renderers[sb.key])renderers[sb.key](cB.getContext("2d"),cellSize,cellSize,shared,sb);
  document.getElementById("cmpALabel").textContent=sa.name;
  document.getElementById("cmpBLabel").textContent=sb.name}}
selA.onchange=selB.onchange=()=>{{if(compareOn)renderCompare()}};

buildGrid();
</script>
</body>
</html>"""


def _esc(s):
    """Escape HTML special characters."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def save(html, path):
    """Write gallery HTML to a file."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return os.path.abspath(path)


def main():
    parser = argparse.ArgumentParser(
        description="Generate an interactive Voronoi style gallery as a self-contained HTML file."
    )
    parser.add_argument("output", help="Output HTML file path")
    parser.add_argument("--title", default="Voronoi Style Gallery",
                        help="Gallery title (default: 'Voronoi Style Gallery')")
    parser.add_argument("--cols", type=int, default=3,
                        help="Number of grid columns (default: 3)")
    parser.add_argument("--size", type=int, default=350,
                        help="Canvas preview size in pixels (default: 350)")
    parser.add_argument("--dark", action="store_true",
                        help="Use dark theme")

    args = parser.parse_args()
    html = generate(title=args.title, cols=args.cols, cell_size=args.size, dark=args.dark)
    out = save(html, args.output)
    print(f"Gallery saved to {out}")
    print(f"  {14} styles • {'dark' if args.dark else 'light'} theme • {args.cols} columns")


if __name__ == "__main__":
    main()
