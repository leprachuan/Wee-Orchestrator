import assert from 'node:assert/strict';
import { startGlobalInbox } from '../webui/dist/autonomy.js';
class Element {
  constructor(tag) { this.tag=tag;this.children=[];this.style={};this.dataset={};this.className='';this.hidden=false;this.value='';this.disabled=false;this.classes=new Set();this.classList={add:v=>this.classes.add(v),remove:v=>this.classes.delete(v)}; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children=[...nodes]; }
  setAttribute() {}
  focus() {}
  querySelectorAll(tag) { return this.children.flatMap(c=>[...(c.tag===tag?[c]:[]),...c.querySelectorAll(tag)]); }
}
const body=new Element('body');globalThis.document={body,createElement:tag=>new Element(tag),addEventListener(){}};
let tick;globalThis.setInterval=callback=>{tick=callback;};
const calls=[];let snapshot={instance_id:'hub',approvals:[],steering:[],peers:[{instance_id:'mac-origin',approvals:[{id:'a',fingerprint:'fp',status:'pending',scope:{agent:'other-agent',operation:'file.write',host:'api-host',resource:'/work/report.md'},preview:{summary:'Save report',details:'Review me'}}],steering:[{id:'s',revision:'rev',status:'pending',agent:'other-agent',question:'Which deadline?'}]}]};
startGlobalInbox({isAuthenticated:()=>true,request:async(method,path,data)=>{calls.push({method,path,data});if(method==='GET')return structuredClone(snapshot);return {won:true};}});
const flush=()=>new Promise(resolve=>setImmediate(resolve));await flush();
const badge=body.children[0];assert.equal(badge.hidden,false);assert.equal(badge.textContent,'2 agent requests');
badge.onclick();assert.equal(body.children[1].classes.has('hidden'),false);
const answer=body.querySelectorAll('textarea')[0];answer.value='Friday';answer.oninput();
snapshot.peers[0].approvals[0].preview.details='New arrival';await tick();await flush();
assert.equal(body.querySelectorAll('textarea')[0].value,'Friday');
const send=body.querySelectorAll('button').find(b=>b.textContent==='Send steering');await send.onclick();await flush();
assert.deepEqual(calls.find(c=>c.method==='POST'),{method:'POST',path:'/autonomy/inbox/mac-origin/s/decision',data:{kind:'steering',answer:'Friday',revision:'rev'}});
const deny=body.querySelectorAll('button').find(b=>b.textContent==='Deny');await deny.onclick();await flush();
assert.equal(calls.filter(c=>c.method==='POST').at(-1).data.decision,'reject');
snapshot.peers=[];await tick();await flush();assert.equal(badge.hidden,true);
console.log('Global inbox origin routing, drafts and rejection checks passed');
