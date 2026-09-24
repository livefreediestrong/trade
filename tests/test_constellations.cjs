const assert=require('node:assert/strict');
const {catalog,project}=require('../static/constellations.js');
assert.equal(catalog.length,3);
assert.equal(new Set(catalog.flatMap(c=>c.stars.map(s=>s[0]))).size,19);
for(const c of catalog){
 for(const [name,ra,dec] of c.stars){assert.ok(name);assert.ok(ra>=0&&ra<360&&dec>=-90&&dec<=90);}
 for(const line of c.lines)for(const index of line)assert.ok(Number.isInteger(index)&&index>=0&&index<c.stars.length);
 for(const p of project(c.stars))assert.ok(Number.isFinite(p.x)&&Number.isFinite(p.y)&&p.x>=25-1e-9&&p.x<=275+1e-9&&p.y>=22-1e-9&&p.y<=202+1e-9);
}
// Symmetric tangent-plane sky: east to the left, north up, one uniform scale.
const p=project([['W',-1,0],['E',1,0],['N',0,1],['S',0,-1]]);
assert.ok(p[0].x>p[1].x&&p[2].y<p[3].y);
assert.ok(Math.abs((p[0].x-p[1].x)-(p[3].y-p[2].y))<1e-8);
const shifted=project([['W',119,0],['E',121,0],['N',120,1],['S',120,-1]]);
p.forEach((v,i)=>assert.ok(Math.hypot(v.x-shifted[i].x,v.y-shifted[i].y)<1e-8));
const orion=project(catalog[0].stars),belt=orion.slice(2,5);
const area=Math.abs((belt[1].x-belt[0].x)*(belt[2].y-belt[0].y)-(belt[1].y-belt[0].y)*(belt[2].x-belt[0].x));
assert.ok(area/Math.hypot(belt[2].x-belt[0].x,belt[2].y-belt[0].y)<2,'Orion belt remains nearly straight');
assert.match(catalog[2].label,/asterism in Ursa Major/);
console.log('19 real-star coordinates, valid connections, bounded charts, north/east orientation, equal geometric scale and Orion belt alignment passed.');
