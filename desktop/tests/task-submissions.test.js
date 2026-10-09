const assert = require('node:assert/strict');
const { Store, normalize, freezeBody, STORAGE_KEY, MAX_BYTES } = require('../src/task-submissions');
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return { promise, resolve, reject }; };
function fixture(raw = null) {
  let value = raw;
  const storage = { getItem: () => value, setItem: (_, next) => { value = next; } };
  const who = { connected: true, userId: 'alice' }, requests = [], reads = [], creates = [];
  const client = { baseUrl: 'https://EXAMPLE.test/', sessionVersion: 1,
    askConversation: (conversation, body) => { const d = deferred(); requests.push({ ...d, conversation, body }); return d.promise; },
    lookupTaskSubmission: (conversation, key) => { const d=deferred(); reads.push({ ...d, conversation, key }); return d.promise; },
    createConversation: project => { const d=deferred(); creates.push({ ...d, project }); return d.promise; } };
  let id=0; const store = new Store(client, () => who, { storage, uuid: () => `key-${++id}` });
  return { store, client, who, requests, reads, creates, storage, raw: () => value, scope: () => store.scope('p','c') };
}
function response(request, status='queued', extra={}) {
  const submission = { schema_version:1, request_key:request.body?.request_key || request.key, project_id:'p', conversation_id:'c', job_id:'j', turn_id:'t', created_at:123 };
  return { schema_version:1, submission, conversation_id:'c', job:{ id:'j', project_id:'p', conversation_id:'c', turn_id:'t', status }, ...extra };
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const start = f => f.store.submitInitial({ projectId:'p', conversationId:'c', body:{ prompt:'same prompt', provider:null, contexts:[{type:'file',path:'A'}],auto_fallback:false,run_mode:'workspace' } });
(async () => {
  let f=fixture(); let pending=start(f); assert.ok(f.raw().includes('same prompt')); await flush();
  assert.equal(f.requests.length,1); assert.ok(!f.raw().includes('sessionVersion')); assert.ok(!f.raw().includes('token'));
  await assert.rejects(start(f),/待确认/); assert.equal(f.requests.length,1);
  f.requests[0].reject(new Error('lost')); assert.equal(await pending,null);
  const frozen=f.store.pending(f.scope()); assert.equal(frozen.request_key,'key-1');
  f=fixture(f.raw()); f.client.sessionVersion=4; const lookup=f.store.lookup(f.scope()); f.reads[0].resolve(response(f.reads[0], 'succeeded')); await lookup;
  assert.ok(f.store.state(f.scope()).candidate); assert.ok(f.store.pending(f.scope())); assert.equal(f.requests.length,0);
  const retry=f.store.post(f.scope()); await flush(); assert.equal(f.requests[0].body.request_key,'key-1'); assert.deepEqual(f.requests[0].body.contexts,[{type:'file',path:'A'}]);
  f.requests[0].resolve(response(f.requests[0],'succeeded')); assert.equal((await retry).job.status,'succeeded'); assert.equal(f.store.pending(f.scope()),undefined);
  assert.equal(f.store.canAutoAttach(f.scope()),false,'ACK cannot expose latest-task auto-attach before exact adoption'); f.store.adopted(f.scope(),'j'); assert.equal(f.store.canAutoAttach(f.scope()),true);

  for (const status of [400,401,403,404,409,413,422,429,500]) {
    f=fixture(); pending=start(f); await flush(); f.requests[0].reject(Object.assign(new Error(`status ${status}`),{status})); await pending;
    assert.ok(f.store.pending(f.scope())); assert.equal(f.requests.length,1); assert.equal(f.store.state(f.scope()).phase,[400,403,404,409,413,422].includes(status)?'rejected':'unconfirmed');
  }
  for (const corrupt of ['{', '{}', JSON.stringify({schema_version:1,pending:[{}],manual_attach_scopes:[]}), 'x'.repeat(MAX_BYTES+1)]) {
    f=fixture(corrupt); assert.equal(f.store.ready,false); await assert.rejects(start(f)); assert.equal(f.requests.length,0); assert.equal(f.raw(),corrupt); assert.equal(f.store.canAutoAttach(f.scope()),false);
  }
  for (const storage of [null, {}, {getItem:()=>null}, {getItem:()=>{throw new Error('denied')},setItem(){}}]) {
    f=fixture(); const store=new Store(f.client,()=>f.who,{storage}); await assert.rejects(store.submitInitial({projectId:'p',conversationId:'c',body:{prompt:'x'}})); assert.equal(f.requests.length,0);
  }
  f=fixture(); f.storage.setItem=()=>{throw new Error('quota')}; await assert.rejects(start(f),/保存失败/); assert.equal(f.requests.length,0);
  f=fixture(); pending=start(f); await flush(); f.requests[0].reject(new Error('lost')); await pending;
  const before=f.raw(); f.storage.setItem=()=>{throw new Error('quota')}; assert.throws(()=>f.store.removeLocal(f.scope()),/保存失败/); assert.equal(f.raw(),before); assert.ok(f.store.pending(f.scope()));
  f=fixture(before); assert.equal(f.store.removeLocal(f.scope()),true); assert.equal(f.store.canAutoAttach(f.scope()),false); f=fixture(f.raw()); assert.equal(f.store.canAutoAttach(f.scope()),false); assert.equal(f.store.pending(f.scope()),undefined);
  pending=start(f); await flush(); f.requests[0].resolve(response(f.requests[0])); await pending; assert.equal(f.store.holds.size,0);

  for (const mutate of [f=>{f.client.sessionVersion++},f=>{f.who.userId='bob'},f=>{f.client.baseUrl='https://foreign.test'},f=>{f.store.blur();f.store.focus()}]) {
    f=fixture(); pending=start(f); mutate(f); await pending;
    assert.equal(f.requests.length,0,'the final microtask must validate identity before touching the current API client'); assert.equal(f.store.records.size,1);
  }
  f=fixture(); let beforePost=true;
  pending=f.store.submitInitial({projectId:'p',conversationId:'c',body:{prompt:'private'}},{isCurrent:()=>beforePost}); beforePost=false; await pending; assert.equal(f.requests.length,0); assert.equal(f.store.records.size,1);
  f=fixture(); await assert.rejects(f.store.submitInitial({projectId:'p',conversationId:'c',body:{prompt:'bounded',contexts:[{text:'x'.repeat(MAX_BYTES)}]}}),/容量/); assert.equal(f.requests.length,0);
  f=fixture();
  for(let i=0;i<10;i++) { const task=f.store.submitInitial({projectId:'p',conversationId:`c${i}`,body:{prompt:`draft ${i}`}}); await flush(); f.requests[i].reject(new Error('lost')); await task; }
  await assert.rejects(f.store.submitInitial({projectId:'p',conversationId:'c11',body:{prompt:'overflow'}}),/10/); assert.equal(f.requests.length,10); assert.equal(f.store.records.size,10);

  for (const mutate of [f=>{f.client.sessionVersion++},f=>{f.who.userId='bob'},f=>{f.client.baseUrl='https://elsewhere.test'},f=>{f.store.blur();f.store.focus()}]) {
    f=fixture(); pending=start(f); await flush(); mutate(f); f.requests[0].resolve(response(f.requests[0])); assert.equal(await pending,null); assert.equal(f.store.records.size,1);
  }
  f=fixture(); let current=true; pending=f.store.submitInitial({projectId:'p',conversationId:'c',body:{prompt:'x'}},{isCurrent:()=>current}); await flush(); current=false; f.requests[0].resolve(response(f.requests[0])); await pending; assert.equal(f.store.records.size,1);
  f=fixture(); pending=start(f); await flush(); f.requests[0].resolve(response(f.requests[0],'queued',{job:{id:'other'}})); assert.equal(await pending,null); assert.ok(f.store.pending(f.scope()));
  f=fixture(); pending=start(f); await flush(); f.requests[0].reject(new Error('lost')); await pending;
  const old=f.store.lookup(f.scope()), fresh=f.store.lookup(f.scope()); f.reads[1].resolve(response(f.reads[1],'paused')); await fresh; f.reads[0].reject(Object.assign(new Error('not found'),{status:404})); await old; assert.equal(f.store.state(f.scope()).candidate.job.status,'paused');
  assert.equal(f.store.records.size,1,'GET never confirms body, even exact text or redacted match');

  f=fixture(); const body={prompt:'frozen',provider:'before',contexts:[{type:'file',path:'a'}]};
  pending=f.store.submitInitial({projectId:'p',conversationId:null,body}); body.prompt='changed'; body.contexts[0].path='b';
  assert.equal(f.creates.length,1); await assert.rejects(start(f),/待确认/); f.creates[0].resolve({id:'c',project_id:'p'}); await flush(); assert.equal(f.requests[0].body.prompt,'frozen'); assert.equal(f.requests[0].body.contexts[0].path,'a'); f.requests[0].resolve(response(f.requests[0])); await pending;
  f=fixture(); pending=f.store.submitInitial({projectId:'p',body:{prompt:'x'}}); f.creates[0].resolve({id:'c',project_id:'foreign'}); await assert.rejects(pending,/归属/); assert.equal(f.requests.length,0);
  f=fixture(); pending=f.store.submitInitial({projectId:'p',body:{prompt:'x'}}); f.creates[0].reject(new Error('create lost')); await assert.rejects(pending,/lost/); assert.equal(f.requests.length,0);

  const good=response({key:'key-1'}), scope={project:'p',conversation:'c'};
  assert.ok(normalize(good,scope,'key-1')); const lookupGood={...good};delete lookupGood.conversation_id;assert.ok(normalize(lookupGood,scope,'key-1',{lookup:true}));assert.equal(normalize({...good,conversation_id:'wrong'},scope,'key-1',{lookup:true}),null);
  for (const patch of [{schema_version:2},{submission:{...good.submission,created_at:0}},{submission:{...good.submission,request_key:'other'}},{job:{...good.job,turn_id:'other'}},{job:{...good.job,project_id:'other'}},{conversation_id:'other'}]) assert.equal(normalize({...good,...patch},scope,'key-1'),null);
  assert.equal(normalize(good,scope,'bad key'),null); assert.throws(()=>freezeBody({prompt:'',provider:null})); assert.throws(()=>freezeBody({prompt:'x',request_key:'x'}));
  console.log('task-submissions.test: OK (durability, identity, immutable body, GET candidate, hold, storage, ABA, bad ACK, creation race)');
})().catch(error=>{console.error(error);process.exitCode=1});
