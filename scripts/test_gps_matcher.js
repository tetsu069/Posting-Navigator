const fs=require('fs'),vm=require('vm');const s=fs.readFileSync(require('path').join(__dirname,'../docs/app.js'),'utf8');
const code=s.slice(s.indexOf('function gpsGraph(){'),s.indexOf('// Show ONLY graph-connected'));
const context={state:{osmRoadSegments:[],gpsRoadGraph:null,gpsTrack:[]},Math,Map,Set,Number,console,
haversine:(a,b,c,d)=>Math.hypot((a-c)*111000,(b-d)*111000),
projectRoadPoint:(lat,lon,pts)=>{const [a,b]=pts;let dx=b[0]-a[0],dy=b[1]-a[1],t=Math.max(0,Math.min(1,((lon-a[0])*dx+(lat-a[1])*dy)/(dx*dx+dy*dy)));return {t,d:Math.hypot((lat-a[1]-dy*t)*111000,(lon-a[0]-dx*t)*111000)}}};
vm.createContext(context);vm.runInContext(code,context);
const nodes=[[35,139],[35,139.001],[35.001,139.001]];
for(let i=1;i<nodes.length;i++){const [lat,lon]=nodes[i-1],[lat2,lon2]=nodes[i];context.state.osmRoadSegments.push({a:{lat,lon},b:{lat:lat2,lon:lon2},len:context.haversine(lat,lon,lat2,lon2),way:1,index:i})}
context.state.gpsTrack=[{lat:35,lon:139.0001,t:0,accuracy:10},{lat:35.00001,lon:139.0005,t:30000,accuracy:10},{lat:35.00002,lon:139.00095,t:60000,accuracy:10},{lat:35.0005,lon:139.00102,t:90000,accuracy:10}];
const r=vm.runInContext('gpsDisplayTrack()',context);console.log(JSON.stringify(r));if(!r.matched.length||r.matched[0].length<3)process.exit(1)
