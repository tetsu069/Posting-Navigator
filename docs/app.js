const configured=(window.POSTING_NAVIGATOR_API||'').replace(/\/$/,'');
const API=configured || location.origin;
const $=id=>document.getElementById(id);
const state={uploadId:null,jobId:null,geojson:null,areaGeojson:null,summary:null,projectId:null,shareCode:null,workerId:1,watchId:null,current:null,completed:new Set(),segments:[],segmentLengths:[],layers:{},syncTimer:null,config:{gps_threshold_m:18,sync_interval_ms:5000},areaInfo:{},fieldGuideLeg:0,lastPosition:null,sessionToken:localStorage.getItem('pn_session')||'',user:null,navLegs:[],startPickMode:false,activeGuideLeg:0,forceOsmRefresh:false,guidePassed:new Set(),servicePasses:new Map(),kmzBlob:null,gpsTrack:[],gpsTrackingDesired:false,roadPasses:{},restoring:false,kmzUploadGeneration:0};
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
  state.gpsTrack=[];
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
function drawGpsTrack(){if(state.layers.gpsTrack)state.layers.gpsTrack.remove();state.layers.gpsTrack=null;if(state.gpsTrack.length<2)return;state.layers.gpsTrack=L.polyline(state.gpsTrack.map(p=>[p.lat,p.lon]),{color:'#7c3aed',weight:6,opacity:.9,lineCap:'round'}).addTo(map);state.layers.gpsTrack.bringToFront?.()}
async function restoreFieldSession(){const generation=state.kmzUploadGeneration;const x=await sessionGet();if(!x||generation!==state.kmzUploadGeneration)return;state.restoring=true;state.kmzBlob=x.kmzBlob||null;state.geojson=x.geojson||null;state.areaGeojson=x.areaGeojson||null;state.summary=x.summary||null;state.areaInfo=x.areaInfo||{};state.workerId=+x.workerId||1;state.completed=new Set(x.completed||[]);state.fieldGuideLeg=+x.fieldGuideLeg||0;state.activeGuideLeg=+x.activeGuideLeg||0;state.guidePassed=new Set(x.guidePassed||[]);restoreServicePasses(x.servicePasses);state.gpsTrack=Array.isArray(x.gpsTrack)?x.gpsTrack:[];state.roadPasses=x.roadPasses||{};state.gpsTrackingDesired=!!x.gpsTrackingDesired;if(x.startLat&&x.startLon)setStartPoint(+x.startLat,+x.startLon);if(x.areas?.length){$('area').innerHTML=x.areas.map(a=>`<option>${escapeHtml(a)}</option>`).join('');$('area').disabled=false;$('build').disabled=false;$('refreshOsm').disabled=false;if(x.selectedArea)$('area').value=x.selectedArea}if(state.areaGeojson){renderAreaBoundaries(false);renderHouseholdInfo()}if(state.geojson){renderGeneratedMap();renderMetrics();renderDownloads();prepareWorkerUI();loadWorker(state.workerId,{preserveProgress:true});restoreServicePasses(x.servicePasses);state.guidePassed=new Set(x.guidePassed||[]);drawGuideProgress();drawGpsTrack();status('status','前回の作業状態を自動復元しました。','success')}drawGpsTrack();state.restoring=false;/* KMZ auto-upload intentionally disabled: user initiated uploads are authoritative. */if(state.gpsTrackingDesired)setTimeout(()=>startGpsTracking(true),500)}

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
    const saved=await saveServerOsmCacheToBrowser(area);await updateOsmCacheStatus();
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
function loadWorker(workerId,{preserveProgress=false}={}){state.workerId=workerId;const feat=workerFeatures().find(f=>+f.properties.worker_id===workerId);if(!feat)return;const geom=feat.geometry||{},parts=geom.type==='MultiLineString'?(geom.coordinates||[]):[(geom.coordinates||[])];state.segments=[];state.segmentLengths=[];for(const coords of parts){for(let i=0;i<coords.length-1;i++){state.segments.push([coords[i],coords[i+1]]);state.segmentLengths.push(haversine(coords[i][1],coords[i][0],coords[i+1][1],coords[i+1][0]))}}const key=progressKey();if(!preserveProgress){let saved=[];try{saved=JSON.parse(localStorage.getItem(key)||'[]')}catch{}state.completed=new Set(saved);state.fieldGuideLeg=0}state.lastPosition=null;$('fieldWorkerName').textContent=feat.properties.name||`担当${workerId}`;drawFieldRoute();updateFieldProgress();nextRouteInstruction();pullProgress()}
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

