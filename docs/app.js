const configured=(window.POSTING_NAVIGATOR_API||'').replace(/\/$/,'');
const API=configured || location.origin;
const $=id=>document.getElementById(id);
const state={uploadId:null,jobId:null,geojson:null,areaGeojson:null,summary:null,projectId:null,shareCode:null,workerId:1,watchId:null,current:null,completed:new Set(),segments:[],segmentLengths:[],layers:{},syncTimer:null,config:{gps_threshold_m:18,sync_interval_ms:5000},sessionToken:localStorage.getItem('pn_session')||'',user:null,navLegs:[]};
const map=L.map('map').setView([35.7005,139.6925],16);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:20,attribution:'© OpenStreetMap contributors'}).addTo(map);

function status(id,text,type=''){const el=$(id);el.textContent=text;el.className='status'+(type?' '+type:'')}
function authHeaders(extra={}){return state.sessionToken?{...extra,Authorization:`Bearer ${state.sessionToken}`} : extra}
async function api(path,opts={}){const r=await fetch(`${API}${path}`,{...opts,headers:authHeaders(opts.headers||{})});let j={};try{j=await r.json()}catch{}if(!r.ok)throw Error(j.error||`${r.status} ${r.statusText}`);return j}
function activateTab(name){document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x.dataset.tab===name));document.querySelectorAll('.tabpane').forEach(x=>x.classList.toggle('active',x.id===`tab-${name}`));setTimeout(()=>map.invalidateSize(),50)}
document.querySelectorAll('.tab').forEach(x=>x.onclick=()=>activateTab(x.dataset.tab));

async function loadConfig(){try{state.config=await api('/api/config');$('gpsThreshold').value=state.config.gps_threshold_m||18;$('gpsThresholdText').textContent=$('gpsThreshold').value;if(state.sessionToken){try{const me=await api('/api/auth/me');state.user=me.user}catch{state.sessionToken='';localStorage.removeItem('pn_session')}}setupGoogleLogin();if(state.user)loadMyProjects()}catch(e){status('status',`APIに接続できません。docs/config.js のRender URLを確認してください。\n${e.message}`,'error')}}

map.on('click',e=>{if(state.layers.start)state.layers.start.remove();$('lat').value=e.latlng.lat.toFixed(7);$('lon').value=e.latlng.lng.toFixed(7);state.layers.start=L.marker(e.latlng).addTo(map).bindPopup('開始地点').openPopup()});

$('useCurrent').onclick=()=>navigator.geolocation?.getCurrentPosition(p=>{const {latitude,longitude}=p.coords;$('lat').value=latitude.toFixed(7);$('lon').value=longitude.toFixed(7);map.setView([latitude,longitude],18)},e=>status('status',`現在地を取得できません: ${e.message}`,'error'),{enableHighAccuracy:true});

$('kmz').onchange=async()=>{const f=$('kmz').files[0];if(!f)return;status('status','KMZを解析中…');$('build').disabled=true;const fd=new FormData();fd.append('kmz',f);try{const r=await fetch(`${API}/api/areas`,{method:'POST',body:fd,headers:authHeaders()});const j=await r.json();if(!r.ok)throw Error(j.error);state.uploadId=j.upload_id;state.areaGeojson=j.area_geojson||null;$('area').innerHTML=j.areas.map(x=>`<option>${escapeHtml(x)}</option>`).join('');$('area').disabled=false;$('build').disabled=false;renderAreaBoundaries(true);status('status',`${j.areas.length}件の区画を読み込みました。地図に町丁目境界を表示しています。`,'success')}catch(e){status('status',e.message,'error')}};


$('area').onchange=()=>renderAreaBoundaries(true);

