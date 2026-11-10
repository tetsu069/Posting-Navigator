const configured=(window.POSTING_NAVIGATOR_API||'').replace(/\/$/,'');
const API=configured || location.origin;
const $=id=>document.getElementById(id);
const state={uploadId:null,jobId:null,geojson:null,areaGeojson:null,summary:null,projectId:null,shareCode:null,workerId:1,watchId:null,current:null,completed:new Set(),segments:[],segmentLengths:[],layers:{},syncTimer:null,config:{gps_threshold_m:18,sync_interval_ms:5000},areaInfo:{},fieldGuideLeg:0,lastPosition:null,sessionToken:localStorage.getItem('pn_session')||'',user:null,navLegs:[],startPickMode:false,activeGuideLeg:0,forceOsmRefresh:false,guidePassed:new Set(),servicePasses:new Map(),kmzBlob:null,gpsTrack:[],gpsTrackingDesired:false,roadPasses:{},restoring:false,kmzUploadGeneration:0,osmRoadSegments:[],osmRoadArea:null,gpsTiles:new Set(),gpsTilesLoading:new Set(),gpsRoadGraph:null};
const map=L.map('map').setView([35.7005,139.6925],16);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:20,attribution:'© OpenStreetMap contributors'}).addTo(map);

function status(id,text,type=''){const el=$(id);el.textContent=text;el.className='status'+(type?' '+type:'')}
function authHeaders(extra={}){return state.sessionToken?{...extra,Authorization:`Bearer ${state.sessionToken}`} : extra}
async function api(path,opts={}){const r=await fetch(`${API}${path}`,{...opts,headers:authHeaders(opts.headers||{})});let j={};try{j=await r.json()}catch{}if(!r.ok)throw Error(j.error||`${r.status} ${r.statusText}`);return j}
async function sessionDb(){return new Promise((resolve,reject)=>{const req=indexedDB.open('posting-navigator-session',1);req.onupgradeneeded=()=>{if(!req.result.objectStoreNames.contains('state'))req.result.createObjectStore('state')};req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error)})}
async function sessionGet(){if(!('indexedDB'in window))return null;try{const db=await sessionDb();return await new Promise((resolve,reject)=>{const tx=db.transaction('state','readonly'),r=tx.objectStore('state').get('current');r.onsuccess=()=>resolve(r.result||null);r.onerror=()=>reject(r.error)})}catch{return null}}
async function sessionPut(v){if(!('indexedDB'in window))return;try{const db=await sessionDb();await new Promise((resolve,reject)=>{const tx=db.transaction('state','readwrite');tx.objectStore('state').put(v,'current');tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error)})}catch(e){console.warn(e)}}
// GPS is stored separately so a large route/session write cannot erase its history.
async function gpsTrackGet(){if(!('indexedDB' in window))return null;try{const db=await sessionDb();return await new Promise((resolve,reject)=>{const tx=db.transaction('state','readonly'),r=tx.objectStore('state').get('gps-track');r.onsuccess=()=>resolve(r.result||null);r.onerror=()=>reject(r.error)})}catch(e){console.warn('GPS復元失敗',e);return null}}
async function gpsTrackPut(){if(!('indexedDB' in window))return;try{const db=await sessionDb();await new Promise((resolve,reject)=>{const tx=db.transaction('state','readwrite');tx.objectStore('state').put({points:state.gpsTrack,savedAt:Date.now()},'gps-track');tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error)})}catch(e){console.warn('GPS保存失敗',e)}}
let gpsSaveTimer=null;
function saveGpsSoon(){clearTimeout(gpsSaveTimer);gpsSaveTimer=setTimeout(()=>gpsTrackPut(),250)}
// Clear both the independent GPS store and the legacy session copy atomically.
// A cleared marker prevents older session snapshots from resurrecting the track.
async function clearSavedGpsTrack(){
  clearTimeout(gpsSaveTimer);clearTimeout(saveSessionTimer);
  state.gpsTrack=[];gpsMatchCached=[];gpsMatchLastCount=-1;
  if(window.PostingNative)window.PostingNative.clearTrack();
  drawGpsTrack();
  if(!('indexedDB' in window))throw Error('この端末では保存領域を利用できません');
  const db=await sessionDb();
  await new Promise((resolve,reject)=>{
    const tx=db.transaction('state','readwrite');
    const store=tx.objectStore('state');
    const request=store.get('current');
    request.onsuccess=()=>{
      const current=request.result;
      if(current){current.gpsTrack=[];store.put(current,'current')}
      store.put({points:[],cleared:true,savedAt:Date.now()},'gps-track');
    };
    request.onerror=()=>tx.abort();
    tx.oncomplete=()=>resolve();
    tx.onerror=()=>reject(tx.error||Error('GPS記録の削除に失敗しました'));
    tx.onabort=()=>reject(tx.error||Error('GPS記録の削除が中断されました'));
  });
}
$('clearGpsTrack').onclick=async()=>{
  if(!confirm('保存済みGPS軌跡を消去しますか？ 巡回ルートと配布進捗は残ります。'))return;
  const button=$('clearGpsTrack');button.disabled=true;
  try{
    await clearSavedGpsTrack();
    status('gpsStatus',state.watchId!==null?'GPS軌跡を消去しました。GPS追跡中のため、次の位置情報から新しい軌跡を記録します。':'GPS軌跡を消去しました。再起動後も復元されません。','success');
  }catch(e){status('gpsStatus',`GPS軌跡の保存データ消去に失敗しました: ${e.message}`,'error')}
  finally{button.disabled=false}
};

function serialiseServicePasses(){return [...state.servicePasses.entries()].map(([k,r])=>[k,{dirs:[...r.dirs],segments:[...r.segments.entries()],complete:!!r.complete}])}
function restoreServicePasses(rows){state.servicePasses=new Map((rows||[]).map(([k,r])=>[k,{dirs:new Set(r.dirs||[]),segments:new Map(r.segments||[]),complete:!!r.complete}]))}
let saveSessionTimer=null;
function saveFieldSessionSoon(){if(state.restoring)return;clearTimeout(saveSessionTimer);saveSessionTimer=setTimeout(saveFieldSession,300)}
async function saveFieldSession(){await sessionPut({savedAt:Date.now(),kmzBlob:state.kmzBlob,geojson:state.geojson,areaGeojson:state.areaGeojson,summary:state.summary,areaInfo:state.areaInfo,areas:[...($('area')?.options||[])].map(o=>o.value),selectedArea:$('area')?.value||'',startLat:$('lat')?.value||'',startLon:$('lon')?.value||'',workerId:state.workerId,completed:[...state.completed],fieldGuideLeg:state.fieldGuideLeg,activeGuideLeg:state.activeGuideLeg,guidePassed:[...state.guidePassed],servicePasses:serialiseServicePasses(),gpsTrack:state.gpsTrack,roadPasses:state.roadPasses,gpsTrackingDesired:state.gpsTrackingDesired})}
// Match display track against the full cached OSM highway network, not the planned route.
// The original measurements are retained; roadPasses continue to use raw GPS evidence.
async function loadGpsRoadNetwork(area){
  if(!area)return;
  if(state.osmRoadArea===area&&state.osmRoadSegments.length)return;
  let saved=await getBrowserOsmCache(area);
  if(!saved?.blob){await saveServerOsmCacheToBrowser(area);saved=await getBrowserOsmCache(area)}
  if(!saved?.blob)return;
  try{
    const data=JSON.parse(await saved.blob.text());
    const segments=[];
    for(const way of data.elements||[]){
      if(way.type!=='way'||!way.tags?.highway||!Array.isArray(way.geometry))continue;
      if(['motorway','motorway_link','trunk','trunk_link','proposed','construction','raceway'].includes(way.tags.highway))continue;
      const pts=way.geometry;
      for(let i=1;i<pts.length;i++){
        const a=pts[i-1],b=pts[i];
        if(![a.lat,a.lon,b.lat,b.lon].every(Number.isFinite))continue;
        const len=haversine(a.lat,a.lon,b.lat,b.lon);
        if(len<.3||len>300)continue;
        segments.push({a,b,len,way:way.id,index:i,layer:Number(way.tags.layer)||0,bridge:!!way.tags.bridge&&way.tags.bridge!=="no",tunnel:!!way.tags.tunnel&&way.tags.tunnel!=="no"});
      }
    }
    state.osmRoadSegments=segments;state.osmRoadArea=area;state.gpsRoadGraph=null;gpsSpatialIndex=null;
    drawGpsTrack();
  }catch(e){console.warn('OSM GPS道路網の読み込み失敗',e)}
}
// Fetch surrounding OSM streets as the walker moves beyond the original area.
// Tiles are about 400 m wide; failures are retried on a later GPS observation.
async function ensureGpsRoadCoverage(lat,lon){
  const x=Math.floor(lon*250),y=Math.floor(lat*250),key=`${x}:${y}`;
  if(state.gpsTiles.has(key)||state.gpsTilesLoading.has(key))return;
  state.gpsTilesLoading.add(key);
  try{
    const data=await api(`/api/gps-roads?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`);
    const existing=new Set(state.osmRoadSegments.map(s=>`${s.way}:${s.index}`));
    for(const way of data.elements||[]){
      if(way.type!=='way'||!way.tags?.highway||!Array.isArray(way.geometry))continue;
      if(['motorway','motorway_link','trunk','trunk_link','proposed','construction','raceway'].includes(way.tags.highway))continue;
      for(let i=1;i<way.geometry.length;i++){
        const a=way.geometry[i-1],b=way.geometry[i],id=`${way.id}:${i}`;
        if(existing.has(id)||![a.lat,a.lon,b.lat,b.lon].every(Number.isFinite))continue;
        const len=haversine(a.lat,a.lon,b.lat,b.lon);
        if(len>.3&&len<300){state.osmRoadSegments.push({a,b,len,way:way.id,index:i,layer:Number(way.tags.layer)||0,bridge:!!way.tags.bridge&&way.tags.bridge!=="no",tunnel:!!way.tags.tunnel&&way.tags.tunnel!=="no"});existing.add(id)}
      }
    }
    state.gpsRoadGraph=null;gpsSpatialIndex=null;
    state.gpsTiles.add(key);
    drawGpsTrack();
  }catch(e){console.warn('周辺GPS道路データの取得失敗',e)}
  finally{state.gpsTilesLoading.delete(key)}
}
// Build a walkable graph of OSM segment endpoints, including shared intersections.
function gpsGraph(){
  if(state.gpsRoadGraph)return state.gpsRoadGraph;
  const nodes=new Map(),edges=new Map();
  const key=p=>`${p.lat.toFixed(6)},${p.lon.toFixed(6)}`;
  function add(a,b,d){if(!edges.has(a))edges.set(a,[]);edges.get(a).push({to:b,d})}
  const endpointRecords=[];
  for(const seg of state.osmRoadSegments){
    const a=key(seg.a),b=key(seg.b);nodes.set(a,seg.a);nodes.set(b,seg.b);add(a,b,seg.len);add(b,a,seg.len);
    // Only genuine OSM way endpoints may be repaired: never snap a crossing
    // in the middle of a bridge, tunnel, or ordinary road.
    if(seg.index===1)endpointRecords.push({p:seg.a,k:a,seg});
  }
  // A way's last endpoint may not be index 1. Detect it from the supplied
  // segments rather than assuming consecutive OSM IDs are present.
  const lastByWay=new Map();
  for(const seg of state.osmRoadSegments){const prev=lastByWay.get(seg.way);if(!prev||seg.index>prev.index)lastByWay.set(seg.way,seg)}
  for(const seg of lastByWay.values())endpointRecords.push({p:seg.b,k:key(seg.b),seg});
  const buckets=new Map(),cell=.000015;let repaired=0;
  for(const e of endpointRecords){
    const x=Math.floor(e.p.lon/cell),y=Math.floor(e.p.lat/cell);
    for(let dy=-1;dy<=1;dy++)for(let dx=-1;dx<=1;dx++){
      for(const other of buckets.get(`${x+dx}:${y+dy}`)||[]){
        if(e.seg.way===other.seg.way||e.k===other.k)continue;
        if((e.seg.layer||0)!==(other.seg.layer||0)||!!e.seg.bridge!==!!other.seg.bridge||!!e.seg.tunnel!==!!other.seg.tunnel)continue;
        const d=haversine(e.p.lat,e.p.lon,other.p.lat,other.p.lon);
        if(d>0.15&&d<=0.8){add(e.k,other.k,d);add(other.k,e.k,d);repaired++}
      }
    }
    const id=`${x}:${y}`;if(!buckets.has(id))buckets.set(id,[]);buckets.get(id).push(e);
  }
  return state.gpsRoadGraph={nodes,edges,key,endpointRepairs:repaired};
}
// Bounded Dijkstra: reject implausible detours rather than inventing a walked route.
// Shortest connected road path between projected GPS fixes. Include both projected
// endpoints; a direct segment is valid only when both fixes lie on the same edge.
function gpsRoadPath(a,b,maxMeters){
  if(!a.seg||!b.seg)return null;
  const coord=p=>[p.lat,p.lon];
  const graph=gpsGraph(),key=graph.key;
  if(a.seg.way===b.seg.way&&a.seg.index===b.seg.index)
    return Math.abs(a.t-b.t)*a.seg.len<=maxMeters?[coord(a),coord(b)]:null;
  const starts=[{k:key(a.seg.a),d:a.t*a.seg.len},{k:key(a.seg.b),d:(1-a.t)*a.seg.len}];
  const ends=new Map([[key(b.seg.a),b.t*b.seg.len],[key(b.seg.b),(1-b.t)*b.seg.len]]);
  const dist=new Map(),previous=new Map(),queue=[];
  for(const x of starts){if(x.d<(dist.get(x.k)??Infinity)){dist.set(x.k,x.d);queue.push({k:x.k,d:x.d})}}
  let finish=null,best=Infinity;
  for(let steps=0;queue.length&&steps<5000;steps++){
    queue.sort((x,y)=>y.d-x.d);const {k,d}=queue.pop();
    if(d!==dist.get(k)||d>maxMeters||d>=best)continue;
    if(ends.has(k)&&d+ends.get(k)<best){best=d+ends.get(k);finish=k}
    for(const e of graph.edges.get(k)||[]){const nd=d+e.d;
      if(nd<=maxMeters&&nd<(dist.get(e.to)??Infinity)){dist.set(e.to,nd);previous.set(e.to,k);queue.push({k:e.to,d:nd})}
    }
  }
  if(finish===null||best>maxMeters)return null;
  const middle=[];for(let k=finish;k!==undefined;k=previous.get(k))middle.push(coord(graph.nodes.get(k)));
  middle.reverse();return [coord(a),...middle,coord(b)];
}
// Candidate roads are indexed in ~90m geographic cells; avoid scanning every
// road segment on every GPS fix. Rebuilt whenever the OSM graph changes.
let gpsSpatialIndex=null,gpsSpatialSource=null;
function gpsRoadSpatialIndex(){
  if(gpsSpatialIndex&&gpsSpatialSource===state.gpsRoadGraph)return gpsSpatialIndex;
  const cell=.0008,grid=new Map();
  for(const seg of state.osmRoadSegments){
    const y0=Math.floor(Math.min(seg.a.lat,seg.b.lat)/cell),y1=Math.floor(Math.max(seg.a.lat,seg.b.lat)/cell);
    const x0=Math.floor(Math.min(seg.a.lon,seg.b.lon)/cell),x1=Math.floor(Math.max(seg.a.lon,seg.b.lon)/cell);
    for(let y=y0;y<=y1;y++)for(let x=x0;x<=x1;x++){
      const k=`${y}:${x}`;if(!grid.has(k))grid.set(k,[]);grid.get(k).push(seg);
    }
  }
  gpsSpatialSource=state.gpsRoadGraph;return gpsSpatialIndex={grid,cell};
}
// Android's native recorder does not call the browser onPosition handler while
// the screen is off. Load road tiles for the restored route as well as live GPS.
function ensureRecordedGpsRoadCoverage(){
  const pts=state.gpsTrack,selected=[];
  for(let i=Math.max(0,pts.length-500);i<pts.length;i++){
    const p=pts[i],last=selected.at(-1);
    if(!last||haversine(last.lat,last.lon,p.lat,p.lon)>230)selected.push(p);
  }
  // Limit simultaneous network requests; the rest are retried at the next sync.
  for(const p of selected.slice(-12))ensureGpsRoadCoverage(p.lat,p.lon);
}
function gpsCandidates(p){
  const accuracy=Number(p.accuracy);
  // Keep the usual search tight, but respect a device-reported poor fix.
  // A capped radius prevents GPS outliers from snapping to distant roads.
  const radius=Math.min(85,Math.max(18,Number.isFinite(accuracy)?accuracy*1.5:30));
  const {grid,cell}=gpsRoadSpatialIndex(),seen=new Set(),candidates=[];
  const dy=radius/110540,dx=radius/(111320*Math.max(.2,Math.cos(p.lat*Math.PI/180)));
  for(let y=Math.floor((p.lat-dy)/cell);y<=Math.floor((p.lat+dy)/cell);y++)
    for(let x=Math.floor((p.lon-dx)/cell);x<=Math.floor((p.lon+dx)/cell);x++)
      for(const seg of grid.get(`${y}:${x}`)||[]){
        if(seen.has(seg))continue;seen.add(seg);
        const q=projectRoadPoint(p.lat,p.lon,[[seg.a.lon,seg.a.lat],[seg.b.lon,seg.b.lat]]);
        if(q.d>radius)continue;
        candidates.push({lat:seg.a.lat+(seg.b.lat-seg.a.lat)*q.t,lon:seg.a.lon+(seg.b.lon-seg.a.lon)*q.t,d:q.d,seg,t:q.t});
      }
  candidates.sort((a,b)=>a.d-b.d);
  // Retain nearest alternatives and add distinct ways when parallel roads or
  // intersections would otherwise fill every slot with one polyline's edges.
  const chosen=candidates.slice(0,4),used=new Set(chosen.map(c=>c.seg.way));
  for(const c of candidates){
    if(chosen.length>=7)break;
    if(!used.has(c.seg.way)){chosen.push(c);used.add(c.seg.way)}
  }
  for(const c of candidates){if(chosen.length>=7)break;if(!chosen.includes(c))chosen.push(c)}
  return chosen.sort((a,b)=>a.d-b.d);
}
function gpsPathLength(path){let n=0;for(let i=1;i<path.length;i++)n+=haversine(path[i-1][0],path[i-1][1],path[i][0],path[i][1]);return n}
// A Viterbi-style temporal matcher: choose a sequence of connected streets,
// not the nearest street independently at each sample. Restart after gaps.
// Road geometry is the ONLY source for solid GPS track lines.
function gpsDisplayTrackLocal(){
  const matched=[],unmatched=[],transitions=[],runs=[],raw=state.gpsTrack.filter(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lon));
  if(!raw.length||!state.osmRoadSegments.length)return {matched,unmatched:raw.map(p=>[p.lat,p.lon])};
  const frames=[];let previous=null;
  for(const p of raw){
    // Sparse samples are kept; zero movement and noisy stationary fixes do not
    // force arbitrary loops through nearby parallel roads.
    const cs=gpsCandidates(p),frame={p,cs,states:[]};
    const dt=previous?Math.max(.1,(p.t-previous.p.t)/1000):0;
    const displacement=previous?haversine(previous.p.lat,previous.p.lon,p.lat,p.lon):0;
    for(let j=0;j<cs.length;j++){
      const c=cs[j],sigma=Math.max(8,Math.min(40,(Number(p.accuracy)||16)*.8));
      const emission=(c.d/sigma)**2*5;
      let best={score:emission,from:-1,path:null};
      if(previous&&dt<150&&displacement<350){
        best={score:Infinity,from:-1,path:null};
        for(let k=0;k<previous.states.length;k++){
          const prior=previous.states[k];if(!Number.isFinite(prior.score))continue;
          const limit=Math.min(700,Math.max(55,displacement*2.7+40,dt*3+35));
          const path=gpsRoadPath(previous.cs[k],c,limit);
          if(!path)continue;
          const distance=gpsPathLength(path);
          const speed=distance/dt;
          if(speed>7.5&&distance>35)continue;
          const transition=Math.abs(distance-displacement)*.38+Math.max(0,distance-displacement*1.7-20)*.7;
          const score=prior.score+emission+transition;
          if(score<best.score)best={score,from:k,path};
        }
        // No connected road transition: start a new trace, never bridge by a line.
        if(best.from<0){
          const previousBest=Math.min(...previous.states.map(x=>x.score));
          best={score:previousBest+emission+120,from:-1,path:null};
        }
      }
      frame.states.push(best);
    }
    frames.push(frame);previous=frame;
  }
  // Backtrack independently from the end of each connected run. This prevents
  // future samples from causing abrupt switches between parallel streets.
  let i=frames.length-1;
  while(i>=0){
    const f=frames[i];
    if(!f.states.length){unmatched.push([f.p.lat,f.p.lon]);i--;continue}
    let j=f.states.reduce((best,v,k)=>v.score<f.states[best].score?k:best,0);
    const parts=[];
    while(i>=0&&j>=0){
      const cur=frames[i],st=cur.states[j];
      if(st.path&&st.from>=0)parts.push(st.path);
      const next=st.from;i--;j=next;
    }
    if(parts.length){const joined=[];for(const path of parts.reverse()){
      if(!joined.length)joined.push(...path);else joined.push(...path.slice(1));
    }if(joined.length>1)matched.push(joined)}
  }
  // A single valid road segment may contain several consecutive fixes even
  // when the wider graph is incomplete. Render these local road-following runs.
  // Do not connect points on different edges unless a graph path was found.
  if(!matched.length){
    let run=[];
    for(let n=0;n<frames.length;n++){
      const f=frames[n];
      if(!f.cs.length){if(run.length>1)matched.push(run);run=[];continue}
      const c=f.cs[0],prev=n?frames[n-1]:null,pc=prev?.cs?.[0];
      const connected=pc&&pc.seg.way===c.seg.way&&pc.seg.index===c.seg.index&&
        haversine(pc.lat,pc.lon,c.lat,c.lon)<80&&
        (f.p.t-prev.p.t)<120000;
      if(!connected){if(run.length>1)matched.push(run);run=[]}
      run.push([c.lat,c.lon]);
    }
    if(run.length>1)matched.push(run);
  }
  // Adjacent connected paths are authoritative for display coverage. Preserve
  // every successful transition instead of choosing between an entire global
  // Viterbi run and a separate collection of local paths.
  const local=[];
  let current=[],lastCandidate=null,runStart=-1,runEnd=-1;
  function flush(){if(current.length>1){local.push(current);runs.push({startFixIndex:runStart,endFixIndex:runEnd,vertices:current.length})}current=[];lastCandidate=null;runStart=-1;runEnd=-1}
  for(let n=1;n<frames.length;n++){
    const prev=frames[n-1],cur=frames[n];
    const dt=(cur.p.t-prev.p.t)/1000;
    const displacement=haversine(prev.p.lat,prev.p.lon,cur.p.lat,cur.p.lon);
    if(!(dt>0&&dt<150&&displacement<350&&prev.cs.length&&cur.cs.length)){
      transitions.push({fromFixIndex:n-1,toFixIndex:n,dtSeconds:dt,gpsMeters:displacement,reason:!(dt>0&&dt<150)?'time_gap':displacement>=350?'gps_jump':'no_candidates',previousCandidates:prev.cs.length,nextCandidates:cur.cs.length});flush();continue}
    const limit=Math.min(700,Math.max(55,displacement*2.7+40,dt*3+35));
    let best=null,bestScore=Infinity,bestStart=null,bestEnd=null,bestBridge=null;
    // Evaluate continuity with the already rendered road at the shared GPS fix.
    // Selecting the cheapest pair independently can oscillate between parallel
    // roads and create artificial gaps even when a consistent pair exists.
    for(const a of prev.cs)for(const b of cur.cs){
      const path=gpsRoadPath(a,b,limit);
      if(!path)continue;
      const distance=gpsPathLength(path);
      if(distance/dt>7.5&&distance>35)continue;
      let bridge=null,bridgeDistance=0;
      if(current.length&&lastCandidate){
        const gap=haversine(lastCandidate.lat,lastCandidate.lon,a.lat,a.lon);
        if(gap>1.5){
          const bridgeLimit=Math.min(130,Math.max(45,gap*3+15));
          if(gap>42)continue;
          bridge=gpsRoadPath(lastCandidate,a,bridgeLimit);
          if(!bridge)continue;
          bridgeDistance=gpsPathLength(bridge);
          if(bridgeDistance>bridgeLimit)continue;
        }
      }
      const score=a.d+b.d+Math.abs(distance-displacement)*.5+bridgeDistance*1.25;
      if(score<bestScore){bestScore=score;best=path;bestStart=a;bestEnd=b;bestBridge=bridge}
    }
    if(!best||best.length<2){transitions.push({fromFixIndex:n-1,toFixIndex:n,dtSeconds:dt,gpsMeters:displacement,reason:'no_valid_road_transition',previousCandidates:prev.cs.map(c=>({way:c.seg.way,index:c.seg.index,d:c.d})),nextCandidates:cur.cs.map(c=>({way:c.seg.way,index:c.seg.index,d:c.d}))});flush();continue}
    // The candidate pair was already checked against the previous segment.
    if(current.length&&bestBridge)current.push(...bestBridge.slice(1));
    if(!current.length){current.push(...best);runStart=n-1}
    else current.push(...best.slice(1));
    runEnd=n;lastCandidate=bestEnd;
  }
  flush();
  return {matched:local,unmatched,runs,transitions};
}