function startGpsTracking(restored=false){if(!navigator.geolocation){status('gpsStatus','この端末ではGPSを利用できません。','error');return}state.gpsTrackingDesired=true;if(state.watchId!==null)return;state.watchId=navigator.geolocation.watchPosition(onPosition,e=>status('gpsStatus',`GPSエラー: ${e.message}`,'error'),{enableHighAccuracy:true,maximumAge:1500,timeout:20000});$('gpsStart').disabled=true;$('gpsStop').disabled=false;saveFieldSessionSoon();status('gpsStatus',restored?'GPS追跡を再開しました。紫線が実歩行軌跡です。':'GPS追跡中。紫線が実歩行軌跡です。','success')}
$('gpsStart').onclick=()=>startGpsTracking(false);
$('gpsStop').onclick=()=>{state.gpsTrackingDesired=false;if(state.watchId!==null)navigator.geolocation.clearWatch(state.watchId);state.watchId=null;$('gpsStart').disabled=false;$('gpsStop').disabled=true;saveFieldSessionSoon();status('gpsStatus','GPS追跡を停止しました。')};
function angleDiff(a,b){return Math.abs(((a-b+540)%360)-180)}
function onPosition(p){const lat=p.coords.latitude,lon=p.coords.longitude,heading=Number.isFinite(p.coords.heading)?p.coords.heading:null;state.current={lat,lon,accuracy:p.coords.accuracy,heading};const prev=state.gpsTrack.at(-1);if(!prev||haversine(prev.lat,prev.lon,lat,lon)>=2){state.gpsTrack.push({lat,lon,t:Date.now(),accuracy:p.coords.accuracy});if(state.gpsTrack.length>12000)state.gpsTrack=state.gpsTrack.slice(-12000);drawGpsTrack();saveGpsSoon();saveFieldSessionSoon();}if(state.layers.gps)state.layers.gps.remove();state.layers.gps=L.marker([lat,lon],{icon:L.divIcon({className:'',html:'<div class="gps-marker"></div>',iconSize:[18,18],iconAnchor:[9,9]})}).addTo(map);if($('autoFollow').checked)map.panTo([lat,lon]);const observed=observeRoadMotion(state.lastPosition,{lat,lon},p.coords.accuracy);if(observed){drawFieldRoute();updateFieldProgress();pushProgress()}advanceFieldGuide();nextRouteInstruction();state.lastPosition={lat,lon};status('gpsStatus',`GPS追跡中\n精度: ±${Math.round(p.coords.accuracy)}m / 案内: ${Math.min((state.fieldGuideLeg||0)+1,state.navLegs.length)}番`,'success')}
$('resetProgress').onclick=()=>{if(!confirm('この担当の進捗を0%に戻しますか？'))return;state.completed.clear();state.roadPasses={};drawFieldRoute();updateFieldProgress();pushProgress()};

