const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const core = require('../../extensions/shared/core.js');
const frame = {frameId:2,frameURL:'https://player.example/embed/episode',title:'Player',resources:[],videos:[{
  index:0,src:'https://media.example/episode.mp4',sources:[],currentTime:23,duration:300,width:1280,height:720,paused:false,
  tracks:[{url:'https://media.example/en.vtt',label:'English',language:'en'}],textTracks:[{label:'English',language:'en',mode:'showing'}]}]};
const uuid='c1a7cb09-1c47-4126-b43d-b10b5107332b';
test('direct embedded video retains exact source, frame referer, position and soft track',()=>{
  const [c]=core.candidatesFromFrames([frame],'Episode title');
  const m=core.offer(c,c.subtitles,uuid);
  assert.equal(m.payload.referer,frame.frameURL);assert.equal(m.payload.currentTime,23);
  assert.equal(m.payload.subtitles[0].language,'en');assert.equal(m.payload.title,'Episode title');
});
test('blob player offers all same-frame HTTP media candidates, never silently last ad',()=>{
  const f=structuredClone(frame);f.videos[0].src='blob:https://player.example/123';
  f.resources=['https://media.example/episode.m3u8?token=test','https://ads.example/ad.mp4','https://other.example/image.png'];
  const c=core.candidatesFromFrames([f]);assert.equal(c.length,2);assert.equal(core.chooseInitial(c),'');
  assert.equal(c[0].inferred,true);assert.equal(c[0].url,f.resources[0]);
});
test('one candidate auto-selects; multiple actual players require explicit selection',()=>{
  let c=core.candidatesFromFrames([frame]);assert.equal(core.chooseInitial(c),c[0].key);
  const f=structuredClone(frame);f.videos.push({...f.videos[0],index:1,src:'https://media.example/ad.mp4'});
  assert.equal(core.chooseInitial(core.candidatesFromFrames([f])),'');
});
test('do not associate resources across frames or fabricate a transferable blob URL',()=>{
  const f=structuredClone(frame);f.videos[0].src='blob:https://player.example/123';
  assert.equal(core.candidatesFromFrames([f,{...frame,videos:[],resources:['https://media.example/a.m3u8']}]).length,0);
});
test('active captions without a file block an empty subtitle handoff',()=>{
  const f=structuredClone(frame);f.videos[0].tracks=[];
  const [c]=core.candidatesFromFrames([f]);assert.equal(c.unresolvedSubtitles,1);
  assert.throws(()=>core.offer(c,[],uuid),/captions/);
});
test('unmatched subtitle resources are offered but not automatically sent',()=>{
  const f=structuredClone(frame);f.resources=['https://media.example/zh.vtt'];
  const [c]=core.candidatesFromFrames([f]);assert.equal(c.extraTracks.length,1);assert.equal(c.subtitles.length,1);
});
test('reject credentials, blobs, control characters and malformed protocol before native IPC',()=>{
  for(const u of ['file:///tmp/a','blob:https://a/1','https://user:pass@a/video','https://a/\r\n'])assert.equal(core.webURL(u),false);
  const [c]=core.candidatesFromFrames([frame]);const m=core.offer(c,c.subtitles,uuid);
  assert.throws(()=>core.validateOffer({...m,protocolVersion:2}));
  assert.throws(()=>core.validateOffer({...m,requestId:'not-uuid'}));
  assert.throws(()=>core.validateOffer({...m,payload:{...m.payload,currentTime:NaN}}));
});
test('permission request patterns cover only one observed origin',()=>{
  assert.equal(core.originPattern('https://player.example/embed/a?secret=1'),'https://player.example/*');
  assert.equal(core.originPattern('javascript:evil'),null);
});
test('injected discovery reads DOM sources, caption metadata and resources without mutation',()=>{
  const track={getAttribute:()=>'/sub.vtt',label:'English',srclang:'en',kind:'subtitles',track:{mode:'showing'}};
  const video={currentSrc:'blob:https://player.example/a',src:'',currentTime:7,duration:30,videoWidth:640,videoHeight:360,paused:false,
    textTracks:[{label:'English',language:'en',kind:'subtitles',mode:'showing'}],querySelectorAll:s=>s==='track'?[track]:[]};
  const context={URL,document:{baseURI:'https://player.example/embed',title:'Episode',querySelectorAll:s=>s==='video'?[video]:[]},
    location:{href:'https://player.example/embed'},performance:{getEntriesByType:()=>[{name:'https://media.example/a.m3u8'}]}};
  vm.createContext(context);vm.runInContext(fs.readFileSync(require.resolve('../../extensions/shared/discovery.js'),'utf8'),context);
  const found=context.inspectVideoFrame();assert.equal(found.videos[0].tracks[0].url,'https://player.example/sub.vtt');
  assert.equal(found.videos[0].textTracks[0].mode,'showing');assert.equal(found.resources.length,1);
});
test('empty track src is not mistaken for a subtitle URL',()=>{
  const {inspectVideoFrame}=require('../../extensions/shared/discovery.js');
  const context={URL,document:{baseURI:'https://player.example/embed',title:'Episode',querySelectorAll:s=>s==='video'?[{
    currentSrc:'https://media.example/a.mp4',currentTime:0,duration:10,textTracks:[],
    querySelectorAll:kind=>kind==='track'?[{getAttribute:()=>'',kind:'subtitles'}]:[] }]:[]},
    location:{href:'https://player.example/embed'},performance:{getEntriesByType:()=>[]}};
  vm.createContext(context);const result=vm.runInContext(`(${inspectVideoFrame.toString()})()`,context);
  assert.equal(result.videos[0].tracks.length,0);
});
test('background forwards only popup offers and treats accepted as handoff receipt',async()=>{
  let listener, calls=0;
  const [c]=core.candidatesFromFrames([frame]);const message=core.offer(c,c.subtitles,uuid);
  const context={VideoBridgeCore:core,browser:{action:{onClicked:{addListener:()=>{}}},runtime:{id:'test',getURL:x=>'moz-extension://test/'+x,
    onMessage:{addListener:f=>listener=f},sendNativeMessage:async(host,m)=>{calls++;assert.equal(host,'app.videobridge.bridge');
      return {protocolVersion:1,requestId:m.requestId,type:'accepted',payload:{token:'test-token'}};}}}};
  vm.createContext(context);vm.runInContext(fs.readFileSync(require.resolve('../../extensions/shared/background.js'),'utf8'),context);
  assert.equal(listener(message,{id:'test',url:'https://site.example'},()=>{}),false);assert.equal(calls,0);
  const reply=await new Promise(resolve=>listener(message,{id:'test',url:'moz-extension://test/popup.html'},resolve));
  assert.equal(reply.ok,true);assert.equal(calls,1);assert.equal(reply.reply,undefined);
});
test('background rejects mismatched native response request identifiers',async()=>{
  let listener;const [c]=core.candidatesFromFrames([frame]);const message=core.offer(c,c.subtitles,uuid);
  const context={VideoBridgeCore:core,browser:{action:{onClicked:{addListener:()=>{}}},runtime:{id:'test',getURL:x=>'moz-extension://test/'+x,
    onMessage:{addListener:f=>listener=f},sendNativeMessage:async()=>({protocolVersion:1,requestId:'wrong',type:'accepted',payload:{token:'x'}})}}};
  vm.createContext(context);vm.runInContext(fs.readFileSync(require.resolve('../../extensions/shared/background.js'),'utf8'),context);
  const reply=await new Promise(resolve=>listener(message,{id:'test',url:'moz-extension://test/popup.html'},resolve));assert.equal(reply.ok,false);
});

test('handoff rejects HTTP and nonstandard ports',()=>{
  for(const u of ['http://media.example/a.mp4','https://media.example:8443/a.mp4']) assert.equal(core.webURL(u),false);
  assert.equal(core.webURL('https://media.example:443/a.mp4'),true);
});

test('recognizable caption filenames get readable explicit language metadata',()=>{
 assert.equal(core.subtitleMetadata('https://media.example/track_0_eng.vtt').language,'en');
 assert.equal(core.subtitleMetadata('https://media.example/track_5_Brazil_por.vtt').label,'Portuguese (Brazil)');
 assert.equal(core.subtitleMetadata('https://media.example/arbitrary.vtt').language,'und');
});