function gpsDisplayTrackLocalCoverage(continuityPenalty=0){
  const matched=[],unmatched=[],transitions=[],runs=[],intervals=[],raw=state.gpsTrack.filter(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lon));
  if(!raw.length||!state.osmRoadSegments.length)return {matched,unmatched:raw.map(p=>[p.lat,p.lon])};
  const frames=[];let previous=null;
  for(const p of raw){
    // Sparse samples are kept; zero movement and noisy stationary fixes do not
    // force arbitrary loops through nearby parallel roads.
    const cs=gpsCandidates(p),frame={p,cs,states:[]};
    const dt=previous?Math.max(.1,(p.t-previous.p.t)/1000):0;
    const displacement=previous?haversine(previous.p.lat,previous.p.lon,p.lat,p.lon):0;
    for(let j=0;j<cs.length;j++){
      const c=cs[j],sigma=Math.max(8,Math.min(40,(Number(p.accuracy)||16)*.8));
      const emission=(c.d/sigma)**2*5;
      let best={score:emission,from:-1,path:null};
      if(previous&&dt<150&&displacement<350){
        best={score:Infinity,from:-1,path:null};
        for(let k=0;k<previous.states.length;k++){
          const prior=previous.states[k];if(!Number.isFinite(prior.score))continue;
          const limit=Math.min(700,Math.max(55,displacement*2.7+40,dt*3+35));
          const path=gpsRoadPath(previous.cs[k],c,limit);
          if(!path)continue;
          const distance=gpsPathLength(path);
          const speed=distance/dt;
          if(speed>7.5&&distance>35)continue;
          const transition=Math.abs(distance-displacement)*.38+Math.max(0,distance-displacement*1.7-20)*.7;
          const score=prior.score+emission+transition;
          if(score<best.score)best={score,from:k,path};
        }
        // No connected road transition: start a new trace, never bridge by a line.
        if(best.from<0){
          const previousBest=Math.min(...previous.states.map(x=>x.score));
          best={score:previousBest+emission+120,from:-1,path:null};
        }
      }
      frame.states.push(best);
    }
    frames.push(frame);previous=frame;
  }
  // Backtrack independently from the end of each connected run. This prevents
  // future samples from causing abrupt switches between parallel streets.
  let i=frames.length-1;
  while(i>=0){
    const f=frames[i];
    if(!f.states.length){unmatched.push([f.p.lat,f.p.lon]);i--;continue}
    let j=f.states.reduce((best,v,k)=>v.score<f.states[best].score?k:best,0);
    const parts=[];
    while(i>=0&&j>=0){
      const cur=frames[i],st=cur.states[j];
      if(st.path&&st.from>=0)parts.push(st.path);
      const next=st.from;i--;j=next;
    }
    if(parts.length){const joined=[];for(const path of parts.reverse()){
      if(!joined.length)joined.push(...path);else joined.push(...path.slice(1));
    }if(joined.length>1)matched.push(joined)}
  }
  // A single valid road segment may contain several consecutive fixes even
  // when the wider graph is incomplete. Render these local road-following runs.
  // Do not connect points on different edges unless a graph path was found.
  if(!matched.length){
    let run=[];
    for(let n=0;n<frames.length;n++){
      const f=frames[n];
      if(!f.cs.length){if(run.length>1)matched.push(run);run=[];continue}
      const c=f.cs[0],prev=n?frames[n-1]:null,pc=prev?.cs?.[0];
      const connected=pc&&pc.seg.way===c.seg.way&&pc.seg.index===c.seg.index&&
        haversine(pc.lat,pc.lon,c.lat,c.lon)<80&&
        (f.p.t-prev.p.t)<120000;
      if(!connected){if(run.length>1)matched.push(run);run=[]}
      run.push([c.lat,c.lon]);
    }
    if(run.length>1)matched.push(run);
  }
  // Adjacent connected paths are authoritative for display coverage. Preserve
  // every successful transition instead of choosing between an entire global
  // Viterbi run and a separate collection of local paths.
  const local=[];
  let current=[],lastCandidate=null,runStart=-1,runEnd=-1,parts=[];
  function flush(){if(current.length>1){local.push(current);runs.push({startFixIndex:runStart,endFixIndex:runEnd,vertices:current.length});intervals.push(parts)}current=[];lastCandidate=null;runStart=-1;runEnd=-1;parts=[]}
  for(let n=1;n<frames.length;n++){
    const prev=frames[n-1],cur=frames[n];
    const dt=(cur.p.t-prev.p.t)/1000;
    const displacement=haversine(prev.p.lat,prev.p.lon,cur.p.lat,cur.p.lon);
    if(!(dt>0&&dt<150&&displacement<350&&prev.cs.length&&cur.cs.length)){
      transitions.push({fromFixIndex:n-1,toFixIndex:n,dtSeconds:dt,gpsMeters:displacement,reason:!(dt>0&&dt<150)?'time_gap':displacement>=350?'gps_jump':'no_candidates',previousCandidates:prev.cs.length,nextCandidates:cur.cs.length});flush();continue}
    const limit=Math.min(700,Math.max(55,displacement*2.7+40,dt*3+35));
    let best=null,bestScore=Infinity,bestStart=null,bestEnd=null;
    for(const a of prev.cs.slice(0,7))for(const b of cur.cs.slice(0,7)){
      const path=gpsRoadPath(a,b,limit);
      if(!path)continue;
      const distance=gpsPathLength(path);
      if(distance/dt>7.5&&distance>35)continue;
      let score=a.d+b.d+Math.abs(distance-displacement)*.5;
      // At a shared GPS fix, prefer the same snapped road position. Do not
      // mistake a 100m OSM detour between parallel streets for walking.
      if(continuityPenalty&&current.length&&lastCandidate){
        const mismatch=haversine(lastCandidate.lat,lastCandidate.lon,a.lat,a.lon);
        if(mismatch>1.5)score+=continuityPenalty*Math.min(50,mismatch);
      }
      if(score<bestScore){bestScore=score;best=path;bestStart=a;bestEnd=b}
    }
    if(!best||best.length<2){transitions.push({fromFixIndex:n-1,toFixIndex:n,dtSeconds:dt,gpsMeters:displacement,reason:'no_valid_road_transition',previousCandidates:prev.cs.map(c=>({way:c.seg.way,index:c.seg.index,d:c.d})),nextCandidates:cur.cs.map(c=>({way:c.seg.way,index:c.seg.index,d:c.d}))});flush();continue}
    // When two adjacent GPS transitions choose different edges at their
    // shared fix, join ONLY through a short, connected OSM graph path.
    // Never draw a direct connector across buildings or disconnected streets.
    if(current.length&&lastCandidate){
      const tail=current.at(-1),head=best[0];
      if(haversine(tail[0],tail[1],head[0],head[1])>1.5){
        const gap=haversine(tail[0],tail[1],head[0],head[1]);
        const bridgeLimit=Math.min(130,Math.max(45,gap*3+15));
        const bridge=gap<=42?gpsRoadPath(lastCandidate,bestStart,bridgeLimit):null;
        if(bridge&&gpsPathLength(bridge)<=bridgeLimit){
          current.push(...bridge.slice(1));
        }else {transitions.push({fromFixIndex:n-1,toFixIndex:n,dtSeconds:dt,gpsMeters:displacement,reason:'candidate_switch_bridge_failed',bridgeGapMeters:gap,bridgeLimitMeters:bridgeLimit,bridgePathMeters:bridge?gpsPathLength(bridge):null,previousWay:lastCandidate.seg.way,nextWay:bestStart.seg.way});flush()}
      }
    }
    if(!current.length){current.push(...best);runStart=n-1}
    else current.push(...best.slice(1));
    // Each interval maps one GPS fix pair to the exact selected OSM candidates
    // and vertex range. This metadata is independent of visual polyline vertices.
    parts.push({fromFixIndex:n-1,toFixIndex:n,startCandidate:bestStart,endCandidate:bestEnd,
      startVertex:current.length-best.length,endVertex:current.length-1,
      roadMeters:gpsPathLength(best)});
    runEnd=n;lastCandidate=bestEnd;
  }
  flush();
  return {matched:local,unmatched,runs,intervals,transitions};
}


