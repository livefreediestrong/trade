/* Bright-star diagrams from a pinned SIMBAD ICRS/J2000 coordinate snapshot.
   Layout is illustrative, not a current all-sky view or an Apache star tradition. */
(() => {
 'use strict';
 const catalog=[
  {id:'orion',label:'Orion · bright-star outline',source:'https://science.nasa.gov/asset/hubble/orion-constellation/',stars:[
   ['Betelgeuse',88.792939,7.407064],['Bellatrix',81.282764,6.349703],['Mintaka',83.001667,-.299095],['Alnilam',84.053389,-1.201919],['Alnitak',85.189694,-1.942574],['Saiph',86.939120,-9.669605],['Rigel',78.634467,-8.201638]],lines:[[0,1],[1,2],[2,3],[3,4],[4,0],[4,5],[5,6],[6,2]]},
  {id:'cassiopeia',label:'Cassiopeia · W pattern',source:'https://www.jpl.nasa.gov/images/pia15256-a-royal-celebration/',stars:[
   ['Caph',2.294522,59.149781],['Schedar',10.126846,56.537329],['Gamma Cassiopeiae',14.177213,60.716740],['Ruchbah',21.453964,60.235284],['Segin',28.598892,63.670100]],lines:[[0,1],[1,2],[2,3],[3,4]]},
  {id:'dipper',label:'Big Dipper · asterism in Ursa Major',source:'https://science.nasa.gov/solar-system/what-are-asterisms/',stars:[
   ['Dubhe',165.931965,61.751035],['Merak',165.460332,56.382434],['Phecda',178.457697,53.694760],['Megrez',183.856499,57.032617],['Alioth',193.507290,55.959823],['Mizar',200.981419,54.925352],['Alkaid',206.885157,49.313267]],lines:[[0,1],[1,2],[2,3],[3,0],[3,4],[4,5],[5,6]]}
 ];
 function project(stars){
  const rad=Math.PI/180,ra0=stars.reduce((n,s)=>n+s[1],0)/stars.length*rad,dec0=stars.reduce((n,s)=>n+s[2],0)/stars.length*rad;
  const points=stars.map(([name,ra,dec])=>{
   const d=dec*rad,a=ra*rad-ra0,c=Math.sin(dec0)*Math.sin(d)+Math.cos(dec0)*Math.cos(d)*Math.cos(a);
   if(c<=0)throw Error('Diagram spans beyond a projection hemisphere');
   return {name,x:-Math.cos(d)*Math.sin(a)/c,y:-(Math.cos(dec0)*Math.sin(d)-Math.sin(dec0)*Math.cos(d)*Math.cos(a))/c};
  });
  const xs=points.map(p=>p.x),ys=points.map(p=>p.y),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys);
  const scale=Math.min(250/(xmax-xmin||1),180/(ymax-ymin||1));
  return points.map(p=>({name:p.name,x:150+(p.x-(xmin+xmax)/2)*scale,y:112+(p.y-(ymin+ymax)/2)*scale}));
 }
 function render(target,study=false){
  if(!target)return;const ns='http://www.w3.org/2000/svg';
  const el=(name,attrs)=>{const n=document.createElementNS(ns,name);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,String(v));return n;};
  target.replaceChildren();
  for(const entry of catalog){
   const svg=el('svg',{viewBox:'0 0 300 250',preserveAspectRatio:'xMidYMid meet',class:'sky-constellation '+entry.id,role:'img','aria-label':entry.label+'; schematic chart, not current local sky'});
   const title=el('title',{});title.textContent=entry.label;svg.append(title);
   const points=project(entry.stars);
   for(const [i,j] of entry.lines)svg.append(el('line',{x1:points[i].x,y1:points[i].y,x2:points[j].x,y2:points[j].y,class:'sky-star-line'}));
   points.forEach(p=>{const circle=el('circle',{cx:p.x,cy:p.y,r:study?2.6:1.8,class:'sky-real-star','data-star':p.name});const label=el('title',{});label.textContent=p.name;circle.append(label);svg.append(circle);});
   const name=el('text',{x:150,y:240,'text-anchor':'middle',class:'sky-pattern-label'});name.textContent=entry.label;svg.append(name);target.append(svg);
  }
 }
 if(typeof module==='object'&&module.exports){module.exports={catalog,project};return;}
 window.DeskConstellations={catalog,project,render};
 render(document.getElementById('constellation-study'),true);
})();