async function pushProgress(){if(!state.projectId)return;const m=updateFieldProgress();try{await api(`/api/projects/${state.projectId}/progress/${state.workerId}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({completed_segments:[...state.completed],completed_distance_m:m.done,lat:state.current?.lat,lon:state.current?.lon})})}catch(e){console.warn(e)}}
async function pullProgress(){if(!state.projectId)return;try{const j=await api(`/api/projects/${state.projectId}/progress`);const me=j.workers.find(w=>+w.worker_id===state.workerId);if(me){const remote=new Set(me.completed_segments||[]);if(remote.size>state.completed.size){state.completed=remote;drawFieldRoute();updateFieldProgress()}}renderTeam(j.workers)}catch(e){status('teamStatus',e.message,'error')}}
function startTeamSync(){if(state.syncTimer)clearInterval(state.syncTimer);pullProgress();state.syncTimer=setInterval(pullProgress,state.config.sync_interval_ms||5000)}
function renderTeam(workers){$('teamProgress').innerHTML=(workers||[]).map(w=>`<div class="worker-card"><div class="worker-head"><b>担当${String(w.worker_id).padStart(2,'0')}</b><span>${w.percent.toFixed(1)}%</span></div><div class="bar"><i style="width:${Math.min(100,w.percent)}%"></i></div><div class="hint">${(w.completed_distance_m/1000).toFixed(2)} / ${(w.total_distance_m/1000).toFixed(2)} km${w.updated_at?' ・ 更新 '+new Date(w.updated_at*1000).toLocaleTimeString():''}</div></div>`).join('')}


async function loadMyProjects(){if(!state.user)return;try{const j=await api('/api/projects');$('myProjectsBox').classList.remove('hidden');$('myProjects').innerHTML=(j.projects||[]).map(p=>`<div class="worker-card"><div class="worker-head"><b>${escapeHtml(p.area)}</b><button class="ghost open-project" data-code="${p.share_code}">開く</button></div><div class="hint">共有コード ${p.share_code} ・ ${p.worker_count}人 ・ ${new Date(p.created_at*1000).toLocaleString()}</div></div>`).join('')||'<div class="hint">保存プロジェクトはまだありません。</div>';document.querySelectorAll('.open-project').forEach(b=>b.onclick=async()=>{$('joinCode').value=b.dataset.code;await $('joinProject').onclick()})}catch(e){console.warn(e)}}

function setupGoogleLogin(){const cid=state.config.google_client_id;if(!cid){$('loginBox').innerHTML='<span class="hint">Googleログイン未設定</span>';return}const s=document.createElement('script');s.src='https://accounts.google.com/gsi/client';s.async=true;s.onload=()=>{google.accounts.id.initialize({client_id:cid,callback:async r=>{try{const j=await api('/api/auth/google',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({credential:r.credential})});state.sessionToken=j.token;state.user=j.user;localStorage.setItem('pn_session',j.token);renderLogin();loadMyProjects()}catch(e){alert(e.message)}}});renderLogin()};document.head.appendChild(s)}
function renderLogin(){if(state.user){$('loginBox').innerHTML=`<button id="logout" class="ghost">${escapeHtml(state.user.name||state.user.email)} ▾</button>`;$('logout').onclick=async()=>{try{await api('/api/auth/logout',{method:'POST'})}catch{}state.sessionToken='';state.user=null;localStorage.removeItem('pn_session');$('myProjectsBox').classList.add('hidden');renderLogin()}}else if(window.google){$('loginBox').innerHTML='<div id="googleBtn"></div>';google.accounts.id.renderButton($('googleBtn'),{theme:'outline',size:'medium',text:'signin_with',shape:'pill'})}}

document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden'){saveFieldSession();gpsTrackPut();}else{drawGpsTrack();if(state.gpsTrackingDesired&&state.watchId===null)startGpsTracking(true)}});window.addEventListener('pagehide',()=>{saveFieldSession();gpsTrackPut()});
let deferredPrompt=null;window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();deferredPrompt=e;$('installBtn').classList.remove('hidden')});$('installBtn').onclick=async()=>{if(!deferredPrompt)return;deferredPrompt.prompt();await deferredPrompt.userChoice;deferredPrompt=null;$('installBtn').classList.add('hidden')};if('serviceWorker'in navigator)window.addEventListener('load',async()=>{try{const r=await navigator.serviceWorker.register('service-worker.js?v=1.9.20',{updateViaCache:'none'});await r.update()}catch(e){console.warn(e)}});

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
  await loadConfig();
})();