function gpsDisplayTrackLocalCoverageConnected(){
  const matched=[],unmatched=[],transitions=[],runs=[],raw=state.gpsTrack.filter(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lon));
  if(!raw.length||!state.osmRoadSegments.length)return {matched,unmatched:raw.map(p=>[p.lat,p.lon])};
  const frames=[];let previous=null;
  for(const p of raw){
    // Sparse samples are kept; zero movement and noisy stationary fixes do not
    // force arbitrary loops through nearby parallel roads.
    const cs=gpsCandidates(p),frame={p,cs,states:[]};
    const dt=previous?Math.max(.1,(p.t-previous.p.t)/1000):0;
    const displacement=previous?haversine(previous.p.lat,previous.p.lon,p.lat,p.lon):0;
    for(let j=0;j<cs.length;j++){
      const c=cs[j],sigma=Math.max(8,Math.min(40,(Number(p.accuracy)||16)*.8));
      const emission=(c.d/sigma)**2*5;
      let best={score:emission,from:-1,path:null};
      if(previous&&dt<150&&displacement<350){
        best={score:Infinity,from:-1,path:null};
        for(let k=0;k<previous.states.length;k++){
          const prior=previous.states[k];if(!Number.isFinite(prior.score))continue;
          const limit=Math.min(700,Math.max(55,displacement*2.7+40,dt*3+35));
          const path=gpsRoadPath(previous.cs[k],c,limit);
          if(!path)continue;
          const distance=gpsPathLength(path);
          const speed=distance/dt;
          if(speed>7.5&&distance>35)continue;
          const transition=Math.abs(distance-displacement)*.38+Math.max(0,distance-displacement*1.7-20)*.7;
          const score=prior.score+emission+transition;
          if(score<best.score)best={score,from:k,path};
        }
        // No connected road transition: start a new trace, never bridge by a line.
        if(best.from<0){
          const previousBest=Math.min(...previous.states.map(x=>x.score));
          best={score:previousBest+emission+120,from:-1,path:null};
        }
      }
      frame.states.push(best);
    }
    frames.push(frame);previous=frame;
  }
  // Backtrack independently from the end of each connected run. This prevents
  // future samples from causing abrupt switches between parallel streets.
  let i=frames.length-1;
  while(i>=0){
    const f=frames[i];
    if(!f.states.length){unmatched.push([f.p.lat,f.p.lon]);i--;continue}
    let j=f.states.reduce((best,v,k)=>v.score<f.states[best].score?k:best,0);
    const parts=[];
    while(i>=0&&j>=0){
      const cur=frames[i],st=cur.states[j];
      if(st.path&&st.from>=0)parts.push(st.path);
      const next=st.from;i--;j=next;
    }
    if(parts.length){const joined=[];for(const path of parts.reverse()){
      if(!joined.length)joined.push(...path);else joined.push(...path.slice(1));
    }if(joined.length>1)matched.push(joined)}
  }
  // A single valid road segment may contain several consecutive fixes even
  // when the wider graph is incomplete. Render these local road-following runs.
  // Do not connect points on different edges unless a graph path was found.
  if(!matched.length){
    let run=[];
    for(let n=0;n<frames.length;n++){
      const f=frames[n];
      if(!f.cs.length){if(run.length>1)matched.push(run);run=[];continue}
      const c=f.cs[0],prev=n?frames[n-1]:null,pc=prev?.cs?.[0];
      const connected=pc&&pc.seg.way===c.seg.way&&pc.seg.index===c.seg.index&&
        haversine(pc.lat,pc.lon,c.lat,c.lon)<80&&
        (f.p.t-prev.p.t)<120000;
      if(!connected){if(run.length>1)matched.push(run);run=[]}
      run.push([c.lat,c.lon]);
    }
    if(run.length>1)matched.push(run);
  }
  // Adjacent connected paths are authoritative for display coverage. Preserve
  // every successful transition instead of choosing between an entire global
  // Viterbi run and a separate collection of local paths.
  const local=[];
  let current=[],lastCandidate=null,runStart=-1,runEnd=-1;
  function flush(){if(current.length>1){local.push(current);runs.push({startFixIndex:runStart,endFixIndex:runEnd,vertices:current.length})}current=[];lastCandidate=null;runStart=-1;runEnd=-1}
  for(let n=1;n<frames.length;n++){
    const prev=frames[n-1],cur=frames[n];
    const dt=(cur.p.t-prev.p.t)/1000;
    const displacement=haversine(prev.p.lat,prev.p.lon,cur.p.lat,cur.p.lon);
    if(!(dt>0&&dt<150&&displacement<350&&prev.cs.length&&cur.cs.length)){
      transitions.push({fromFixIndex:n-1,toFixIndex:n,dtSeconds:dt,gpsMeters:displacement,reason:!(dt>0&&dt<150)?'time_gap':displacement>=350?'gps_jump':'no_candidates',previousCandidates:prev.cs.length,nextCandidates:cur.cs.length});flush();continue}
    const limit=Math.min(700,Math.max(55,displacement*2.7+40,dt*3+35));
    let best=null,bestScore=Infinity,bestStart=null,bestEnd=null,bestJoin=null;
    for(const a of prev.cs.slice(0,7))for(const b of cur.cs.slice(0,7)){
      const path=gpsRoadPath(a,b,limit);
      if(!path)continue;
      const distance=gpsPathLength(path);
      if(distance/dt>7.5&&distance>35)continue;
      // Choose a pair that can be connected to the existing displayed road.
      // A locally cheapest pair can force a discontinuity at the shared fix.
      let joinPath=null,joinDistance=0;
      if(current.length&&lastCandidate){
        const gap=haversine(lastCandidate.lat,lastCandidate.lon,a.lat,a.lon);
        if(gap>1.5){
          const joinLimit=Math.min(130,Math.max(45,gap*3+15));
          if(gap>42)continue;
          joinPath=gpsRoadPath(lastCandidate,a,joinLimit);
          if(!joinPath)continue;
          joinDistance=gpsPathLength(joinPath);
          if(joinDistance>joinLimit)continue;
        }
      }
      const score=a.d+b.d+Math.abs(distance-displacement)*.5+joinDistance*1.25;
      if(score<bestScore){bestScore=score;best=path;bestStart=a;bestEnd=b;bestJoin=joinPath}
    }
    if(!best||best.length<2){transitions.push({fromFixIndex:n-1,toFixIndex:n,dtSeconds:dt,gpsMeters:displacement,reason:'no_valid_road_transition',previousCandidates:prev.cs.map(c=>({way:c.seg.way,index:c.seg.index,d:c.d})),nextCandidates:cur.cs.map(c=>({way:c.seg.way,index:c.seg.index,d:c.d}))});flush();continue}
    // When two adjacent GPS transitions choose different edges at their
    // shared fix, join ONLY through a short, connected OSM graph path.
    // Never draw a direct connector across buildings or disconnected streets.
    if(current.length&&bestJoin)current.push(...bestJoin.slice(1));
    if(current.length&&lastCandidate&&!bestJoin){
      const tail=current.at(-1),head=best[0];
      if(haversine(tail[0],tail[1],head[0],head[1])>1.5){
        const gap=haversine(tail[0],tail[1],head[0],head[1]);
        const bridgeLimit=Math.min(130,Math.max(45,gap*3+15));
        const bridge=gap<=42?gpsRoadPath(lastCandidate,bestStart,bridgeLimit):null;
        if(bridge&&gpsPathLength(bridge)<=bridgeLimit){
          current.push(...bridge.slice(1));
        }else {transitions.push({fromFixIndex:n-1,toFixIndex:n,dtSeconds:dt,gpsMeters:displacement,reason:'candidate_switch_bridge_failed',bridgeGapMeters:gap,bridgeLimitMeters:bridgeLimit,bridgePathMeters:bridge?gpsPathLength(bridge):null,previousWay:lastCandidate.seg.way,nextWay:bestStart.seg.way});flush()}
      }
    }
    if(!current.length){current.push(...best);runStart=n-1}
    else current.push(...best.slice(1));
    runEnd=n;lastCandidate=bestEnd;
  }
  flush();
  return {matched:local,unmatched,runs,transitions};
}


// Look-ahead map matching: keep multiple candidate histories until the end of
// a contiguous GPS window. Do not commit to a road at each individual fix.
// Every emitted segment must be an actual OSM graph path.
function gpsDisplayTrackWindowed(){
  const raw=state.gpsTrack.filter(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lon));
  const matched=[],runs=[],transitions=[],unmatched=[];
  const frames=raw.map(p=>({p,cs:gpsCandidates(p).slice(0,7)}));
  const selected=new Array(frames.length).fill(null);
  const edges=new Array(frames.length).fill(null);
  let windowStart=0;
  // A window ends only at a truly unmatchable fix or impossible transition.
  function solveWindow(start,end){
    if(end<=start)return;
    const dp=[],parents=[];
    const first=frames[start];
    dp[0]=first.cs.map(c=>c.d*1.6);parents[0]=first.cs.map(()=>null);
    for(let i=start+1;i<=end;i++){
      const prev=frames[i-1],cur=frames[i],dt=(cur.p.t-prev.p.t)/1000;
      const displacement=haversine(prev.p.lat,prev.p.lon,cur.p.lat,cur.p.lon);
      const limit=Math.min(700,Math.max(55,displacement*2.7+40,dt*3+35));
      const costs=cur.cs.map(()=>Infinity),back=cur.cs.map(()=>null);
      for(let j=0;j<cur.cs.length;j++)for(let k=0;k<prev.cs.length;k++){
        if(!Number.isFinite(dp[i-start-1][k]))continue;
        const path=gpsRoadPath(prev.cs[k],cur.cs[j],limit);
        if(!path)continue;
        const distance=gpsPathLength(path);
        if(distance/dt>7.5&&distance>35)continue;
        const emission=cur.cs[j].d*1.6;
        const penalty=Math.abs(distance-displacement)*.5+Math.max(0,distance-displacement*1.7-20)*.7;
        const score=dp[i-start-1][k]+emission+penalty;
        if(score<costs[j]){costs[j]=score;back[j]={k,path}}
      }
      dp.push(costs);parents.push(back);
    }
    const last=dp.at(-1);let j=last.indexOf(Math.min(...last));
    if(j<0||!Number.isFinite(last[j]))return;
    for(let i=end;i>start;i--){const b=parents[i-start][j];if(!b)return;edges[i]=b.path;selected[i]=j;j=b.k}
    selected[start]=j;
  }
  // Split windows where no valid candidate-to-candidate transition exists.
  for(let i=1;i<=frames.length;i++){
    let split=i===frames.length;
    if(!split){
      const a=frames[i-1],b=frames[i],dt=(b.p.t-a.p.t)/1000;
      const displacement=haversine(a.p.lat,a.p.lon,b.p.lat,b.p.lon);
      if(!(dt>0&&dt<150&&displacement<350&&a.cs.length&&b.cs.length))split=true;
      else{
        const limit=Math.min(700,Math.max(55,displacement*2.7+40,dt*3+35));
        let possible=false;
        for(const x of a.cs){if(possible)break;for(const y of b.cs){const path=gpsRoadPath(x,y,limit);if(!path)continue;const d=gpsPathLength(path);if(d/dt<=7.5||d<=35){possible=true;break}}}
        split=!possible;
      }
    }
    if(split){
      solveWindow(windowStart,i-1);
      if(i<frames.length){const a=frames[i-1],b=frames[i];transitions.push({fromFixIndex:i-1,toFixIndex:i,reason:!a.cs.length||!b.cs.length?'no_candidates':'no_plausible_road_transition'});}
      windowStart=i;
    }
  }
  let current=[],start=-1;
  const flush=(end)=>{if(current.length>1){matched.push(current);runs.push({startFixIndex:start,endFixIndex:end,vertices:current.length})}current=[];start=-1};
  for(let i=1;i<frames.length;i++){
    const path=edges[i];
    if(!path){flush(i-1);continue}
    if(!current.length){current=[...path];start=i-1}
    else current.push(...path.slice(1));
  }
  flush(frames.length-1);
  for(let i=0;i<frames.length;i++)if(!frames[i].cs.length)unmatched.push([frames[i].p.lat,frames[i].p.lon]);
  return {matched,unmatched,runs,transitions,matcher:'windowed_multi_candidate'};
}

