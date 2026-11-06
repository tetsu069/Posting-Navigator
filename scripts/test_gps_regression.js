const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const src=fs.readFileSync(path.join(__dirname,'../docs/app.js'),'utf8');
const code=src.slice(src.indexOf('function gpsGraph(){'),src.indexOf('// Show ONLY graph-connected'));
const hav=(a,b,c,d)=>Math.hypot((a-c)*111000,(b-d)*91000);
const ctx={state:{osmRoadSegments:[],gpsRoadGraph:null,gpsTrack:[]},Math,Map,Set,Number,console,haversine:hav,
projectRoadPoint:(lat,lon,pts)=>{const [a,b]=pts;let dx=b[0]-a[0],dy=b[1]-a[1],t=Math.max(0,Math.min(1,((lon-a[0])*dx+(lat-a[1])*dy)/(dx*dx+dy*dy)));return {t,d:Math.hypot((lat-a[1]-dy*t)*111000,(lon-a[0]-dx*t)*91000)}}};
vm.createContext(ctx);vm.runInContext(code,ctx);
function road(a,b,way,index=1){ctx.state.osmRoadSegments.push({a:{lat:a[0],lon:a[1]},b:{lat:b[0],lon:b[1]},len:hav(...a,...b),way,index})}
function point(lat,lon,t){return {lat,lon,t,accuracy:8}}
function reset(){ctx.state.osmRoadSegments=[];ctx.state.gpsRoadGraph=null;vm.runInContext('gpsSpatialIndex=null;gpsSpatialSource=null',ctx)}
function match(points){ctx.state.gpsTrack=points;return vm.runInContext('gpsDisplayTrack()',ctx)}
let n=0;function test(name,fn){fn();console.log('PASS '+name);n++}
const A=[35,139],B=[35,139.001],C=[35.001,139.001],D=[35.001,139.002];
test('交差点を曲がるGPSを道路形状で接続',()=>{reset();road(A,B,1);road(B,C,2);const r=match([point(35,139.0001,0),point(35,139.0007,30000),point(35.0003,139.001,60000),point(35.0008,139.001,90000)]);assert(r.matched.length);assert(r.matched.flat().some(x=>Math.abs(x[0]-B[0])<1e-7&&Math.abs(x[1]-B[1])<1e-7));});
test('道路が接続しない場合は架空の直線を描かない',()=>{reset();road(A,B,1);road([35.002,139],[35.002,139.001],2);const r=match([point(35,139.0005,0),point(35.002,139.0005,30000)]);assert.strictEqual(r.matched.length,0)});
test('同一道路でも制限距離を超える経路を拒否',()=>{reset();road(A,B,1);const cs=vm.runInContext('gpsCandidates({lat:35,lon:139.0001,accuracy:8})',ctx);const ce=vm.runInContext('gpsCandidates({lat:35,lon:139.0009,accuracy:8})',ctx);assert(cs.length&&ce.length);assert.strictEqual(vm.runInContext('gpsRoadPath(a,b,15)',Object.assign(ctx,{a:cs[0],b:ce[0]})),null)});
test('袋小路の往復は同一道路上に収まる',()=>{reset();road(A,B,1);const r=match([point(35,139.0001,0),point(35,139.0008,30000),point(35,139.0005,60000),point(35,139.0002,90000)]);assert(r.matched.length);for(const [lat,lon] of r.matched.flat())assert(Math.abs(lat-35)<1e-7&&lon>=139&&lon<=139.001)});
test('OSM未取得時は偽の道路実線を生成しない',()=>{reset();const r=match([point(35,139,0),point(35,139.001,30000)]);assert.strictEqual(r.matched.length,0);assert.strictEqual(r.unmatched.length,2)});
const gpx=fs.readFileSync('/mnt/data/123.gpx','utf8');const fixes=[...gpx.matchAll(/<trkpt lat="([\d.]+)" lon="([\d.]+)">\s*<time>([^<]+)<\/time>/g)].map(m=>point(+m[1],+m[2],Date.parse(m[3])));
test('提供GPXの測位点を読み込み再生できる',()=>{assert(fixes.length>500);reset();const r=match(fixes);assert.strictEqual(r.unmatched.length,fixes.length);console.log('GPX fixes='+fixes.length+' (OSM unavailable: no road-accuracy claim)')});
console.log(`${n} GPS regression tests passed`);