function renderAreaBoundaries(fitSelected=false){
  if(state.layers.areas){state.layers.areas.remove();state.layers.areas=null}
  if(!state.areaGeojson)return;
  const selected=$('area').value;
  let selectedLayer=null;
  state.layers.areas=L.geoJSON(state.areaGeojson,{
    style:f=>{
      const active=f.properties?.name===selected;
      return active
        ? {color:'#2563eb',weight:4,opacity:.95,fillColor:'#3b82f6',fillOpacity:.14}
        : {color:'#64748b',weight:1.5,opacity:.65,fillColor:'#94a3b8',fillOpacity:.035};
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

$('build').onclick=async()=>{status('status','道路取得・巡回計算・KML生成を実行中…');$('build').disabled=true;$('downloads').classList.add('hidden');try{const j=await api('/api/build',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({upload_id:state.uploadId,area:$('area').value,workers:+$('workers').value,start_lat:$('lat').value,start_lon:$('lon').value,offline_fallback:$('fallback').checked})});state.jobId=j.job_id;state.geojson=j.geojson;state.summary=j.summary;renderGeneratedMap();renderMetrics();renderDownloads();status('status',`生成完了（道路データ: ${j.summary.data_mode}）`,'success');await createSharedProject();prepareWorkerUI();activateTab('field')}catch(e){status('status',e.message,'error')}finally{$('build').disabled=false}};

function routeStepFeatures(){return (state.geojson?.features||[]).filter(f=>f.properties?.kind==='route_step').sort((a,b)=>(a.properties.seq||0)-(b.properties.seq||0))}
function navigationLegFeatures(){return (state.geojson?.features||[]).filter(f=>f.properties?.kind==='navigation_leg').sort((a,b)=>(a.properties.leg||0)-(b.properties.leg||0))}
function renderGeneratedMap(){
  clearRouteLayers();
  const steps=routeStepFeatures(), legs=navigationLegFeatures();
  state.navLegs=legs;
  const hasSteps=steps.length>0;
  state.layers.generated=L.geoJSON(state.geojson,{
    filter:f=>{const k=f.properties?.kind;return k!=='navigation_leg' && !(hasSteps&&(k==='route'||k==='worker_route'))},
    style:f=>{
      const k=f.properties?.kind;
      if(k==='area')return state.areaGeojson?{weight:0,opacity:0,fillOpacity:0}:{weight:4,color:'#2563eb',opacity:.95,fillColor:'#3b82f6',fillOpacity:.14};
      if(k==='road')return{weight:1.1,color:'#94a3b8',opacity:.28};
      if(k==='route_step'){if(f.properties.transfer)return{weight:3,color:'#64748b',opacity:.7,dashArray:'8 8'};if(f.properties.duplicated)return{weight:5,color:'#f59e0b',opacity:.82};return{weight:5,color:'#ef4444',opacity:.82}}
      return{weight:2,color:'#64748b'}
    }
  }).addTo(map);
  drawRouteDirections(legs.length?legs:steps);
  renderRouteGuide(legs);
  const b=state.layers.generated.getBounds();if(b.isValid())map.fitBounds(b,{padding:[15,15]})
}
function clearRouteLayers(){['generated','todo','done','gps','directions','sequence'].forEach(k=>{if(state.layers[k]){state.layers[k].remove();state.layers[k]=null}})}
function bearingDeg(a,b){const p=Math.PI/180,y=Math.sin((b[0]-a[0])*p)*Math.cos(b[1]*p),x=Math.cos(a[1]*p)*Math.sin(b[1]*p)-Math.sin(a[1]*p)*Math.cos(b[1]*p)*Math.cos((b[0]-a[0])*p);return(Math.atan2(y,x)/p+360)%360}
function interpolateCoord(a,b,t){return[a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t]}
function arrowSvg(){return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 1.5 4.5 10H9v12h6V10h4.5z"/></svg>'}
function lineMidpoint(coords){let lens=[],total=0;for(let i=0;i<coords.length-1;i++){const d=haversine(coords[i][1],coords[i][0],coords[i+1][1],coords[i+1][0]);lens.push(d);total+=d}let target=total/2,acc=0;for(let i=0;i<lens.length;i++){if(acc+lens[i]>=target){const t=lens[i]?((target-acc)/lens[i]):.5;return{pt:interpolateCoord(coords[i],coords[i+1],t),bearing:bearingDeg(coords[i],coords[i+1])}}acc+=lens[i]}return{pt:coords[0],bearing:bearingDeg(coords[0],coords[1])}}
function drawRouteDirections(features){
  if(state.layers.directions)state.layers.directions.remove();if(state.layers.sequence)state.layers.sequence.remove();
  const arrows=L.layerGroup(), numbers=L.layerGroup();
  const showArrows=$('showArrows')?.checked!==false, showNumbers=$('showNumbers')?.checked!==false;
  const isLeg=features.some(f=>f.properties?.kind==='navigation_leg');
  const numberEvery=features.length>70?5:features.length>40?3:features.length>24?2:1;
  features.forEach((f,idx)=>{const c=f.geometry?.coordinates;if(!c||c.length<2)return;const transfer=!!f.properties.transfer;
    if(showArrows){const m=lineMidpoint(c);L.marker([m.pt[1],m.pt[0]],{interactive:false,icon:L.divIcon({className:'route-arrow-wrap',html:`<div class="route-arrow ${transfer?'transfer':''}" style="transform:rotate(${m.bearing}deg)">${arrowSvg()}</div>`,iconSize:[28,28],iconAnchor:[14,14]})}).addTo(arrows)}
    const n=isLeg?(f.properties.leg||idx+1):(f.properties.seq||idx+1);
    if(showNumbers&&!transfer&&(n===1||n%numberEvery===0)){const p=c[0];L.marker([p[1],p[0]],{interactive:false,icon:L.divIcon({className:'route-seq-wrap',html:`<div class="route-seq">${n}</div>`,iconSize:[28,28],iconAnchor:[14,14]})}).addTo(numbers)}
  });
  if(features.length){const first=features[0].geometry.coordinates[0],lastF=features[features.length-1],last=lastF.geometry.coordinates.at(-1);L.marker([first[1],first[0]],{icon:L.divIcon({className:'route-end-wrap',html:'<div class="route-end start">START</div>',iconSize:[52,24],iconAnchor:[26,12]})}).addTo(numbers);L.marker([last[1],last[0]],{icon:L.divIcon({className:'route-end-wrap',html:'<div class="route-end goal">GOAL</div>',iconSize:[48,24],iconAnchor:[24,12]})}).addTo(numbers)}
  state.layers.directions=arrows.addTo(map);state.layers.sequence=numbers.addTo(map);
}
function renderRouteGuide(legs){
  const box=$('routeGuide');if(!box)return;
  if(!legs?.length){box.classList.add('hidden');return}
  const shown=legs.slice(0,30);
  box.innerHTML=`<h3>開始地点からの回り方</h3><div class="hint">地図の番号と同じ順番です。道路上の青い矢印の向きに進みます。</div><div class="guide-list">${shown.map(f=>`<div class="guide-item"><div class="guide-num">${f.properties.leg}</div><div><b>${escapeHtml(f.properties.turn||'進む')} ・ ${Math.round(f.properties.length_m||0)}m</b><span>${escapeHtml(f.properties.name||f.properties.instruction||'道路に沿って進む')}</span></div></div>`).join('')}</div>${legs.length>30?`<div class="hint">続き ${legs.length-30} 区間（地図上の番号に続きます）</div>`:''}`;
  box.classList.remove('hidden');
}
function nextRouteInstruction(){
  const box=$('nextGuide');if(!box||!state.navLegs?.length)return;
  let idx=0;
  if(state.current){let best=Infinity;state.navLegs.forEach((f,i)=>{const c=f.geometry.coordinates;for(let j=0;j<c.length-1;j++){const d=pointSegmentMeters(state.current.lat,state.current.lon,c[j][1],c[j][0],c[j+1][1],c[j+1][0]);if(d<best){best=d;idx=i}}})}
  const f=state.navLegs[idx],p=f.properties;
  box.innerHTML=`<b>次に進む方向</b><div class="turn">${p.leg}. ${escapeHtml(p.turn||'進む')}</div><div>${escapeHtml(p.name||'道路に沿って進む')} ・ 約${Math.round(p.length_m||0)}m</div>`;
  box.classList.remove('hidden');
}
function renderMetrics(){const s=state.summary;$('metrics').innerHTML=`<div class="metric">全体距離<b>${(s.route_length_m/1000).toFixed(2)} km</b></div><div class="metric">対象道路<b>${s.source_edges||0}本</b></div><div class="metric">非連結成分<b>${s.component_count||1}</b></div><div class="metric">重複倍率<b>${(s.route_ratio||s.duplication_ratio||0).toFixed(2)}</b></div><div class="metric">移動区間<b>${((s.transfer_length_m||0)/1000).toFixed(2)} km</b></div><div class="metric">担当人数<b>${s.worker_count}人</b></div>`;$('metrics').classList.remove('hidden')}
function renderDownloads(){const base=`${API}/download/${state.jobId}`;$('downloads').innerHTML=`<label>成果物</label><a href="${base}/posting_navigator_results.zip">一式ZIP</a><a href="${base}/posting_navigator.kmz">統合KMZ</a><a href="${base}/posting_navigator.kml">統合KML</a><a href="${base}/assignments.csv">担当CSV</a><a href="${base}/summary.json">集計JSON</a>`;$('downloads').classList.remove('hidden')}

async function createSharedProject(){try{const j=await api('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:state.jobId})});state.projectId=j.project_id;state.shareCode=j.share_code;localStorage.setItem('pn_last_project',JSON.stringify({projectId:state.projectId,shareCode:state.shareCode}));renderProjectBox();startTeamSync()}catch(e){status('teamStatus',`共有プロジェクトを作成できません: ${e.message}`,'error')}}
function renderProjectBox(){if(!state.projectId)return;$('projectBox').classList.remove('hidden');$('shareCode').textContent=state.shareCode;status('teamStatus','共有中。各担当の進捗は約5秒ごとに同期されます。','success')}
$('copyCode').onclick=async()=>{await navigator.clipboard?.writeText(state.shareCode||'');$('copyCode').textContent='コピー済み';setTimeout(()=>$('copyCode').textContent='コピー',1200)};
$('joinProject').onclick=async()=>{const code=$('joinCode').value.trim().toUpperCase();if(!code)return;try{const j=await api(`/api/projects/join/${encodeURIComponent(code)}`);state.projectId=j.project_id;state.shareCode=j.share_code;state.geojson=j.geojson;state.summary=j.summary;renderGeneratedMap();renderProjectBox();prepareWorkerUI();startTeamSync();activateTab('field');status('teamStatus',`${j.area} の共有プロジェクトに参加しました。`,'success')}catch(e){status('teamStatus',e.message,'error')}};

function prepareWorkerUI(){const features=workerFeatures();const options=features.map(f=>`<option value="${f.properties.worker_id}">${escapeHtml(f.properties.name||`担当${f.properties.worker_id}`)}</option>`).join('');$('fieldWorker').innerHTML=options;if(options){state.workerId=+($('fieldWorker').value||1);loadWorker(state.workerId)}}
$('fieldWorker').onchange=()=>loadWorker(+$('fieldWorker').value);
function workerFeatures(){return (state.geojson?.features||[]).filter(f=>f.properties?.kind==='worker_route').sort((a,b)=>a.properties.worker_id-b.properties.worker_id)}
function loadWorker(workerId){state.workerId=workerId;const feat=workerFeatures().find(f=>+f.properties.worker_id===workerId);if(!feat)return;const coords=feat.geometry.coordinates;state.segments=[];state.segmentLengths=[];for(let i=0;i<coords.length-1;i++){state.segments.push([coords[i],coords[i+1]]);state.segmentLengths.push(haversine(coords[i][1],coords[i][0],coords[i+1][1],coords[i+1][0]))}const key=progressKey();let saved=[];try{saved=JSON.parse(localStorage.getItem(key)||'[]')}catch{}state.completed=new Set(saved);$('fieldWorkerName').textContent=feat.properties.name||`担当${workerId}`;drawFieldRoute();updateFieldProgress();nextRouteInstruction();pullProgress()}
function progressKey(){return `pn_progress_${state.projectId||'local'}_${state.workerId}`}
function drawFieldRoute(){if(!state.areaGeojson&&state.geojson){const areaOnly={type:'FeatureCollection',features:(state.geojson.features||[]).filter(f=>f.properties?.kind==='area')};if(areaOnly.features.length){state.areaGeojson=areaOnly;renderAreaBoundaries(false)}}if(state.layers.generated){state.layers.generated.remove();state.layers.generated=null}if(state.layers.todo)state.layers.todo.remove();if(state.layers.done)state.layers.done.remove();if(state.layers.directions)state.layers.directions.remove();if(state.layers.sequence)state.layers.sequence.remove();const todo=[],done=[],arrows=L.layerGroup();state.segments.forEach((s,i)=>{(state.completed.has(i)?done:todo).push({type:'Feature',properties:{segment:i},geometry:{type:'LineString',coordinates:s}});if($('showArrows')?.checked!==false&&i%6===0){const a=s[0],b=s[1],pt=interpolateCoord(a,b,.5),deg=bearingDeg(a,b);L.marker([pt[1],pt[0]],{interactive:false,icon:L.divIcon({className:'route-arrow-wrap',html:`<div class="route-arrow field" style="transform:rotate(${deg}deg)">${arrowSvg()}</div>`,iconSize:[28,28],iconAnchor:[14,14]})}).addTo(arrows)}});state.layers.todo=L.geoJSON({type:'FeatureCollection',features:todo},{style:{color:'#ef4444',weight:7,opacity:.82}}).addTo(map);state.layers.done=L.geoJSON({type:'FeatureCollection',features:done},{style:{color:'#22c55e',weight:8,opacity:.95}}).addTo(map);state.layers.directions=arrows.addTo(map);const both=L.featureGroup([state.layers.todo,state.layers.done]);const b=both.getBounds();if(b.isValid())map.fitBounds(b,{padding:[20,20]})}
function updateFieldProgress(){const done=[...state.completed].reduce((s,i)=>s+(state.segmentLengths[i]||0),0), total=state.segmentLengths.reduce((a,b)=>a+b,0),pct=total?done/total*100:0;$('fieldPercent').textContent=`${pct.toFixed(1)}%`;$('fieldDistance').textContent=`${(done/1000).toFixed(2)} / ${(total/1000).toFixed(2)} km`;localStorage.setItem(progressKey(),JSON.stringify([...state.completed]));return{done,total,pct}}
$('gpsThreshold').oninput=()=>$('gpsThresholdText').textContent=$('gpsThreshold').value;

$('gpsStart').onclick=()=>{if(!navigator.geolocation){status('gpsStatus','この端末ではGPSを利用できません。','error');return}if(state.watchId!==null)return;state.watchId=navigator.geolocation.watchPosition(onPosition,e=>status('gpsStatus',`GPSエラー: ${e.message}`,'error'),{enableHighAccuracy:true,maximumAge:1500,timeout:15000});$('gpsStart').disabled=true;$('gpsStop').disabled=false;status('gpsStatus','GPS追跡中。ルート上を歩くと近い区間が自動的に緑になります。','success')};
$('gpsStop').onclick=()=>{if(state.watchId!==null)navigator.geolocation.clearWatch(state.watchId);state.watchId=null;$('gpsStart').disabled=false;$('gpsStop').disabled=true;status('gpsStatus','GPS追跡を停止しました。')};
function onPosition(p){const lat=p.coords.latitude,lon=p.coords.longitude;state.current={lat,lon,accuracy:p.coords.accuracy};if(state.layers.gps)state.layers.gps.remove();state.layers.gps=L.marker([lat,lon],{icon:L.divIcon({className:'',html:'<div class="gps-marker"></div>',iconSize:[18,18],iconAnchor:[9,9]})}).addTo(map);if($('autoFollow').checked)map.panTo([lat,lon]);const threshold=+$('gpsThreshold').value;let best=-1,bestD=Infinity;state.segments.forEach((seg,i)=>{if(state.completed.has(i))return;const d=pointSegmentMeters(lat,lon,seg[0][1],seg[0][0],seg[1][1],seg[1][0]);if(d<bestD){bestD=d;best=i}});if(best>=0&&bestD<=threshold){state.completed.add(best);for(const n of [best-1,best+1])if(n>=0&&n<state.segments.length&&pointSegmentMeters(lat,lon,state.segments[n][0][1],state.segments[n][0][0],state.segments[n][1][1],state.segments[n][1][0])<=threshold)state.completed.add(n);drawFieldRoute();updateFieldProgress();pushProgress()}nextRouteInstruction();status('gpsStatus',`GPS追跡中\n精度: ±${Math.round(p.coords.accuracy)}m / 最寄り未配布区間: ${isFinite(bestD)?bestD.toFixed(1):'-'}m`,'success')}
$('resetProgress').onclick=()=>{if(!confirm('この担当の進捗を0%に戻しますか？'))return;state.completed.clear();drawFieldRoute();updateFieldProgress();pushProgress()};

async function pushProgress(){if(!state.projectId)return;const m=updateFieldProgress();try{await api(`/api/projects/${state.projectId}/progress/${state.workerId}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({completed_segments:[...state.completed],completed_distance_m:m.done,lat:state.current?.lat,lon:state.current?.lon})})}catch(e){console.warn(e)}}
async function pullProgress(){if(!state.projectId)return;try{const j=await api(`/api/projects/${state.projectId}/progress`);const me=j.workers.find(w=>+w.worker_id===state.workerId);if(me){const remote=new Set(me.completed_segments||[]);if(remote.size>state.completed.size){state.completed=remote;drawFieldRoute();updateFieldProgress()}}renderTeam(j.workers)}catch(e){status('teamStatus',e.message,'error')}}
function startTeamSync(){if(state.syncTimer)clearInterval(state.syncTimer);pullProgress();state.syncTimer=setInterval(pullProgress,state.config.sync_interval_ms||5000)}
function renderTeam(workers){$('teamProgress').innerHTML=(workers||[]).map(w=>`<div class="worker-card"><div class="worker-head"><b>担当${String(w.worker_id).padStart(2,'0')}</b><span>${w.percent.toFixed(1)}%</span></div><div class="bar"><i style="width:${Math.min(100,w.percent)}%"></i></div><div class="hint">${(w.completed_distance_m/1000).toFixed(2)} / ${(w.total_distance_m/1000).toFixed(2)} km${w.updated_at?' ・ 更新 '+new Date(w.updated_at*1000).toLocaleTimeString():''}</div></div>`).join('')}


async function loadMyProjects(){if(!state.user)return;try{const j=await api('/api/projects');$('myProjectsBox').classList.remove('hidden');$('myProjects').innerHTML=(j.projects||[]).map(p=>`<div class="worker-card"><div class="worker-head"><b>${escapeHtml(p.area)}</b><button class="ghost open-project" data-code="${p.share_code}">開く</button></div><div class="hint">共有コード ${p.share_code} ・ ${p.worker_count}人 ・ ${new Date(p.created_at*1000).toLocaleString()}</div></div>`).join('')||'<div class="hint">保存プロジェクトはまだありません。</div>';document.querySelectorAll('.open-project').forEach(b=>b.onclick=async()=>{$('joinCode').value=b.dataset.code;await $('joinProject').onclick()})}catch(e){console.warn(e)}}

function setupGoogleLogin(){const cid=state.config.google_client_id;if(!cid){$('loginBox').innerHTML='<span class="hint">Googleログイン未設定</span>';return}const s=document.createElement('script');s.src='https://accounts.google.com/gsi/client';s.async=true;s.onload=()=>{google.accounts.id.initialize({client_id:cid,callback:async r=>{try{const j=await api('/api/auth/google',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({credential:r.credential})});state.sessionToken=j.token;state.user=j.user;localStorage.setItem('pn_session',j.token);renderLogin();loadMyProjects()}catch(e){alert(e.message)}}});renderLogin()};document.head.appendChild(s)}
function renderLogin(){if(state.user){$('loginBox').innerHTML=`<button id="logout" class="ghost">${escapeHtml(state.user.name||state.user.email)} ▾</button>`;$('logout').onclick=async()=>{try{await api('/api/auth/logout',{method:'POST'})}catch{}state.sessionToken='';state.user=null;localStorage.removeItem('pn_session');$('myProjectsBox').classList.add('hidden');renderLogin()}}else if(window.google){$('loginBox').innerHTML='<div id="googleBtn"></div>';google.accounts.id.renderButton($('googleBtn'),{theme:'outline',size:'medium',text:'signin_with',shape:'pill'})}}

let deferredPrompt=null;window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();deferredPrompt=e;$('installBtn').classList.remove('hidden')});$('installBtn').onclick=async()=>{if(!deferredPrompt)return;deferredPrompt.prompt();await deferredPrompt.userChoice;deferredPrompt=null;$('installBtn').classList.add('hidden')};if('serviceWorker'in navigator)window.addEventListener('load',()=>navigator.serviceWorker.register('service-worker.js').catch(console.warn));

function haversine(lat1,lon1,lat2,lon2){const R=6371000,p=Math.PI/180,dLat=(lat2-lat1)*p,dLon=(lon2-lon1)*p,a=Math.sin(dLat/2)**2+Math.cos(lat1*p)*Math.cos(lat2*p)*Math.sin(dLon/2)**2;return 2*R*Math.atan2(Math.sqrt(a),Math.sqrt(1-a))}
function pointSegmentMeters(lat,lon,lat1,lon1,lat2,lon2){const mLat=(lat+lat1+lat2)/3*Math.PI/180,x=(v)=>v*Math.PI/180*6371000*Math.cos(mLat),y=(v)=>v*Math.PI/180*6371000;const px=x(lon),py=y(lat),ax=x(lon1),ay=y(lat1),bx=x(lon2),by=y(lat2),dx=bx-ax,dy=by-ay;const t=Math.max(0,Math.min(1,((px-ax)*dx+(py-ay)*dy)/(dx*dx+dy*dy||1)));return Math.hypot(px-(ax+t*dx),py-(ay+t*dy))}
$('showArrows')?.addEventListener('change',()=>state.geojson&&renderGeneratedMap());
$('showNumbers')?.addEventListener('change',()=>state.geojson&&renderGeneratedMap());

function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]))}

loadConfig();