// Preserve all successfully mapped intervals while merging ONLY adjacent runs
// that can be joined along actual OSM graph edges. Unlike a global Viterbi
// replacement, this operation never drops previously rendered GPS intervals.
function gpsStitchCoverageRuns(result){
  if(!result.runs||result.runs.length!==result.matched.length||result.runs.length<2)
    return result;
  const raw=state.gpsTrack.filter(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lon));
  const lines=[],runs=[],joins=[],intervals=[];
  const near=(a,b)=>haversine(a[0],a[1],b.lat,b.lon);
  for(let i=0;i<result.matched.length;i++){
    const line=result.matched[i],run=result.runs[i];
    if(!lines.length){lines.push(line.slice());runs.push({...run});intervals.push((result.intervals?.[i]||[]).map(x=>({...x})));continue}
    const prev=runs[runs.length-1],tail=lines[lines.length-1].at(-1),head=line[0];
    const from=prev.endFixIndex,to=run.startFixIndex;
    const p=raw[from],q=raw[to];
    const dt=(q?.t-p?.t)/1000;
    const gpsDistance=p&&q?haversine(p.lat,p.lon,q.lat,q.lon):Infinity;
    let bridge=null,bridgeMeters=Infinity;
    // A boundary can share a GPS fix (dt=0) when two neighboring mapped
    // intervals chose different road segments. Require a very short join.
    if(p&&q&&to>=from&&to-from<=4&&dt>=0&&dt<=90&&gpsDistance<=85){
      const direct=haversine(tail[0],tail[1],head[0],head[1]);
      if(direct<.7){bridge=[tail,head];bridgeMeters=direct}
      else {
        const ca=gpsCandidates(p).filter(c=>near(tail,c)<1.5);
        const cb=gpsCandidates(q).filter(c=>near(head,c)<1.5);
        const limit=dt===0?Math.min(35,Math.max(12,direct*2.5)):
          Math.min(110,Math.max(22,gpsDistance*2+18,dt*2.5+10));
        for(const a of ca)for(const b of cb){
          const path=gpsRoadPath(a,b,limit);
          if(!path)continue;
          const length=gpsPathLength(path);
          if(length>limit||(dt>0&&length>35&&length/dt>4.5))continue;
          if(length<bridgeMeters){bridge=path;bridgeMeters=length}
        }
      }
    }
    if(bridge){
      const target=lines[lines.length-1];
      // Graph paths include the exact projected endpoints. No straight-line
      // shortcuts across different roads are inserted.
      const offset=target.length-1;
      target.push(...bridge.slice(1),...line.slice(1));
      intervals[intervals.length-1].push({fromFixIndex:from,toFixIndex:to,
        startVertex:offset,endVertex:offset+bridge.length-1,roadMeters:bridgeMeters,stitch:true});
      intervals[intervals.length-1].push(...(result.intervals?.[i]||[]).map(x=>({...x,
        startVertex:x.startVertex+offset+bridge.length-1,endVertex:x.endVertex+offset+bridge.length-1})));
      prev.endFixIndex=run.endFixIndex;prev.vertices=target.length;
      joins.push({fromFixIndex:from,toFixIndex:to,roadMeters:bridgeMeters});
    }else {lines.push(line.slice());runs.push({...run});intervals.push((result.intervals?.[i]||[]).map(x=>({...x})))}
  }
  const stitched={...result,matched:lines,runs,intervals,stitchJoins:joins,matcher:(result.matcher||'coverage')+'_safe_stitched'};
  stitched.unresolvedGaps=gpsExplainUnresolvedGaps(stitched);
  return stitched;
}
// Local constrained rematching around an unresolved boundary. Keep the remote
// geometry untouched: only replace short tails/heads when a connected OSM
// candidate path can explain the GPS fixes without an impossible walking speed.
function gpsRematchLocalGaps(result){
  if(!result.runs||result.runs.length!==result.matched.length||result.runs.length<2)return result;
  const raw=state.gpsTrack.filter(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lon));
  const lines=result.matched.map(x=>x.slice()),runs=result.runs.map(x=>({...x})),intervals=(result.intervals||[]).map(xs=>xs.map(x=>({...x}))),repairs=[];
  const nearest=(line,c,fromEnd,runIndex,fixIndex)=>{
    // Prefer exact GPS-indexed road vertices instead of searching arbitrary
    // nearest points (which can land on the wrong parallel road).
    const indexed=(intervals[runIndex]||[]).flatMap(x=>[
      ...(x.fromFixIndex===fixIndex?[x.startVertex]:[]),
      ...(x.toFixIndex===fixIndex?[x.endVertex]:[])]);
    if(indexed.length){let best=null;for(const i of indexed){
      if(i<0||i>=line.length)continue;
      const d=haversine(line[i][0],line[i][1],c.lat,c.lon);
      if(!best||d<best.d)best={i,d};
    }return best||{i:-1,d:Infinity}}
    return {i:-1,d:Infinity};
  };
  for(let k=1;k<lines.length;k++){
    const left=runs[k-1],right=runs[k],from=left.endFixIndex,to=right.startFixIndex;
    if(to<from||to-from>2||!raw[from]||!raw[to])continue;
    const aLine=lines[k-1],bLine=lines[k];
    // Search short fixed anchors at most 3 fixes either side of the gap.
    const start=Math.max(left.startFixIndex,from-3),end=Math.min(right.endFixIndex,to+3);
    if(end-start<2||end-start>9)continue;
    const fixes=raw.slice(start,end+1),candidates=fixes.map(p=>gpsCandidates(p));
    if(candidates.some(cs=>!cs.length))continue;
    const leftAnchors=candidates[0].map(c=>({...c,at:nearest(aLine,c,true,k-1,start)})).filter(c=>c.at.d<3);
    const rightAnchors=candidates.at(-1).map(c=>({...c,at:nearest(bLine,c,false,k,end)})).filter(c=>c.at.d<3);
    if(!leftAnchors.length||!rightAnchors.length)continue;
    let winner=null;
    for(const anchor of leftAnchors){
      let states=new Map([[anchor,{cost:anchor.d,path:[],meters:0}]]);
      for(let n=1;n<fixes.length;n++){
        const next=new Map(),dt=(fixes[n].t-fixes[n-1].t)/1000;
        const disp=haversine(fixes[n-1].lat,fixes[n-1].lon,fixes[n].lat,fixes[n].lon);
        if(!(dt>0&&dt<150&&disp<350)){states=new Map();break}
        const limit=Math.min(200,Math.max(45,disp*2.5+25,dt*3+20));
        for(const b of candidates[n]){
          for(const [a,prior] of states){
            const path=gpsRoadPath(a,b,limit);if(!path)continue;
            const meters=gpsPathLength(path);
            if(meters>35&&meters/dt>4.5)continue;
            const cost=prior.cost+b.d+Math.abs(meters-disp)*.5;
            if(!next.has(b)||cost<next.get(b).cost)
              next.set(b,{cost,path:[...prior.path,path],meters:prior.meters+meters});
          }
        }
        states=next;if(!states.size)break;
      }
      for(const endAnchor of rightAnchors){
        const found=[...states].find(([c])=>c.seg===endAnchor.seg&&Math.abs(c.t-endAnchor.t)<1e-9);
        const st=found?.[1];if(!st)continue;
        const aCut=anchor.at.i,bCut=endAnchor.at.i;
        const removed=gpsPathLength(aLine.slice(aCut))+gpsPathLength(bLine.slice(0,bCut+1));
        // Prevent the rematch from erasing substantial mapped road coverage.
        if(Math.abs(st.meters-removed)>Math.max(30,removed*.35))continue;
        const score=st.cost+Math.abs(st.meters-removed);
        if(!winner||score<winner.score)winner={score,aCut,bCut,paths:st.path,meters:st.meters,removed,start,end};
      }
    }
    if(!winner)continue;
    const middle=[];for(const path of winner.paths){if(!middle.length)middle.push(...path);else middle.push(...path.slice(1))}
    if(!middle.length)continue;
    const oldA=aLine.slice(),oldB=bLine.slice();
    const updated=[...aLine.slice(0,winner.aCut),...middle,...bLine.slice(winner.bCut+1)];
    // Anchors must be within 3m of original vertices. Avoid a synthetic
    // diagonal from an anchor to the old line.
    if(haversine(oldA[winner.aCut][0],oldA[winner.aCut][1],middle[0][0],middle[0][1])>0.8||
       haversine(oldB[winner.bCut][0],oldB[winner.bCut][1],middle.at(-1)[0],middle.at(-1)[1])>0.8)continue;
    lines[k-1]=updated;lines.splice(k,1);
    const keptLeft=(intervals[k-1]||[]).filter(x=>x.toFixIndex<=winner.start&&x.endVertex<=winner.aCut);
    const keptRight=(intervals[k]||[]).filter(x=>x.fromFixIndex>=winner.end&&x.startVertex>=winner.bCut)
      .map(x=>({...x,startVertex:x.startVertex-winner.bCut+winner.aCut+middle.length-1,
        endVertex:x.endVertex-winner.bCut+winner.aCut+middle.length-1}));
    intervals[k-1]=[...keptLeft,{fromFixIndex:winner.start,toFixIndex:winner.end,
      startVertex:winner.aCut,endVertex:winner.aCut+middle.length-1,
      roadMeters:winner.meters,rematched:true},...keptRight];intervals.splice(k,1);
    runs[k-1]={...left,endFixIndex:right.endFixIndex,vertices:updated.length};runs.splice(k,1);
    repairs.push({fromFixIndex:from,toFixIndex:to,windowStart:winner.start,windowEnd:winner.end,
      oldMeters:winner.removed,newMeters:winner.meters});k--;
  }
  const out={...result,matched:lines,runs,intervals,localRepairs:repairs};
  out.unresolvedGaps=gpsExplainUnresolvedGaps(out);
  return out;
}
// Indexed, bounded re-routing: replace only the two GPS intervals adjacent to
// a split. Their outer endpoints are held fixed on the original OSM edges.
// A replacement is accepted only if it is continuous, plausible in elapsed
// time, and does not erase meaningful existing street coverage.
function gpsRepairIndexedBoundaries(result){
  if(!result.intervals||!result.runs||result.runs.length<2)return result;
  const raw=state.gpsTrack.filter(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lon));
  const lines=result.matched.map(l=>l.slice()),runs=result.runs.map(r=>({...r}));
  const parts=result.intervals.map(xs=>xs.map(x=>({...x}))),repairs=[],attempts=[];
  for(let k=1;k<lines.length;k++){
    const left=runs[k-1],right=runs[k],f=left.endFixIndex,g=right.startFixIndex;
    const lp=parts[k-1],rp=parts[k];
    const a=lp.at(-1),b=rp[0];
    if(!a||!b||!a.startCandidate||!b.endCandidate||g<f||g-f>1){
      attempts.push({fromFixIndex:f,toFixIndex:g,reason:'no_indexed_anchors'});continue;
    }
    const start=a.fromFixIndex,end=b.toFixIndex;
    if(end-start>4||start>=end){attempts.push({fromFixIndex:f,toFixIndex:g,reason:'window_too_wide'});continue}
    const original=gpsPathLength(lines[k-1].slice(a.startVertex))+gpsPathLength(lines[k].slice(0,b.endVertex+1));
    const first=a.startCandidate,last=b.endCandidate;
    // Include the original snapped points, even if they are outside the
    // closest seven candidates for a noisy GPS fix.
    const candidates=[];
    for(let i=start;i<=end;i++)candidates.push(i===start?[first]:i===end?[last]:gpsCandidates(raw[i]).slice(0,12));
    let states=[{c:first,score:0,meters:0,paths:[]}],failure='no_connected_candidate_path';
    for(let i=start+1;i<=end;i++){
      const dt=(raw[i].t-raw[i-1].t)/1000;
      const disp=haversine(raw[i-1].lat,raw[i-1].lon,raw[i].lat,raw[i].lon);
      if(!(dt>0&&dt<150&&disp<350)){states=[];failure='invalid_gps_time_or_jump';break}
      const next=[];
      for(const c of candidates[i-start]){
        let best=null;
        for(const prev of states){
          const limit=Math.min(700,Math.max(55,disp*2.7+40,dt*3+35));
          const path=gpsRoadPath(prev.c,c,limit);if(!path)continue;
          const meters=gpsPathLength(path);
          if(meters>35&&meters/dt>7.5)continue;
          const score=prev.score+c.d+Math.abs(meters-disp)*.5+Math.max(0,meters-disp*1.7-20)*.7;
          if(!best||score<best.score)best={c,score,meters:prev.meters+meters,paths:[...prev.paths,path]};
        }
        if(best)next.push(best);
      }
      states=next;if(!states.length)break;
    }
    const best=states[0];
    if(!best){attempts.push({fromFixIndex:f,toFixIndex:g,reason:failure});continue}
    const geometry=[];
    for(const path of best.paths){if(!geometry.length)geometry.push(...path);else geometry.push(...path.slice(1))}
    const difference=Math.abs(best.meters-original);
    if(difference>Math.max(30,original*.35)){
      attempts.push({fromFixIndex:f,toFixIndex:g,reason:'coverage_difference',oldMeters:original,newMeters:best.meters});continue;
    }
    const leftLine=lines[k-1],rightLine=lines[k];
    if(!geometry.length||haversine(...leftLine[a.startVertex],...geometry[0])>1||
       haversine(...rightLine[b.endVertex],...geometry.at(-1))>1){
      attempts.push({fromFixIndex:f,toFixIndex:g,reason:'anchor_mismatch'});continue;
    }
    const prefix=leftLine.slice(0,a.startVertex),suffix=rightLine.slice(b.endVertex+1);
    const joined=[...prefix,...geometry,...suffix];
    const offset=prefix.length+geometry.length-b.endVertex-1;
    const keptLeft=lp.filter(x=>x.endVertex<=a.startVertex&&x!==a);
    const keptRight=rp.filter(x=>x.startVertex>=b.endVertex&&x!==b).map(x=>({...x,startVertex:x.startVertex+offset,endVertex:x.endVertex+offset}));
    lines[k-1]=joined;lines.splice(k,1);
    parts[k-1]=[...keptLeft,{fromFixIndex:start,toFixIndex:end,startVertex:prefix.length,endVertex:prefix.length+geometry.length-1,
      roadMeters:best.meters,rematched:true,startCandidate:first,endCandidate:last},...keptRight];parts.splice(k,1);
    runs[k-1]={...left,endFixIndex:right.endFixIndex,vertices:joined.length};runs.splice(k,1);
    repairs.push({fromFixIndex:f,toFixIndex:g,windowStart:start,windowEnd:end,oldMeters:original,newMeters:best.meters});k--;
  }
  const out={...result,matched:lines,runs,intervals:parts,indexedRepairs:repairs,indexedRepairAttempts:attempts};
  out.unresolvedGaps=gpsExplainUnresolvedGaps(out);
  return out;
}
// Re-evaluate several consecutive GPS intervals on both sides of a split.
// Anchors are exact previously drawn OSM projections. Replacing only the
// indexed interior leaves all outside vertices and their fix mapping intact.
function gpsRepairWideBoundaries(result){
  if(!result.intervals||!result.runs||result.runs.length<2)return result;
  const raw=state.gpsTrack.filter(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lon));
  const lines=result.matched.map(l=>l.slice()),runs=result.runs.map(r=>({...r}));
  const parts=result.intervals.map(xs=>xs.map(x=>({...x}))),repairs=[],attempts=[];
  for(let k=1;k<lines.length;k++){
    const left=runs[k-1],right=runs[k],f=left.endFixIndex,g=right.startFixIndex;
    const lp=parts[k-1],rp=parts[k];
    if(!lp?.length||!rp?.length||g<f||g-f>2){attempts.push({fromFixIndex:f,toFixIndex:g,reason:'missing_indexed_boundary'});continue}
    let winner=null,checked=0,connected=0,rejected=0;
    for(const width of [2,4,6,9]){
      const la=lp[Math.max(0,lp.length-width)],rb=rp[Math.min(rp.length-1,width-1)];
      if(!la.startCandidate||!rb.endCandidate)continue;
      const start=la.fromFixIndex,end=rb.toFixIndex;
      if(end-start<2||end-start>22||!raw[start]||!raw[end])continue;
      checked++;
      const candidateSets=[];
      for(let i=start;i<=end;i++)candidateSets.push(i===start?[la.startCandidate]:i===end?[rb.endCandidate]:gpsCandidates(raw[i]).slice(0,12));
      if(candidateSets.some(cs=>!cs.length))continue;
      let states=[{c:la.startCandidate,score:0,meters:0,paths:[]}];
      for(let i=start+1;i<=end;i++){
        const dt=(raw[i].t-raw[i-1].t)/1000,disp=haversine(raw[i-1].lat,raw[i-1].lon,raw[i].lat,raw[i].lon);
        if(!(dt>0&&dt<150&&disp<350)){states=[];break}
        const limit=Math.min(700,Math.max(55,disp*2.7+40,dt*3+35));
        const next=[];
        for(const b of candidateSets[i-start]){
          let best=null;
          for(const prev of states){
            const path=gpsRoadPath(prev.c,b,limit);if(!path)continue;
            const meters=gpsPathLength(path);
            if(meters>35&&meters/dt>7.5)continue;
            const score=prev.score+b.d+Math.abs(meters-disp)*.5+Math.max(0,meters-disp*1.7-20)*.7;
            if(!best||score<best.score)best={c:b,score,meters:prev.meters+meters,paths:[...prev.paths,path]};
          }
          if(best)next.push(best);
        }
        states=next;if(!states.length)break;
      }
      if(!states.length)continue;
      connected++;
      const best=states.reduce((a,b)=>b.score<a.score?b:a);
      const oldLeft=lines[k-1],oldRight=lines[k];
      const original=gpsPathLength(oldLeft.slice(la.startVertex))+gpsPathLength(oldRight.slice(0,rb.endVertex+1));
      if(Math.abs(best.meters-original)>Math.max(30,original*.35)){rejected++;continue}
      const middle=[];for(const path of best.paths){if(!middle.length)middle.push(...path);else middle.push(...path.slice(1))}
      if(middle.length<2||haversine(...oldLeft[la.startVertex],...middle[0])>1||haversine(...oldRight[rb.endVertex],...middle.at(-1))>1){rejected++;continue}
      const score=best.score+Math.abs(best.meters-original)*2;
      if(!winner||score<winner.score)winner={score,start,end,la,rb,middle,meters:best.meters,original};
    }
    if(!winner){attempts.push({fromFixIndex:f,toFixIndex:g,reason:connected?'coverage_or_anchor_rejected':'no_plausible_wide_path',windowsChecked:checked,connectedWindows:connected,rejectedWindows:rejected});continue}
    const {start,end,la,rb,middle,meters,original}=winner;
    const prefix=lines[k-1].slice(0,la.startVertex),suffix=lines[k].slice(rb.endVertex+1);
    const joined=[...prefix,...middle,...suffix],offset=prefix.length+middle.length-rb.endVertex-1;
    const keepLeft=lp.filter(x=>x.endVertex<=la.startVertex&&x!==la);
    const keepRight=rp.filter(x=>x.startVertex>=rb.endVertex&&x!==rb).map(x=>({...x,startVertex:x.startVertex+offset,endVertex:x.endVertex+offset}));
    lines[k-1]=joined;lines.splice(k,1);
    parts[k-1]=[...keepLeft,{fromFixIndex:start,toFixIndex:end,startVertex:prefix.length,endVertex:prefix.length+middle.length-1,roadMeters:meters,rematched:true,wideRematched:true,startCandidate:la.startCandidate,endCandidate:rb.endCandidate},...keepRight];parts.splice(k,1);
    runs[k-1]={...left,endFixIndex:right.endFixIndex,vertices:joined.length};runs.splice(k,1);
    repairs.push({fromFixIndex:f,toFixIndex:g,windowStart:start,windowEnd:end,oldMeters:original,newMeters:meters});k--;
  }
  const out={...result,matched:lines,runs,intervals:parts,wideRepairs:repairs,wideRepairAttempts:attempts};
  out.unresolvedGaps=gpsExplainUnresolvedGaps(out);return out;
}
// Jointly optimize the road candidate at each GPS fix across a boundary window.
// A single candidate is shared by the incoming and outgoing interval. Unlike
// independent pair selection this cannot teleport between parallel roads at
// the same GPS timestamp. The existing indexed intervals are the coverage
// contract: outside the window geometry is never changed.
function gpsOptimizeJointBoundaryWindows(result){
  if(!result.intervals||!result.runs||result.runs.length<2)return result;
  const raw=state.gpsTrack.filter(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lon));
  const lines=result.matched.map(l=>l.slice()),runs=result.runs.map(r=>({...r}));
  const parts=result.intervals.map(xs=>xs.map(x=>({...x}))),attempts=[],repairs=[];
  for(let k=1;k<lines.length;k++){
    const left=runs[k-1],right=runs[k],lp=parts[k-1],rp=parts[k];
    const f=left.endFixIndex,g=right.startFixIndex;
    if(!lp?.length||!rp?.length||g<f||g-f>2){attempts.push({fromFixIndex:f,toFixIndex:g,reason:'missing_interval_mapping'});continue}
    let winner=null,reason='no_joint_connected_path';
    for(const width of [3,6,10,15,24]){
      const la=lp[Math.max(0,lp.length-width)],rb=rp[Math.min(rp.length-1,width-1)];
      if(!la.startCandidate||!rb.endCandidate)continue;
      const start=la.fromFixIndex,end=rb.toFixIndex;
      if(end-start<2||end-start>54||!raw[start]||!raw[end])continue;
      const sets=[];
      for(let i=start;i<=end;i++)sets.push(i===start?[la.startCandidate]:i===end?[rb.endCandidate]:gpsCandidates(raw[i]));
      if(sets.some(cs=>!cs.length)){reason='no_candidates';continue}
      // Viterbi dynamic programming with one state per shared GPS fix, not
      // one independent pair of candidates per GPS interval.
      let states=[{c:sets[0][0],score:0,meters:0,paths:[],intervalMeters:[],candidates:[sets[0][0]]}];
      for(let i=start+1;i<=end;i++){
        const dt=(raw[i].t-raw[i-1].t)/1000;
        const disp=haversine(raw[i-1].lat,raw[i-1].lon,raw[i].lat,raw[i].lon);
        if(!(dt>0&&dt<150&&disp<350)){states=[];reason='invalid_time_or_jump';break}
        const limit=Math.min(700,Math.max(55,disp*2.7+40,dt*3+35));
        const next=[];
        for(const b of sets[i-start]){
          let best=null;
          for(const st of states){
            const path=gpsRoadPath(st.c,b,limit);if(!path)continue;
            const meters=gpsPathLength(path);
            if(meters>35&&meters/dt>7.5)continue;
            const score=st.score+b.d+Math.abs(meters-disp)*.5+
              Math.max(0,meters-disp*1.7-20)*.7;
            if(!best||score<best.score)best={c:b,score,meters:st.meters+meters,
              paths:[...st.paths,path],intervalMeters:[...st.intervalMeters,meters],
              candidates:[...st.candidates,b]};
          }
          if(best)next.push(best);
        }
        states=next;
        if(!states.length)break;
      }
      if(!states.length)continue;
      const best=states.reduce((a,b)=>b.score<a.score?b:a);
      const oldLeft=lines[k-1],oldRight=lines[k];
      const original=gpsPathLength(oldLeft.slice(la.startVertex))+
        gpsPathLength(oldRight.slice(0,rb.endVertex+1));
      // Do not trade already-mapped street coverage for a smaller run count.
      if(Math.abs(best.meters-original)>Math.max(30,original*.35)){
        reason='coverage_difference';continue;
      }
      const middle=[];for(const path of best.paths){
        if(!middle.length)middle.push(...path);else middle.push(...path.slice(1));
      }
      if(middle.length<2||haversine(...oldLeft[la.startVertex],...middle[0])>1||
        haversine(...oldRight[rb.endVertex],...middle.at(-1))>1){reason='anchor_mismatch';continue}
      const score=best.score+Math.abs(best.meters-original)*2;
      if(!winner||score<winner.score)winner={score,start,end,la,rb,best,middle,original};
    }
    if(!winner){attempts.push({fromFixIndex:f,toFixIndex:g,reason});continue}
    const {start,end,la,rb,best,middle,original}=winner;
    const prefix=lines[k-1].slice(0,la.startVertex),suffix=lines[k].slice(rb.endVertex+1);
    const joined=[...prefix,...middle,...suffix];
    const keepLeft=lp.filter(x=>x.endVertex<=la.startVertex&&x!==la);
    const offset=prefix.length+middle.length-rb.endVertex-1;
    const keepRight=rp.filter(x=>x.startVertex>=rb.endVertex&&x!==rb)
      .map(x=>({...x,startVertex:x.startVertex+offset,endVertex:x.endVertex+offset}));
    // Preserve exact per-fix interval mapping, not one coarse replacement span.
    const replacements=[];let vertex=prefix.length;
    for(let i=0;i<best.paths.length;i++){
      const path=best.paths[i],begin=vertex;
      vertex+=path.length-(i===0?0:1);
      replacements.push({fromFixIndex:start+i,toFixIndex:start+i+1,
        startVertex:begin-(i?1:0),endVertex:vertex-1,
        roadMeters:best.intervalMeters[i],jointOptimized:true,
        startCandidate:best.candidates[i],endCandidate:best.candidates[i+1]});
    }
    lines[k-1]=joined;lines.splice(k,1);
    parts[k-1]=[...keepLeft,...replacements,...keepRight];parts.splice(k,1);
    runs[k-1]={...left,endFixIndex:right.endFixIndex,vertices:joined.length};runs.splice(k,1);
    repairs.push({fromFixIndex:f,toFixIndex:g,windowStart:start,windowEnd:end,
      oldMeters:original,newMeters:best.meters});k--;
  }
  const out={...result,matched:lines,runs,intervals:parts,jointRepairs:repairs,jointRepairAttempts:attempts};
  out.unresolvedGaps=gpsExplainUnresolvedGaps(out);return out;
}
// Explain unresolved boundaries using the actual rendered endpoint candidates.
// A graph component being connected does not imply a plausible pedestrian
// transition: parallel roads can be separated by a >100m detour.
function gpsExplainUnresolvedGaps(result){
  if(!result.runs||result.runs.length!==result.matched.length)return [];
  const raw=state.gpsTrack.filter(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lon));
  const out=[];
  for(let i=1;i<result.runs.length;i++){
    const left=result.runs[i-1],right=result.runs[i];
    const p=raw[left.endFixIndex],q=raw[right.startFixIndex];
    if(!p||!q)continue;
    const tail=result.matched[i-1].at(-1),head=result.matched[i][0];
    const dt=(q.t-p.t)/1000;
    const straight=haversine(tail[0],tail[1],head[0],head[1]);
    let shortest=Infinity,fromWay=null,toWay=null;
    const ca=gpsCandidates(p).filter(c=>haversine(c.lat,c.lon,tail[0],tail[1])<1.5);
    const cb=gpsCandidates(q).filter(c=>haversine(c.lat,c.lon,head[0],head[1])<1.5);
    for(const a of ca)for(const b of cb){
      const path=gpsRoadPath(a,b,250);
      if(!path)continue;
      const meters=gpsPathLength(path);
      if(meters<shortest){shortest=meters;fromWay=a.seg.way;toWay=b.seg.way}
    }
    const maxAllowed=dt===0?Math.min(35,Math.max(12,straight*2.5)):
      Math.min(110,Math.max(22,haversine(p.lat,p.lon,q.lat,q.lon)*2+18,dt*2.5+10));
    const reason=dt===0&&shortest>maxAllowed?'same_fix_incompatible_road_snap':
      !Number.isFinite(shortest)?'no_road_path_within_250m':
      shortest>maxAllowed?'road_detour_exceeds_limit':
      dt>0&&shortest>35&&shortest/dt>4.5?'implausible_pedestrian_speed':
      'not_stitched_check_endpoint_precision';
    out.push({fromFixIndex:left.endFixIndex,toFixIndex:right.startFixIndex,
      dtSeconds:dt,endpointMeters:straight,roadMeters:Number.isFinite(shortest)?shortest:null,
      maxAllowedMeters:maxAllowed,fromWay,toWay,reason});
  }
  return out;
}
function gpsValidateIntervalMapping(result){
  if(!result.intervals||result.intervals.length!==result.matched.length)return false;
  return result.intervals.every((parts,k)=>parts.every(x=>
    Number.isInteger(x.fromFixIndex)&&Number.isInteger(x.toFixIndex)&&
    Number.isInteger(x.startVertex)&&Number.isInteger(x.endVertex)&&
    x.startVertex>=0&&x.endVertex>=x.startVertex&&x.endVertex<result.matched[k].length));
}
function gpsPathLengthLines(lines){return lines.reduce((sum,line)=>sum+gpsPathLength(line),0)}
function gpsDisplayTrack(){
  const matched=[],unmatched=[],breaks=[];
  const raw=state.gpsTrack.filter(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lon));
  if(!raw.length||!state.osmRoadSegments.length)return {matched,unmatched:raw.map(p=>[p.lat,p.lon]),breaks};
  // Viterbi: score the complete sequence, including candidate-to-candidate
  // road travel. Never substitute a straight line when the OSM graph is broken.
  const frames=[];
  for(let i=0;i<raw.length;i++){
    const p=raw[i],cs=gpsCandidates(p).slice(0,4),prior=frames.at(-1);
    const dt=prior?(p.t-prior.p.t)/1000:0;
    const displacement=prior?haversine(prior.p.lat,prior.p.lon,p.lat,p.lon):0;
    const states=cs.map(c=>{
      const sigma=Math.max(8,Math.min(40,(Number(p.accuracy)||16)*.8));
      return {score:Math.pow(c.d/sigma,2)*5,from:-1,path:null};
    });
    if(prior&&dt>0&&dt<150&&displacement<350){
      const limit=Math.min(700,Math.max(55,displacement*2.7+40,dt*3+35));
      const base=Math.min(...prior.states.map(x=>x.score));
      for(let j=0;j<cs.length;j++){
        const emission=states[j].score;
        states[j].score=base+emission+85; // restarting is allowed, but costly
        for(let k=0;k<prior.cs.length;k++){
          if(!Number.isFinite(prior.states[k].score))continue;
          const path=gpsRoadPath(prior.cs[k],cs[j],limit);
          if(!path)continue;
          const distance=gpsPathLength(path);
          if(distance/dt>7.5&&distance>35)continue;
          const transition=Math.abs(distance-displacement)*.38+
            Math.max(0,distance-displacement*1.7-20)*.7;
          const score=prior.states[k].score+emission+transition;
          if(score<states[j].score)states[j]={score,from:k,path};
        }
      }
    }else if(prior){
      const base=Math.min(...prior.states.map(x=>x.score));
      for(const st of states)st.score+=base+85;
    }
    frames.push({p,cs,states});
  }
  // Trace the best path backwards, respecting each explicit restart. A local
  // nearest-road decision cannot overwrite this globally optimized sequence.
  let i=frames.length-1;
  while(i>=0){
    const f=frames[i];
    if(!f.cs.length){unmatched.push([f.p.lat,f.p.lon]);breaks.push({index:i,reason:'no_road_candidate'});i--;continue}
    let j=f.states.reduce((best,st,k)=>st.score<f.states[best].score?k:best,0);
    const parts=[];let end=i;
    while(i>=0&&j>=0){
      const st=frames[i].states[j];
      if(st.path&&st.from>=0)parts.push(st.path);
      const next=st.from;i--;j=next;
    }
    if(parts.length){
      const line=[];
      for(const path of parts.reverse()){
        if(!line.length)line.push(...path);
        else line.push(...path.slice(1));
      }
      if(line.length>1)matched.push(line);
    }
    if(i>=0)breaks.push({index:i+1,next:end,reason:'no_plausible_connected_transition'});
  }
  matched.reverse();breaks.reverse();
  // Preserve road coverage if global Viterbi prefers too many restarts.
  // Both alternatives use only connected OSM geometry, never raw straight lines.
  const continuous=gpsDisplayTrackLocal();
  const coverage=gpsDisplayTrackLocalCoverage();
  const consistentVariants=[0.25,0.5,1,2,4,8].map(weight=>({weight,result:gpsDisplayTrackLocalCoverage(weight)}));
  const safeConsistent=consistentVariants.filter(x=>
    gpsPathLengthLines(x.result.matched)>=gpsPathLengthLines(coverage.matched)*.98&&
    x.result.matched.length<coverage.matched.length)
    .sort((a,b)=>a.result.matched.length-b.result.matched.length||
      gpsPathLengthLines(b.result.matched)-gpsPathLengthLines(a.result.matched))[0]?.result;
  const connected=gpsDisplayTrackLocalCoverageConnected();
  const windowed=gpsDisplayTrackWindowed();
  // Prefer a continuous road-constrained trace only when it retains coverage.
  // Fewer lines alone is not evidence of improvement if walked streets disappear.
  // Reject continuity-only matches that silently discard substantial walked
  // coverage. A smaller number of polylines is not enough to select a trace.
  const continuousMeters=gpsPathLengthLines(continuous.matched);
  const coverageMeters=gpsPathLengthLines(coverage.matched);
  const connectedMeters=gpsPathLengthLines(connected.matched);
  const windowedMeters=gpsPathLengthLines(windowed.matched);
  // Prefer a connection-aware trace only when it preserves >=98% of the
  // coverage baseline. Never hide 850m of streets just to remove gaps.
  const local=safeConsistent || (windowedMeters>=coverageMeters*0.98&&windowed.matched.length<coverage.matched.length?windowed:
    connectedMeters>=coverageMeters*0.98&&connected.matched.length<coverage.matched.length?connected:
    continuousMeters>=coverageMeters*0.98?continuous:coverage);
  const length=lines=>lines.reduce((sum,line)=>sum+gpsPathLength(line),0);
  if(length(local.matched)>length(matched)*1.05)
    return gpsOptimizeJointBoundaryWindows(gpsRepairWideBoundaries(gpsRepairIndexedBoundaries(gpsRematchLocalGaps(gpsStitchCoverageRuns({matched:local.matched,unmatched:local.unmatched,breaks,runs:local.runs,intervals:local.intervals,transitions:local.transitions,matcher:local===safeConsistent?'shared_fix_consistent':local===windowed?'windowed_multi_candidate':local===connected?'connected_candidate_coverage_safe':local===continuous?'continuity_aware':'coverage_preserving_fallback'}))))); 
  return {matched,unmatched,breaks,matcher:'viterbi'};
}
// Show ONLY graph-connected street geometry as a line. Never join raw GPS fixes
// across buildings: a straight raw fallback was the cause of misleading traces.
let gpsRenderTimer=null;
function scheduleGpsTrackDraw(){
  if(gpsRenderTimer!==null)return;
  gpsRenderTimer=setTimeout(()=>{gpsRenderTimer=null;drawGpsTrack()},900);
}
// Keep measured GPS visible independently of OSM availability or matcher failures.
// A raw GPS line is evidence of measured positions, not proof of the street walked.
function gpsRawLines(points){
  const lines=[];let line=[];
  const flush=()=>{if(line.length>1)lines.push(line);line=[]};
  for(const p of points){
    if(!Number.isFinite(p.lat)||!Number.isFinite(p.lon))continue;
    const prev=line.length?line._last:null;
    if(prev){
      const dt=(Number(p.t)-Number(prev.t))/1000;
      const distance=haversine(prev.lat,prev.lon,p.lat,p.lon);
      // Avoid fictitious connectors across missing fixes, stale sessions or GPS jumps.
      if(!Number.isFinite(dt)||dt<=0||dt>120||distance>Math.max(100,dt*5+30))flush();
    }
    line.push([p.lat,p.lon]);line._last=p;
  }
  flush();return lines;
}
let gpsMatchLastCount=-1,gpsMatchLastRoadCount=-1,gpsMatchLastAt=0,gpsMatchCached=[];
function drawGpsTrack(){
  const raw=state.gpsTrack.filter(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lon));
  // Draw the complete measured track first. This must never depend on OSM.
  const rawLines=gpsRawLines(raw);
  const rawLayer=rawLines.length?L.polyline(rawLines,{color:'#f97316',weight:5,opacity:.9,
    lineCap:'round',lineJoin:'round',interactive:false}).addTo(map):null;
  const pointLayer=raw.length?L.circleMarker([raw.at(-1).lat,raw.at(-1).lon],{
    radius:5,color:'#9a3412',fillColor:'#fb923c',fillOpacity:1,weight:2,interactive:false}).addTo(map):null;
  // Do not destroy the visible track before its replacement is ready.
  state.layers.gpsRawTrack?.remove();state.layers.gpsRawTrack=rawLayer;
  state.layers.gpsTrackPoint?.remove();state.layers.gpsTrackPoint=pointLayer;
  state.layers.gpsUnmatched?.remove();state.layers.gpsUnmatched=null;
  if(!raw.length){state.layers.gpsTrack?.remove();state.layers.gpsTrack=null;gpsMatchCached=[];gpsMatchLastCount=-1;return}
  // Road matching can be costly on Android. Recalculate periodically, but
  // keep the measured line continuously visible between calculations.
  const now=Date.now(),roadCount=state.osmRoadSegments.length;
  if(gpsMatchLastCount>raw.length){gpsMatchCached=[];gpsMatchLastCount=-1;}
  const shouldMatch=roadCount>0&&(gpsMatchLastRoadCount!==roadCount||
    (gpsMatchLastCount!==raw.length&&now-gpsMatchLastAt>=12000));
  if(shouldMatch){
    gpsMatchLastAt=now;gpsMatchLastCount=raw.length;gpsMatchLastRoadCount=roadCount;
    try{gpsMatchCached=gpsDisplayTrack().matched||[]}
    catch(e){console.warn('GPS道路補正失敗（GPS実測線は維持）',e)}
  }
  const matchedLayer=gpsMatchCached.length?L.polyline(gpsMatchCached,{
    color:'#7c3aed',weight:7,opacity:1,lineCap:'round',lineJoin:'round',interactive:false}).addTo(map):null;
  state.layers.gpsTrack?.remove();state.layers.gpsTrack=matchedLayer;
  state.layers.gpsTrackPoint?.bringToFront?.();
  const gpsInfo=$('gpsStatus');
  if(gpsInfo&&raw.length>1&&!gpsMatchCached.length){
    gpsInfo.textContent=`GPS ${raw.length}点を記録済み / オレンジ線は実測軌跡、紫線は道路補正済み区間です。`;
  }
}

async function restoreFieldSession(){const generation=state.kmzUploadGeneration;const x=await sessionGet();if(!x||generation!==state.kmzUploadGeneration)return;state.restoring=true;state.kmzBlob=x.kmzBlob||null;state.geojson=x.geojson||null;state.areaGeojson=x.areaGeojson||null;state.summary=x.summary||null;state.areaInfo=x.areaInfo||{};state.workerId=+x.workerId||1;state.completed=new Set(x.completed||[]);state.fieldGuideLeg=+x.fieldGuideLeg||0;state.activeGuideLeg=+x.activeGuideLeg||0;state.guidePassed=new Set(x.guidePassed||[]);restoreServicePasses(x.servicePasses);state.gpsTrack=Array.isArray(x.gpsTrack)?x.gpsTrack:[];state.roadPasses=x.roadPasses||{};state.gpsTrackingDesired=!!x.gpsTrackingDesired;if(x.startLat&&x.startLon)setStartPoint(+x.startLat,+x.startLon);if(x.areas?.length){$('area').innerHTML=x.areas.map(a=>`<option>${escapeHtml(a)}</option>`).join('');$('area').disabled=false;$('build').disabled=false;$('refreshOsm').disabled=false;if(x.selectedArea)$('area').value=x.selectedArea}if(state.areaGeojson){renderAreaBoundaries(false);renderHouseholdInfo()}if(state.geojson){renderGeneratedMap();renderMetrics();renderDownloads();prepareWorkerUI();loadWorker(state.workerId,{preserveProgress:true});restoreServicePasses(x.servicePasses);state.guidePassed=new Set(x.guidePassed||[]);drawGuideProgress();drawGpsTrack();status('status','前回の作業状態を自動復元しました。','success')}drawGpsTrack();loadGpsRoadNetwork(x.selectedArea||$('area')?.value);state.restoring=false;/* KMZ auto-upload intentionally disabled: user initiated uploads are authoritative. */if(state.gpsTrackingDesired)setTimeout(()=>startGpsTracking(true),500)}

async function osmDb(){return new Promise((resolve,reject)=>{const req=indexedDB.open('posting-navigator-osm',1);req.onupgradeneeded=()=>{if(!req.result.objectStoreNames.contains('snapshots'))req.result.createObjectStore('snapshots')};req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error)})}
async function getBrowserOsmCache(area){if(!area||!('indexedDB'in window))return null;try{const db=await osmDb();return await new Promise((resolve,reject)=>{const tx=db.transaction('snapshots','readonly'),req=tx.objectStore('snapshots').get(area);req.onsuccess=()=>resolve(req.result||null);req.onerror=()=>reject(req.error)})}catch{return null}}
async function putBrowserOsmCache(area,blob){if(!area||!blob||!('indexedDB'in window))return;try{const db=await osmDb();await new Promise((resolve,reject)=>{const tx=db.transaction('snapshots','readwrite');tx.objectStore('snapshots').put({blob,updatedAt:Date.now()},area);tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error)})}catch{}}
async function restoreBrowserOsmCache(area){const saved=await getBrowserOsmCache(area);if(!saved?.blob)return false;const fd=new FormData();fd.append('area',area);fd.append('cache',saved.blob,'osm_cache.json');try{const r=await fetch(`${API}/api/osm-cache/import`,{method:'POST',body:fd,headers:authHeaders()});return r.ok}catch{return false}}
async function saveServerOsmCacheToBrowser(area){try{const r=await fetch(`${API}/api/osm-cache/export?area=${encodeURIComponent(area)}`,{headers:authHeaders()});if(!r.ok)return false;const blob=await r.blob();await putBrowserOsmCache(area,blob);return true}catch{return false}}
async function updateOsmCacheStatus(){const el=$('osmCacheStatus'),area=$('area')?.value;if(!el||!area||$('area')?.disabled){if(el){el.textContent='未保存';el.className=''}return}const local=await getBrowserOsmCache(area);if(local?.blob){el.textContent='この端末に保存済み';el.className='saved';return}try{const j=await api(`/api/osm-cache/status?area=${encodeURIComponent(area)}`);if(j.exists){el.textContent='Renderに一時保存済み';el.className='server'}else{el.textContent='初回取得が必要';el.className=''}}catch{el.textContent='状態不明';el.className=''}}
function activateTab(name){document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x.dataset.tab===name));document.querySelectorAll('.tabpane').forEach(x=>x.classList.toggle('active',x.id===`tab-${name}`));setTimeout(()=>map.invalidateSize(),50)}
document.querySelectorAll('.tab').forEach(x=>x.onclick=()=>{activateTab(x.dataset.tab);if(x.dataset.tab==='field'&&state.geojson){const wid=+(($('fieldWorker')?.value)||state.workerId||1);loadWorker(wid)}});

async function loadConfig(){try{state.config=await api('/api/config');$('gpsThreshold').value=state.config.gps_threshold_m||18;$('gpsThresholdText').textContent=$('gpsThreshold').value;if(state.sessionToken){try{const me=await api('/api/auth/me');state.user=me.user}catch{state.sessionToken='';localStorage.removeItem('pn_session')}}setupGoogleLogin();if(state.user)loadMyProjects()}catch(e){status('status',`APIに接続できません。docs/config.js のRender URLを確認してください。\n${e.message}`,'error')}}

function setStartPoint(lat,lon,{center=false}={}){
  if(state.layers.start)state.layers.start.remove();
  $('lat').value=(+lat).toFixed(7);$('lon').value=(+lon).toFixed(7);
  state.layers.start=L.marker([lat,lon]).addTo(map).bindPopup('開始地点');
  if(center)map.setView([lat,lon],18);
}
function setStartPickMode(on){state.startPickMode=!!on;const b=$('pickStart');if(b){b.classList.toggle('active',state.startPickMode);b.textContent=state.startPickMode?'地図上の開始地点をクリックしてください':'地図で開始地点を指定'}map.getContainer().classList.toggle('start-pick-mode',state.startPickMode)}
map.on('click',e=>{
  // 最初の1クリックだけは、その地点を開始地点として自動設定する。
  // 2回目以降の通常クリックでは開始地点を動かさない。変更したい場合だけ明示設定モードを使う。
  const hasStartPoint=!!state.layers.start;
  if(hasStartPoint && !state.startPickMode)return;
  setStartPoint(e.latlng.lat,e.latlng.lng);
  if(state.startPickMode)setStartPickMode(false);
  status('status',hasStartPoint?'開始地点を変更しました。':'開始地点を設定しました。以後、通常クリックでは移動しません。','success');
});
$('pickStart').onclick=()=>setStartPickMode(!state.startPickMode);
$('useCurrent').onclick=()=>navigator.geolocation?.getCurrentPosition(p=>{const {latitude,longitude}=p.coords;setStartPoint(latitude,longitude,{center:true})},e=>status('status',`現在地を取得できません: ${e.message}`,'error'),{enableHighAccuracy:true});

// Network diagnosis is independent of KMZ parsing and never changes saved sessions.
async function diagnoseUpload(){
  const btn=$('diagnoseUpload');btn.disabled=true;
  const steps=[];
  const record=(name,value)=>{steps.push(`${name}: ${value}`);$('uploadDiagnostic').textContent=steps.join('\n')};
  const probe=async(name,url,opts)=>{
    const t=Date.now();
    try{
      const r=await fetch(url,{cache:'no-store',...opts});
      const body=(await r.text()).slice(0,500);
      record(name,`HTTP ${r.status} (${Date.now()-t}ms) ${body}`);
      return r.ok;
    }catch(e){record(name,`${e.name}: ${e.message} (${Date.now()-t}ms)`);return false}
  };
  try{
    record('環境',`${location.origin} → ${API} / ${navigator.userAgent}`);
    await probe('GET',`${API}/api/config`);
    await probe('POST JSON',`${API}/api/diagnostics/upload`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({test:true})});
    const fd=new FormData();fd.append('kmz',new Blob(['test'],{type:'application/octet-stream'}),'diagnostic.kmz');
    await probe('POST FormData',`${API}/api/diagnostics/upload`,{method:'POST',body:fd});
    const file=$('kmz').files[0];
    if(!file){record('実KMZ','未選択。上の「1. 区画KMZ」でファイルを選択すると、実ファイル送信診断も行えます。');return}
    record('実KMZ',`ファイル名=${file.name} / サイズ=${file.size} bytes / type=${file.type||'(empty)'}`);
    const actual=new FormData();actual.append('kmz',file,file.name);
    const ok=await probe('実KMZ POST (診断専用・保存なし)',`${API}/api/diagnostics/upload?inspect=1`,{method:'POST',body:actual,mode:'cors'});
    if(!ok){
      try{
        const bytes=await file.arrayBuffer();
        record('ArrayBuffer読込',`${bytes.byteLength} bytes / 先頭=${Array.from(new Uint8Array(bytes.slice(0,4))).map(x=>x.toString(16).padStart(2,'0')).join('')}`);
        const rawOk=await probe('実KMZ RAW POST (保存なし)',`${API}/api/diagnostics/raw`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:bytes,mode:'cors'});
        record('判定',rawOk?'RAW送信成功。通常読み込みではRAW経路に自動切替します。':'RAW送信も失敗。ファイル本体の送信が端末・ネットワークで遮断される可能性があります。');
      }catch(e){record('RAW診断',`${e.name}: ${e.message}`)}
    }else record('判定','実KMZのFormData転送は成功しました。');
  }finally{btn.disabled=false}
}
$('diagnoseUpload').onclick=diagnoseUpload;
$('kmz').onchange=async()=>{
  const f=$('kmz').files[0];if(!f)return;
  const generation=++state.kmzUploadGeneration;
  status('status','KMZを解析中…');
  const fd=new FormData();fd.append('kmz',f);
  try{
    // Original v1.9.3 upload flow; no automatic upload runs alongside this request.
    const started=Date.now();console.info('[KMZ] upload start',{name:f.name,bytes:f.size,api:API});status('status',`KMZ送信中: ${(f.size/1024).toFixed(1)} KB → ${API}/api/areas`);let r;let uploadMethod='FormData';try{
      r=await fetch(`${API}/api/areas?compact=1`,{method:'POST',body:fd,cache:'no-store',mode:'cors'});
    }catch(formError){
      console.warn('[KMZ] multipart upload failed; retrying raw binary',formError);
      status('status','FormData送信に失敗。KMZをバイナリ形式で再送信中…');
      const bytes=await f.arrayBuffer();
      uploadMethod='RAW';
      r=await fetch(`${API}/api/areas/raw?compact=1`,{method:'POST',body:bytes,headers:{'Content-Type':'application/octet-stream'},cache:'no-store',mode:'cors'});
    }console.info('[KMZ] upload method',uploadMethod);console.info('[KMZ] response headers',{status:r.status,elapsed_ms:Date.now()-started});
    status('status',`KMZ応答を受信: HTTP ${r.status} / ${Date.now()-started}ms。解析中…`);const raw=await r.text();console.info('[KMZ] response body received',{bytes:raw.length,elapsed_ms:Date.now()-started});let j;
    try{j=JSON.parse(raw)}catch{throw Error(`API HTTP ${r.status}: ${raw.slice(0,160)}`)}
    if(!r.ok)throw Error(j.error||`HTTP ${r.status}`);
    if(generation!==state.kmzUploadGeneration)return;
    if(!Array.isArray(j.areas)||!j.areas.length)throw Error('KMZに選択可能な町丁目がありません');
    // A successful replacement starts a new area/route session, without deleting GPS history in storage.
    clearRouteLayers();state.segments=[];state.segmentLengths=[];state.jobId=null;state.navLegs=[];state.layers.areas?.remove();state.layers.areas=null;['downloads','routeGuide','nextGuide','metrics','householdProgress'].forEach(id=>$(id)?.classList.add('hidden'));state.uploadId=j.upload_id;state.kmzBlob=f;state.areaGeojson=j.area_geojson||null;
    state.areaInfo=j.area_info||{};state.geojson=null;state.summary=null;state.projectId=null;state.shareCode=null;
    state.completed=new Set();state.roadPasses={};state.servicePasses=new Map();state.guidePassed=new Set();state.activeGuideLeg=0;state.fieldGuideLeg=0;
    $('area').innerHTML=j.areas.map(x=>`<option>${escapeHtml(x)}</option>`).join('');
    $('area').disabled=false;$('build').disabled=false;$('refreshOsm').disabled=false;
    status('status',`${j.areas.length}件の区画を読み込みました。②から選択できます。`,'success');
    saveFieldSessionSoon();
    try{renderAreaBoundaries(true);renderHouseholdInfo()}catch(err){console.error('境界描画失敗',err)}
    updateOsmCacheStatus();
    // Large polygon payload is loaded separately; the dropdown is usable immediately.
    fetch(`${API}/api/areas/${encodeURIComponent(state.uploadId)}/details`,{cache:'no-store'}).then(r=>{if(!r.ok)throw Error(`HTTP ${r.status}`);return r.json()}).then(details=>{if(generation!==state.kmzUploadGeneration)return;state.areaGeojson=details.area_geojson||null;state.areaInfo=details.area_info||{};renderAreaBoundaries(true);renderHouseholdInfo();saveFieldSessionSoon()}).catch(err=>console.warn('境界データの追加取得に失敗（町丁目選択は利用可能）',err));
  }catch(e){
    if(generation!==state.kmzUploadGeneration)return;
    console.error('KMZアップロード',e,'API:',API,'origin:',location.origin);
    status('status',`KMZ読み込み失敗: ${e.name}: ${e.message} / API: ${API} / 上の「KMZ通信を診断」で実KMZ送信を確認できます。`,'error');
  }
};
$('area').onchange=()=>{renderAreaBoundaries(true);renderHouseholdInfo();updateOsmCacheStatus();saveFieldSessionSoon()};

function renderAreaBoundaries(fitSelected=false){
  if(state.layers.areas){state.layers.areas.remove();state.layers.areas=null}
  if(!state.areaGeojson)return;
  const selected=$('area').value;
  let selectedLayer=null;
  state.layers.areas=L.geoJSON(state.areaGeojson,{
    style:f=>{
      const active=f.properties?.name===selected;
      return active
        ? {color:'#64748b',weight:4,opacity:.82,fillColor:'#cbd5e1',fillOpacity:.06}
        : {color:'#64748b',weight:2.5,opacity:.62,fillColor:'#94a3b8',fillOpacity:.025};
    },
    onEachFeature:(f,layer)=>{
      const name=f.properties?.name||'';
      layer.bindTooltip(name,{sticky:true,direction:'center',className:'area-tooltip'});
      if(name===selected)selectedLayer=layer;
    }
  }).addTo(map);
  if(fitSelected&&selectedLayer){
    const b=selectedLayer.getBounds();
    if(b.isValid())map.fitBounds(b,{padding:[35,35]});
    selectedLayer.bringToFront?.();
  }
}

function currentHouseholds(){const v=state.areaInfo?.[$('area').value]?.households;return Number.isFinite(+v)?+v:null}
function renderHouseholdInfo(){const n=currentHouseholds(),box=$('householdBox');if(!box)return;if(n==null){box.classList.add('hidden');return}box.classList.remove('hidden');$('householdTotal').textContent=n.toLocaleString('ja-JP')+'世帯'}
function updateHouseholdProgress(pct){const total=currentHouseholds(),box=$('householdProgress');if(!box||total==null){box?.classList.add('hidden');return}const done=Math.max(0,Math.min(total,Math.round(total*(pct||0)/100)));box.classList.remove('hidden');$('householdDone').textContent=done.toLocaleString('ja-JP')+'世帯';$('householdRemain').textContent=(total-done).toLocaleString('ja-JP')+'世帯'}

async function runRouteBuild(forceRefresh=false){
  const area=$('area').value;
  status('status',forceRefresh?'最新OSMを取得して巡回計算中…':'保存済みOSMを確認して巡回計算中…');
  $('build').disabled=true;$('refreshOsm').disabled=true;$('downloads').classList.add('hidden');
  try{
    // 更新時も先に端末キャッシュをRenderへ戻す。最新取得が失敗した場合の安全網になる。
    await restoreBrowserOsmCache(area);
    const j=await api('/api/build',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({upload_id:state.uploadId,area,workers:1,start_lat:$('lat').value,start_lon:$('lon').value,offline_fallback:$('fallback').checked,force_osm_refresh:!!forceRefresh})});
    state.jobId=j.job_id;state.geojson=j.geojson;state.summary=j.summary;renderGeneratedMap();renderMetrics();renderDownloads();
    const saved=await saveServerOsmCacheToBrowser(area);await loadGpsRoadNetwork(area);await updateOsmCacheStatus();
    const mode=j.summary.data_mode||'osm';
    const modeText=mode==='osm'?'最新OSM':mode==='osm-cache'?'保存済みOSM':mode==='osm-cache-fallback'?'保存済みOSM（更新失敗のため既存データ）':mode;
    status('status',`生成完了（道路データ: ${modeText}${saved?'・端末保存済み':''}）`,'success');
    await createSharedProject();prepareWorkerUI();saveFieldSessionSoon();activateTab('build');
  }catch(e){status('status',e.message,'error')}finally{$('build').disabled=false;$('refreshOsm').disabled=!state.uploadId}
}
// Route display reset is deliberately separate from GPS track/history reset.
function clearDisplayedRoute(){
  if(!confirm('表示中の巡回ルートを消去しますか？ GPS軌跡は残ります。'))return;
  clearRouteLayers();
  ['once','roundtrip','gps'].forEach(k=>{if(state.layers[k]){state.layers[k].remove();state.layers[k]=null}});
  state.geojson=null;state.summary=null;state.jobId=null;state.projectId=null;state.shareCode=null;
  state.navLegs=[];state.segments=[];state.segmentLengths=[];state.completed=new Set();
  state.roadPasses={};state.servicePasses=new Map();state.guidePassed=new Set();
  state.activeGuideLeg=0;state.fieldGuideLeg=0;
  if(state.syncTimer){clearInterval(state.syncTimer);state.syncTimer=null}
  ['routeGuide','nextGuide','downloads','metrics','householdProgress','projectBox'].forEach(id=>$(id)?.classList.add('hidden'));
  $('fieldPercent').textContent='0%';$('fieldDistance').textContent='0 / 0 km';
  $('fieldWorkerName').textContent='未選択';
  drawGpsTrack();saveFieldSessionSoon();
  status('status','巡回ルート表示を消去しました。GPS軌跡は保持しています。','success');
}
$('clearRouteDisplay').onclick=clearDisplayedRoute;
function setMapFullscreen(on){
  document.body.classList.toggle('map-fullscreen',!!on);
  const btn=$('toggleMapFullscreen');
  btn.textContent=on?'地図を縮小':'地図を全画面表示';
  btn.setAttribute('aria-pressed',String(!!on));
  setTimeout(()=>map.invalidateSize(),80);
}
$('toggleMapFullscreen').onclick=()=>setMapFullscreen(!document.body.classList.contains('map-fullscreen'));
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&document.body.classList.contains('map-fullscreen'))setMapFullscreen(false)});
$('build').onclick=()=>runRouteBuild(false);
$('refreshOsm').onclick=()=>runRouteBuild(true);

function routeStepFeatures(){return (state.geojson?.features||[]).filter(f=>f.properties?.kind==='route_step').sort((a,b)=>(a.properties.seq||0)-(b.properties.seq||0))}
function navigationLegFeatures(){return (state.geojson?.features||[]).filter(f=>f.properties?.kind==='navigation_leg').sort((a,b)=>(a.properties.leg||0)-(b.properties.leg||0))}
function renderGeneratedMap(){
  clearRouteLayers();
  const steps=routeStepFeatures(), legs=navigationLegFeatures();
  state.navLegs=legs;state.activeGuideLeg=0;state.guidePassed=new Set();state.servicePasses=new Map();
  if(!map.getPane('routeProgressPane')){const pane=map.createPane('routeProgressPane');pane.style.zIndex='675';pane.style.pointerEvents='none'}
  const hasSteps=steps.length>0;
  state.layers.generated=L.geoJSON(state.geojson,{
    filter:f=>{const k=f.properties?.kind;return k!=='navigation_leg' && !(hasSteps&&(k==='route'||k==='worker_route'))},
    style:f=>{
      const k=f.properties?.kind;
      if(k==='area')return state.areaGeojson?{weight:0,opacity:0,fillOpacity:0}:{weight:4,color:'#64748b',opacity:.82,fillColor:'#cbd5e1',fillOpacity:.06};
      if(k==='road')return{weight:1.0,color:'#94a3b8',opacity:.20};
      if(k==='route_step'){if(f.properties.transfer)return{weight:3,color:'#64748b',opacity:.45,dashArray:'8 8'};if(f.properties.duplicated)return{weight:4,color:'#f59e0b',opacity:.58};return{weight:4,color:'#ef4444',opacity:.38}}
      return{weight:2,color:'#64748b'}
    }
  }).addTo(map);
  renderRouteGuide(legs);
  drawRouteEndpoints(legs.length?legs:steps);
  if(legs.length)focusGuideLeg(0,false);
  const b=state.layers.generated.getBounds();if(b.isValid())map.fitBounds(b,{padding:[15,15]})
}
function clearRouteLayers(){['generated','todo','once','roundtrip','done','gps','directions','sequence','focus','nextPreview','guideProgress'].forEach(k=>{if(state.layers[k]){state.layers[k].remove();state.layers[k]=null}})}
function bearingDeg(a,b){const p=Math.PI/180,y=Math.sin((b[0]-a[0])*p)*Math.cos(b[1]*p),x=Math.cos(a[1]*p)*Math.sin(b[1]*p)-Math.sin(a[1]*p)*Math.cos(b[1]*p)*Math.cos((b[0]-a[0])*p);return(Math.atan2(y,x)/p+360)%360}
function interpolateCoord(a,b,t){return[a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t]}
function arrowSvg(){return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 1.5 4.5 10H9v12h6V10h4.5z"/></svg>'}
function lineLengths(coords){const lens=[];let total=0;for(let i=0;i<coords.length-1;i++){const d=haversine(coords[i][1],coords[i][0],coords[i+1][1],coords[i+1][0]);lens.push(d);total+=d}return{lens,total}}
function coordAtDistance(coords,d){const {lens,total}=lineLengths(coords);if(!coords.length)return null;if(total<=0)return coords[0];d=Math.max(0,Math.min(total,d));let acc=0;for(let i=0;i<lens.length;i++){if(acc+lens[i]>=d){const t=lens[i]?((d-acc)/lens[i]):0;return interpolateCoord(coords[i],coords[i+1],t)}acc+=lens[i]}return coords.at(-1)}
function tangentAtDistance(coords,d){const {total}=lineLengths(coords);const delta=Math.min(12,Math.max(3,total*.12));const a=coordAtDistance(coords,Math.max(0,d-delta)),b=coordAtDistance(coords,Math.min(total,d+delta));return{pt:coordAtDistance(coords,d),bearing:bearingDeg(a,b)}}
function drawRouteEndpoints(features){
  if(state.layers.sequence)state.layers.sequence.remove();const g=L.layerGroup();if(!features.length){state.layers.sequence=g.addTo(map);return}
  const first=features[0].geometry.coordinates[0],lastF=features.at(-1),last=lastF.geometry.coordinates.at(-1);
  L.marker([first[1],first[0]],{interactive:false,icon:L.divIcon({className:'route-end-wrap',html:'<div class="route-end start">START</div>',iconSize:[52,24],iconAnchor:[26,12]})}).addTo(g);
  L.marker([last[1],last[0]],{interactive:false,icon:L.divIcon({className:'route-end-wrap',html:'<div class="route-end goal">GOAL</div>',iconSize:[48,24],iconAnchor:[24,12]})}).addTo(g);
  state.layers.sequence=g.addTo(map);
}
function arrowMarkersForLeg(feature){
  const g=L.layerGroup(),c=feature.geometry?.coordinates||[];if(c.length<2||$('showArrows')?.checked===false)return g;
  const {total}=lineLengths(c);const ds=total<35?[total*.5]:total<90?[total*.35,total*.72]:[total*.22,total*.50,total*.78];
  ds.forEach(d=>{const m=tangentAtDistance(c,d);if(!m?.pt)return;L.marker([m.pt[1],m.pt[0]],{interactive:false,icon:L.divIcon({className:'route-arrow-wrap',html:`<div class="route-arrow selected" style="transform:rotate(${m.bearing}deg)">${arrowSvg()}</div>`,iconSize:[30,30],iconAnchor:[15,15]})}).addTo(g)});return g;
}
function atomicServiceSegments(f){
  const c=f?.geometry?.coordinates||[], out=[];
  const q=p=>`${(+p[0]).toFixed(6)},${(+p[1]).toFixed(6)}`;
  for(let i=0;i<c.length-1;i++){
    const a=c[i],b=c[i+1],qa=q(a),qb=q(b);
    if(qa===qb)continue;
    out.push({key:[qa,qb].sort().join('|'),dir:qa<=qb?'f':'r',geometry:{type:'LineString',coordinates:[a,b]}});
  }
  return out;
}
function routeStepsForGuideLeg(f){const p=f?.properties||{},a=Number(p.start_seq),b=Number(p.end_seq);if(!Number.isFinite(a)||!Number.isFinite(b))return[];return routeStepFeatures().filter(s=>{const q=Number(s.properties?.seq);return q>=Math.min(a,b)&&q<=Math.max(a,b)&&!s.properties?.transfer})}
function recordServiceStep(f){
  const p=f?.properties||{};
  for(const seg of atomicServiceSegments(f)){
    const rec=state.servicePasses.get(seg.key)||{dirs:new Set(),segments:new Map(),complete:false};
    rec.dirs.add(seg.dir);rec.segments.set(seg.dir,seg.geometry);
    if(p.boundary_one_side_service||p.both_sides_single_pass||p.service_mode==='boundary-one-side'||p.service_mode==='narrow-both-sides')rec.complete=true;
    else if(rec.dirs.size>=2)rec.complete=true;
    state.servicePasses.set(seg.key,rec);
  }
}
function drawGuideProgress(){
  if(state.layers.guideProgress)state.layers.guideProgress.remove();
  const g=L.layerGroup();
  for(const rec of state.servicePasses.values()){
    // Draw the physical road once. Blue = one serviced direction, green = both
    // directions (or a one-side-only boundary/narrow road) complete.
    const geom=rec.segments.values().next().value;if(!geom)continue;
    L.geoJSON({type:'Feature',properties:{},geometry:geom},{pane:'routeProgressPane',style:{color:rec.complete?'#16a34a':'#2563eb',weight:rec.complete?11:10,opacity:1,lineCap:'round',lineJoin:'round'}}).addTo(g);
  }
  state.layers.guideProgress=g.addTo(map);
  document.querySelectorAll('.guide-item').forEach((el,i)=>el.classList.toggle('passed',state.guidePassed.has(i)));
}
function markGuideLegPassed(index){
  if(index<0||index>=state.navLegs.length)return;
  state.guidePassed.add(index);const leg=state.navLegs[index];const steps=routeStepsForGuideLeg(leg);
  if(steps.length){for(const step of steps)recordServiceStep(step)}else if(!leg.properties?.transfer){recordServiceStep(leg)}
  drawGuideProgress();if(!state.restoring)saveFieldSessionSoon();
}
function focusGuideLeg(index,fit=true){
  if(!state.navLegs?.length)return;index=Math.max(0,Math.min(state.navLegs.length-1,index));state.activeGuideLeg=index;
  if(state.layers.focus)state.layers.focus.remove();if(state.layers.directions)state.layers.directions.remove();if(state.layers.nextPreview)state.layers.nextPreview.remove();
  const f=state.navLegs[index],c=f.geometry.coordinates,p=f.properties;
  state.layers.focus=L.geoJSON(f,{style:{color:'#2563eb',weight:9,opacity:1,lineCap:'round',lineJoin:'round'}}).addTo(map);
  if(index+1<state.navLegs.length){state.layers.nextPreview=L.geoJSON(state.navLegs[index+1],{style:{color:'#60a5fa',weight:6,opacity:.48,dashArray:'5 7'}}).addTo(map)}
  state.layers.directions=arrowMarkersForLeg(f).addTo(map);
  if($('showNumbers')?.checked!==false){const pt=c[0];L.marker([pt[1],pt[0]],{interactive:false,icon:L.divIcon({className:'route-seq-wrap focus-seq',html:`<div class="route-seq">${p.leg}</div>`,iconSize:[32,32],iconAnchor:[16,16]})}).addTo(state.layers.directions)}
  document.querySelectorAll('.guide-item').forEach((el,i)=>el.classList.toggle('active',i===index));
  const counter=$('guideCounter');if(counter)counter.textContent=`${index+1} / ${state.navLegs.length}`;
  const title=$('guideCurrent');if(title)title.innerHTML=`<b>${p.leg}. ${escapeHtml(p.turn||'進む')}</b><span>${escapeHtml(p.name||'道路に沿って進む')} ・ 約${Math.round(p.length_m||0)}m</span>`;
  $('guidePrev')&&( $('guidePrev').disabled=index===0 );$('guideNext')&&( $('guideNext').disabled=index===state.navLegs.length-1 );
  // Never scroll the guide list automatically: GPS updates must not steal scroll position.
  if(fit){const b=state.layers.focus.getBounds();if(b.isValid())map.fitBounds(b,{padding:[80,80],maxZoom:18})}
}
function renderRouteGuide(legs){
  const box=$('routeGuide');if(!box)return;if(!legs?.length){box.classList.add('hidden');return}
  box.innerHTML=`<h3>開始地点からの最適巡回順</h3><div class="hint">上から1→2→3…の順に進みます。項目をクリックすると、その区間だけ地図上で青く強調します。</div><div class="guide-controller"><button id="guidePrev" class="secondary">← 前へ</button><div><strong id="guideCounter">1 / ${legs.length}</strong><div id="guideCurrent" class="guide-current"></div></div><button id="guideNext" class="secondary">次へ →</button></div><div class="guide-list">${legs.map((f,i)=>`<button class="guide-item" data-index="${i}"><div class="guide-num">${f.properties.leg}</div><div><b>${escapeHtml(f.properties.turn||'進む')} ・ ${Math.round(f.properties.length_m||0)}m</b><span>${escapeHtml(f.properties.component_break_before?(f.properties.instruction||'次の道路群へ移動'): (f.properties.name||f.properties.instruction||'道路に沿って進む'))}</span></div></button>`).join('')}</div>`;
  box.classList.remove('hidden');
  const moveGuideTo=(target)=>{target=Math.max(0,Math.min(state.navLegs.length-1,target));if(target>state.activeGuideLeg){for(let i=state.activeGuideLeg;i<target;i++)markGuideLegPassed(i)}focusGuideLeg(target,true)};
  box.querySelectorAll('.guide-item').forEach(el=>el.onclick=()=>moveGuideTo(+el.dataset.index));
  $('guidePrev').onclick=()=>focusGuideLeg(state.activeGuideLeg-1,true);$('guideNext').onclick=()=>moveGuideTo(state.activeGuideLeg+1);
  focusGuideLeg(0,false);
}
function distanceToFeatureMeters(lat,lon,f){let best=Infinity,c=f?.geometry?.coordinates||[];for(let j=0;j<c.length-1;j++)best=Math.min(best,pointSegmentMeters(lat,lon,c[j][1],c[j][0],c[j+1][1],c[j+1][0]));return best}
function advanceFieldGuide(){if(!state.current||!state.navLegs?.length)return;const cur=Math.max(0,Math.min(state.navLegs.length-1,state.fieldGuideLeg||0));let bestIdx=cur,bestD=distanceToFeatureMeters(state.current.lat,state.current.lon,state.navLegs[cur]);for(let i=cur+1;i<=Math.min(cur+3,state.navLegs.length-1);i++){const d=distanceToFeatureMeters(state.current.lat,state.current.lon,state.navLegs[i]);if(d+4<bestD){bestD=d;bestIdx=i}}const threshold=+$('gpsThreshold').value;if(bestIdx>cur&&bestD<=Math.max(threshold,22)){for(let i=cur;i<bestIdx;i++)markGuideLegPassed(i);state.fieldGuideLeg=bestIdx}else{const c=state.navLegs[cur].geometry.coordinates,end=c[c.length-1],dEnd=haversine(state.current.lat,state.current.lon,end[1],end[0]);if(dEnd<=Math.max(10,threshold*.7)&&cur+1<state.navLegs.length){markGuideLegPassed(cur);state.fieldGuideLeg=cur+1}}}
function nextRouteInstruction(){const box=$('nextGuide');if(!box||!state.navLegs?.length)return;advanceFieldGuide();const idx=Math.max(0,Math.min(state.navLegs.length-1,state.fieldGuideLeg||0)),f=state.navLegs[idx],p=f.properties;box.innerHTML=`<b>現在の案内 ${idx+1} / ${state.navLegs.length}</b><div class="turn">${p.leg}. ${escapeHtml(p.turn||'進む')}</div><div>${escapeHtml(p.component_break_before?(p.instruction||'次の道路群へ移動'):(p.name||'道路に沿って進む'))}${p.component_break_before?'':` ・ 約${Math.round(p.length_m||0)}m`}</div>`;box.classList.remove('hidden');if(state.watchId!==null){state.activeGuideLeg=idx;focusGuideLeg(idx,false)}}
function renderMetrics(){const s=state.summary;$('metrics').innerHTML=`<div class="metric">全体距離<b>${(s.route_length_m/1000).toFixed(2)} km</b></div><div class="metric">対象道路<b>${s.source_edges||0}本</b></div><div class="metric">道路群<b>${s.component_count||1}</b></div><div class="metric">左右配布倍率<b>${(s.route_ratio||s.duplication_ratio||0).toFixed(2)}</b></div><div class="metric">実道路移動<b>${((s.transfer_length_m||0)/1000).toFixed(2)} km</b></div><div class="metric">必要2面超過<b>${((s.excess_over_two_side_m||0)/1000).toFixed(2)} km</b></div>`;$('metrics').classList.remove('hidden')}
function renderDownloads(){const base=`${API}/download/${state.jobId}`;$('downloads').innerHTML=`<label>成果物</label><a href="${base}/posting_navigator_results.zip">一式ZIP</a><a href="${base}/posting_navigator.kmz">統合KMZ</a><a href="${base}/posting_navigator.kml">統合KML</a><a href="${base}/assignments.csv">担当CSV</a><a href="${base}/summary.json">集計JSON</a>`;$('downloads').classList.remove('hidden')}

async function createSharedProject(){try{const j=await api('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:state.jobId})});state.projectId=j.project_id;state.shareCode=j.share_code;localStorage.setItem('pn_last_project',JSON.stringify({projectId:state.projectId,shareCode:state.shareCode}));renderProjectBox();startTeamSync()}catch(e){status('teamStatus',`共有プロジェクトを作成できません: ${e.message}`,'error')}}
function renderProjectBox(){if(!state.projectId)return;$('projectBox').classList.remove('hidden');$('shareCode').textContent=state.shareCode;status('teamStatus','共有中。各担当の進捗は約5秒ごとに同期されます。','success')}
$('copyCode').onclick=async()=>{await navigator.clipboard?.writeText(state.shareCode||'');$('copyCode').textContent='コピー済み';setTimeout(()=>$('copyCode').textContent='コピー',1200)};
$('joinProject').onclick=async()=>{const code=$('joinCode').value.trim().toUpperCase();if(!code)return;try{const j=await api(`/api/projects/join/${encodeURIComponent(code)}`);state.projectId=j.project_id;state.shareCode=j.share_code;state.geojson=j.geojson;state.summary=j.summary;renderGeneratedMap();renderProjectBox();prepareWorkerUI();startTeamSync();activateTab('field');loadWorker(+(($('fieldWorker').value)||1));status('teamStatus',`${j.area} の共有プロジェクトに参加しました。`,'success')}catch(e){status('teamStatus',e.message,'error')}};

function prepareWorkerUI(){const features=workerFeatures();const options=features.map(f=>`<option value="${f.properties.worker_id}">${escapeHtml(f.properties.name||`担当${f.properties.worker_id}`)}</option>`).join('');$('fieldWorker').innerHTML=options;if(options)state.workerId=+($('fieldWorker').value||1)}
$('fieldWorker').onchange=()=>loadWorker(+$('fieldWorker').value);
function workerFeatures(){return (state.geojson?.features||[]).filter(f=>f.properties?.kind==='worker_route').sort((a,b)=>a.properties.worker_id-b.properties.worker_id)}
function loadWorker(workerId,{preserveProgress=false}={}){state.workerId=workerId;const feat=workerFeatures().find(f=>+f.properties.worker_id===workerId);if(!feat)return;const geom=feat.geometry||{},parts=geom.type==='MultiLineString'?(geom.coordinates||[]):[(geom.coordinates||[])];state.segments=[];state.segmentLengths=[];for(const coords of parts){for(let i=0;i<coords.length-1;i++){state.segments.push([coords[i],coords[i+1]]);state.segmentLengths.push(haversine(coords[i][1],coords[i][0],coords[i+1][1],coords[i+1][0]))}}const key=progressKey();if(!preserveProgress){let saved=[];try{saved=JSON.parse(localStorage.getItem(key)||'[]')}catch{}state.completed=new Set(saved);state.fieldGuideLeg=0}state.lastPosition=null;$('fieldWorkerName').textContent=feat.properties.name||`担当${workerId}`;drawFieldRoute();drawGpsTrack();updateFieldProgress();nextRouteInstruction();pullProgress()}
function progressKey(){return `pn_progress_${state.projectId||'local'}_${state.workerId}`}
function segmentPhysicalKey(s){const q=p=>`${(+p[0]).toFixed(6)},${(+p[1]).toFixed(6)}`;const a=q(s[0]),b=q(s[1]);return a<b?`${a}|${b}`:`${b}|${a}`}
function roadPassRecord(key){return state.roadPasses[key]||(state.roadPasses[key]={dirs:[],evidence:{}})}
function projectRoadPoint(lat,lon,s){const mid=(s[0][1]+s[1][1])/2,scale=Math.cos(mid*Math.PI/180),x=(lon-s[0][0])*111320*scale,y=(lat-s[0][1])*111320,dx=(s[1][0]-s[0][0])*111320*scale,dy=(s[1][1]-s[0][1])*111320,den=dx*dx+dy*dy;if(den<.01)return {t:0,d:Infinity};const t=Math.max(0,Math.min(1,(x*dx+y*dy)/den));return {t,d:Math.hypot(x-t*dx,y-t*dy)}}
// Record only physically observed motion. No inferred completion of earlier route indices.
function observeRoadMotion(prev,now,accuracy){if(!prev||!state.segments.length||accuracy>35)return false;const travel=haversine(prev.lat,prev.lon,now.lat,now.lon);if(travel<2||travel>65)return false;const threshold=Math.min(14,Math.max(7,(+$('gpsThreshold').value||18)*.7));let best=null;for(let i=0;i<state.segments.length;i++){const seg=state.segments[i],length=state.segmentLengths[i];if(length<1)continue;const a=projectRoadPoint(prev.lat,prev.lon,seg),b=projectRoadPoint(now.lat,now.lon,seg);if(a.d>threshold||b.d>threshold)continue;const moved=(b.t-a.t)*length;if(Math.abs(moved)<1.5||Math.abs(moved)<travel*.42)continue;const score=Math.max(a.d,b.d)+Math.abs(Math.abs(moved)-travel)*.15;if(!best||score<best.score)best={i,seg,length,moved,score}}
if(!best)return false;const key=segmentPhysicalKey(best.seg),canonical=key.split('|')[0]===`${(+best.seg[0][0]).toFixed(6)},${(+best.seg[0][1]).toFixed(6)}`,dir=(best.moved>0)==canonical?'+':'-';const rec=roadPassRecord(key);if(rec.dirs.includes(dir))return false;const evidence=rec.evidence[dir]||(rec.evidence[dir]={meters:0,last:0});const nowTime=Date.now();if(nowTime-evidence.last>30000)evidence.meters=0;evidence.meters+=Math.abs(best.moved);evidence.last=nowTime;const needed=Math.max(4,Math.min(best.length*.55,16));if(evidence.meters<needed)return false;rec.dirs.push(dir);for(let i=0;i<state.segments.length;i++){if(segmentPhysicalKey(state.segments[i])!==key)continue;const routeCanonical=key.split('|')[0]===`${(+state.segments[i][0][0]).toFixed(6)},${(+state.segments[i][0][1]).toFixed(6)}`;const routeDir=routeCanonical?'+':'-';if(routeDir===dir)state.completed.add(i)}return true}
function drawFieldRoute(){if(!state.areaGeojson&&state.geojson){const areaOnly={type:'FeatureCollection',features:(state.geojson.features||[]).filter(f=>f.properties?.kind==='area')};if(areaOnly.features.length){state.areaGeojson=areaOnly;renderAreaBoundaries(false)}}if(state.layers.generated){state.layers.generated.remove();state.layers.generated=null}['todo','once','roundtrip','done','directions','sequence'].forEach(k=>{if(state.layers[k]){state.layers[k].remove();state.layers[k]=null}});const todo=[],once=[],roundtrip=[];const legacyCounts=new Map();state.segments.forEach((seg,i)=>{if(state.completed.has(i)){const key=segmentPhysicalKey(seg);legacyCounts.set(key,(legacyCounts.get(key)||0)+1)}});state.segments.forEach((seg,i)=>{const key=segmentPhysicalKey(seg),rec=state.roadPasses[key],count=rec?rec.dirs.length:Math.min(2,legacyCounts.get(key)||0),f={type:'Feature',properties:{segment:i},geometry:{type:'LineString',coordinates:seg}};(count>=2?roundtrip:count===1?once:todo).push(f)});state.layers.todo=L.geoJSON({type:'FeatureCollection',features:todo},{style:{color:'#ef4444',weight:7,opacity:.66}}).addTo(map);state.layers.once=L.geoJSON({type:'FeatureCollection',features:once},{style:{color:'#2563eb',weight:8,opacity:.92}}).addTo(map);state.layers.roundtrip=L.geoJSON({type:'FeatureCollection',features:roundtrip},{style:{color:'#22c55e',weight:9,opacity:.96}}).addTo(map);drawNextWorkerArrow();const both=L.featureGroup([state.layers.todo,state.layers.once,state.layers.roundtrip]);/* Do not refit map on GPS progress updates; preserve the user-selected viewport. */}

function drawNextWorkerArrow(){if(state.layers.directions)state.layers.directions.remove();const g=L.layerGroup();let idx=state.segments.findIndex((_,i)=>!state.completed.has(i));if(idx<0){state.layers.directions=g.addTo(map);return}const s=state.segments[idx],a=s[0],b=s[1],pt=interpolateCoord(a,b,.5),deg=bearingDeg(a,b);L.geoJSON({type:'Feature',properties:{},geometry:{type:'LineString',coordinates:s}},{style:{color:'#2563eb',weight:10,opacity:1}}).addTo(g);L.marker([pt[1],pt[0]],{interactive:false,icon:L.divIcon({className:'route-arrow-wrap',html:`<div class="route-arrow field selected" style="transform:rotate(${deg}deg)">${arrowSvg()}</div>`,iconSize:[32,32],iconAnchor:[16,16]})}).addTo(g);state.layers.directions=g.addTo(map)}
function updateFieldProgress(){const done=[...state.completed].reduce((s,i)=>s+(state.segmentLengths[i]||0),0), total=state.segmentLengths.reduce((a,b)=>a+b,0),pct=total?done/total*100:0;$('fieldPercent').textContent=`${pct.toFixed(1)}%`;$('fieldDistance').textContent=`${(done/1000).toFixed(2)} / ${(total/1000).toFixed(2)} km`;localStorage.setItem(progressKey(),JSON.stringify([...state.completed]));updateHouseholdProgress(pct);if(!state.restoring)saveFieldSessionSoon();return{done,total,pct}}
$('gpsThreshold').oninput=()=>$('gpsThresholdText').textContent=$('gpsThreshold').value;

function startGpsTracking(restored=false){if(window.PostingNative){state.gpsTrackingDesired=true;state.watchId=-1;window.PostingNative.startTracking();$('gpsStart').disabled=true;$('gpsStop').disabled=false;saveFieldSessionSoon();status('gpsStatus','AndroidバックグラウンドGPSを開始します。通知を確認してください。','success');return}if(!navigator.geolocation){status('gpsStatus','この端末ではGPSを利用できません。','error');return}state.gpsTrackingDesired=true;if(state.watchId!==null)return;state.watchId=navigator.geolocation.watchPosition(onPosition,e=>status('gpsStatus',`GPSエラー: ${e.message}`,'error'),{enableHighAccuracy:true,maximumAge:1500,timeout:20000});$('gpsStart').disabled=true;$('gpsStop').disabled=false;saveFieldSessionSoon();status('gpsStatus',restored?'GPS追跡を再開しました。紫線が実歩行軌跡です。':'GPS追跡中。紫線が実歩行軌跡です。','success')}
$('gpsStart').onclick=()=>startGpsTracking(false);
$('gpsStop').onclick=()=>{if(window.PostingNative)window.PostingNative.stopTracking();state.gpsTrackingDesired=false;if(state.watchId!==null&&state.watchId!==-1)navigator.geolocation.clearWatch(state.watchId);state.watchId=null;$('gpsStart').disabled=false;$('gpsStop').disabled=true;saveFieldSessionSoon();status('gpsStatus','GPS追跡を停止しました。')};
function angleDiff(a,b){return Math.abs(((a-b+540)%360)-180)}
function onPosition(p){const lat=p.coords.latitude,lon=p.coords.longitude;ensureGpsRoadCoverage(lat,lon);const heading=Number.isFinite(p.coords.heading)?p.coords.heading:null;state.current={lat,lon,accuracy:p.coords.accuracy,heading};const prev=state.gpsTrack.at(-1);if(!prev||haversine(prev.lat,prev.lon,lat,lon)>=2){state.gpsTrack.push({lat,lon,t:p.timestamp||Date.now(),accuracy:p.coords.accuracy});if(state.gpsTrack.length>12000)state.gpsTrack=state.gpsTrack.slice(-12000);scheduleGpsTrackDraw();saveGpsSoon();saveFieldSessionSoon();}if(state.layers.gps)state.layers.gps.remove();state.layers.gps=L.marker([lat,lon],{icon:L.divIcon({className:'',html:'<div class="gps-marker"></div>',iconSize:[18,18],iconAnchor:[9,9]})}).addTo(map);if($('autoFollow').checked)map.panTo([lat,lon]);const observed=observeRoadMotion(state.lastPosition,{lat,lon},p.coords.accuracy);if(observed){drawFieldRoute();updateFieldProgress();pushProgress()}advanceFieldGuide();nextRouteInstruction();state.lastPosition={lat,lon};status('gpsStatus',`GPS追跡中 / 記録: ${state.gpsTrack.length}点\n精度: ±${Math.round(p.coords.accuracy)}m / 案内: ${Math.min((state.fieldGuideLeg||0)+1,state.navLegs.length)}番`,'success')}
$('resetProgress').onclick=()=>{if(!confirm('この担当の進捗を0%に戻しますか？'))return;state.completed.clear();state.roadPasses={};drawFieldRoute();updateFieldProgress();pushProgress()};

async function pushProgress(){if(!state.projectId)return;const m=updateFieldProgress();try{await api(`/api/projects/${state.projectId}/progress/${state.workerId}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({completed_segments:[...state.completed],completed_distance_m:m.done,lat:state.current?.lat,lon:state.current?.lon})})}catch(e){console.warn(e)}}
async function pullProgress(){if(!state.projectId)return;try{const j=await api(`/api/projects/${state.projectId}/progress`);const me=j.workers.find(w=>+w.worker_id===state.workerId);if(me){const remote=new Set(me.completed_segments||[]);if(remote.size>state.completed.size){state.completed=remote;drawFieldRoute();updateFieldProgress()}}renderTeam(j.workers)}catch(e){status('teamStatus',e.message,'error')}}
function startTeamSync(){if(state.syncTimer)clearInterval(state.syncTimer);pullProgress();state.syncTimer=setInterval(pullProgress,state.config.sync_interval_ms||5000)}
function renderTeam(workers){$('teamProgress').innerHTML=(workers||[]).map(w=>`<div class="worker-card"><div class="worker-head"><b>担当${String(w.worker_id).padStart(2,'0')}</b><span>${w.percent.toFixed(1)}%</span></div><div class="bar"><i style="width:${Math.min(100,w.percent)}%"></i></div><div class="hint">${(w.completed_distance_m/1000).toFixed(2)} / ${(w.total_distance_m/1000).toFixed(2)} km${w.updated_at?' ・ 更新 '+new Date(w.updated_at*1000).toLocaleTimeString():''}</div></div>`).join('')}


async function loadMyProjects(){if(!state.user)return;try{const j=await api('/api/projects');$('myProjectsBox').classList.remove('hidden');$('myProjects').innerHTML=(j.projects||[]).map(p=>`<div class="worker-card"><div class="worker-head"><b>${escapeHtml(p.area)}</b><button class="ghost open-project" data-code="${p.share_code}">開く</button></div><div class="hint">共有コード ${p.share_code} ・ ${p.worker_count}人 ・ ${new Date(p.created_at*1000).toLocaleString()}</div></div>`).join('')||'<div class="hint">保存プロジェクトはまだありません。</div>';document.querySelectorAll('.open-project').forEach(b=>b.onclick=async()=>{$('joinCode').value=b.dataset.code;await $('joinProject').onclick()})}catch(e){console.warn(e)}}

function setupGoogleLogin(){const cid=state.config.google_client_id;if(!cid){$('loginBox').innerHTML='<span class="hint">Googleログイン未設定</span>';return}const s=document.createElement('script');s.src='https://accounts.google.com/gsi/client';s.async=true;s.onload=()=>{google.accounts.id.initialize({client_id:cid,callback:async r=>{try{const j=await api('/api/auth/google',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({credential:r.credential})});state.sessionToken=j.token;state.user=j.user;localStorage.setItem('pn_session',j.token);renderLogin();loadMyProjects()}catch(e){alert(e.message)}}});renderLogin()};document.head.appendChild(s)}
function renderLogin(){if(state.user){$('loginBox').innerHTML=`<button id="logout" class="ghost">${escapeHtml(state.user.name||state.user.email)} ▾</button>`;$('logout').onclick=async()=>{try{await api('/api/auth/logout',{method:'POST'})}catch{}state.sessionToken='';state.user=null;localStorage.removeItem('pn_session');$('myProjectsBox').classList.add('hidden');renderLogin()}}else if(window.google){$('loginBox').innerHTML='<div id="googleBtn"></div>';google.accounts.id.renderButton($('googleBtn'),{theme:'outline',size:'medium',text:'signin_with',shape:'pill'})}}

document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden'){saveFieldSession();gpsTrackPut();}else{drawGpsTrack();if(state.gpsTrackingDesired&&state.watchId===null)startGpsTracking(true)}});window.addEventListener('pagehide',()=>{saveFieldSession();gpsTrackPut()});
let deferredPrompt=null;window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();deferredPrompt=e;$('installBtn').classList.remove('hidden')});$('installBtn').onclick=async()=>{if(!deferredPrompt)return;deferredPrompt.prompt();await deferredPrompt.userChoice;deferredPrompt=null;$('installBtn').classList.add('hidden')};if('serviceWorker'in navigator)window.addEventListener('load',async()=>{try{const r=await navigator.serviceWorker.register('service-worker.js?v=1.9.55',{updateViaCache:'none'});await r.update()}catch(e){console.warn(e)}});

function haversine(lat1,lon1,lat2,lon2){const R=6371000,p=Math.PI/180,dLat=(lat2-lat1)*p,dLon=(lon2-lon1)*p,a=Math.sin(dLat/2)**2+Math.cos(lat1*p)*Math.cos(lat2*p)*Math.sin(dLon/2)**2;return 2*R*Math.atan2(Math.sqrt(a),Math.sqrt(1-a))}
function pointSegmentMeters(lat,lon,lat1,lon1,lat2,lon2){const mLat=(lat+lat1+lat2)/3*Math.PI/180,x=(v)=>v*Math.PI/180*6371000*Math.cos(mLat),y=(v)=>v*Math.PI/180*6371000;const px=x(lon),py=y(lat),ax=x(lon1),ay=y(lat1),bx=x(lon2),by=y(lat2),dx=bx-ax,dy=by-ay;const t=Math.max(0,Math.min(1,((px-ax)*dx+(py-ay)*dy)/(dx*dx+dy*dy||1)));return Math.hypot(px-(ax+t*dx),py-(ay+t*dy))}
$('showArrows')?.addEventListener('change',()=>state.navLegs?.length&&focusGuideLeg(state.activeGuideLeg,false));
$('showNumbers')?.addEventListener('change',()=>state.navLegs?.length&&focusGuideLeg(state.activeGuideLeg,false));

function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]))}

(async()=>{
  // Restore before any GPS or session writes can overwrite saved history.
  await restoreFieldSession();
  const gpsSaved=await gpsTrackGet();
  if(Array.isArray(gpsSaved?.points)&&(gpsSaved.cleared||gpsSaved.points.length>=state.gpsTrack.length)){state.gpsTrack=gpsSaved.points;drawGpsTrack()}
  if(state.gpsTrack.length)await gpsTrackPut(); // migrate tracks saved by earlier versions
  if(window.PostingNative){
    try{
      const points=JSON.parse(window.PostingNative.getTrack());
      if(Array.isArray(points)&&points.length){
        const known=new Set(state.gpsTrack.map(p=>String(p.t)));
        for(const p of points){if(Number.isFinite(p.lat)&&Number.isFinite(p.lon)&&!known.has(String(p.t))){state.gpsTrack.push(p);known.add(String(p.t))}}
        state.gpsTrack.sort((a,b)=>a.t-b.t);
        if(state.gpsTrack.length>12000)state.gpsTrack=state.gpsTrack.slice(-12000);
        drawGpsTrack();await gpsTrackPut();ensureRecordedGpsRoadCoverage();
      }
      if(window.PostingNative.isTracking()){state.gpsTrackingDesired=true;state.watchId=-1;$('gpsStart').disabled=true;$('gpsStop').disabled=false}
    }catch(e){console.warn('Android GPS記録の復元失敗',e)}
  }
  await loadConfig();
})();
window.postingNavigatorNativeSync=async function(){
  if(!window.PostingNative)return;
  try{
    const pts=JSON.parse(window.PostingNative.getTrack());
    const known=new Set(state.gpsTrack.map(p=>String(p.t)));
    let changed=false;
    for(const p of pts){if(Number.isFinite(p.lat)&&Number.isFinite(p.lon)&&!known.has(String(p.t))){state.gpsTrack.push(p);known.add(String(p.t));changed=true}}
    if(changed){state.gpsTrack.sort((a,b)=>a.t-b.t);if(state.gpsTrack.length>12000)state.gpsTrack=state.gpsTrack.slice(-12000);drawGpsTrack();saveGpsSoon();saveFieldSessionSoon();ensureRecordedGpsRoadCoverage()}
    const running=window.PostingNative.isTracking();
    state.gpsTrackingDesired=running;state.watchId=running?-1:null;
    $('gpsStart').disabled=running;$('gpsStop').disabled=!running;
    if(running)status('gpsStatus',`Android GPS記録中 / 記録: ${state.gpsTrack.length}点`,'success');
  }catch(e){console.warn('Android GPS再同期失敗',e)}
};
window.postingNavigatorNativePosition=function(p){
  if(!p||!Number.isFinite(p.lat)||!Number.isFinite(p.lon))return;
  // Do not infer traversed route while history is replayed; only live fixes reach onPosition.
  onPosition({coords:{latitude:p.lat,longitude:p.lon,accuracy:p.accuracy||20,heading:p.heading??null},timestamp:p.t});
};

// Explicit version verification: never auto-reload during active GPS recording.
(function(){
  const button=document.getElementById('checkAppUpdate');
  if(!button)return;
  button.addEventListener('click',async()=>{
    button.disabled=true;
    button.textContent='確認中…';
    try{
      if('serviceWorker' in navigator){
        const registration=await navigator.serviceWorker.getRegistration();
        if(registration)await registration.update();
      }
      const url=new URL('index.html',location.href);
      url.searchParams.set('_version_check',String(Date.now()));
      const response=await fetch(url.toString(),{cache:'no-store',headers:{'Cache-Control':'no-cache'}});
      if(!response.ok)throw new Error('HTTP '+response.status);
      const html=await response.text();
      const match=html.match(/class="badge"[^>]*>v(\d+\.\d+\.\d+)</);
      if(!match)throw new Error('公開中のバージョンを取得できません');
      const published=match[1];
      if(published===(document.querySelector('.badge')?.textContent||'').trim().replace(/^v/,'')){
        button.textContent='v'+published+' 確認済み';
      }else{
        button.textContent='公開中 v'+published;
        alert('GitHub Pagesで公開されているのは v'+published+' です。GitHubのdocs/index.htmlとPagesの公開状況を確認してください。');
      }
    }catch(error){
      button.textContent='確認に失敗';
      alert('バージョン確認に失敗しました: '+error.message);
    }finally{button.disabled=false;}
  });
})();
