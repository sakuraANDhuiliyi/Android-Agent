/** Real Electron message receipts with an isolated API, local model stub, and dropped POST response. */
const assert = require("assert");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { _electron } = require("playwright");
const { isolatedSmokeEnv } = require("./smoke/environment");

const desktopDir = path.join(__dirname, "..");
const repoRoot = path.join(desktopDir, "..");
const SHARED_STUB = path.join(repoRoot, "tests", "e2e", "stub_scenario_model.py");
const SHARED_SCENARIOS = path.join(repoRoot, "tests", "e2e", "scenarios");

const AGENT_PORT = Number(process.env.AGENT_SUBMISSIONS_SMOKE_PORT || 8153);
const STUB_PORT = Number(process.env.AGENT_SUBMISSIONS_SMOKE_STUB_PORT || 9504);
const REG_TOKEN = "submissions-smoke-reg-token-123";
const SERVER_URL = `http://127.0.0.1:${AGENT_PORT}`;
const ACCOUNT_EMAIL = "desktop-submissions-smoke@example.com";
const ACCOUNT_PASSWORD = "secure-scenario-123";

const SMOKE_DATA = fs.mkdtempSync(path.join(os.tmpdir(), "agent-submissions-data-"));
const SMOKE_PROFILE = fs.mkdtempSync(path.join(os.tmpdir(), "agent-submissions-prof-"));

let svcLog = "";

const children = [];
function track(child) {
  children.push(child);
  return child;
}
function cleanup() {
  // SIGKILL: smoke services are disposable; graceful SIGTERM can hang on
  // non-daemon worker threads and leak ports that poison the next run.
  for (const child of children) {
    try {
      if (!child.killed) child.kill("SIGKILL");
    } catch (_) {}
  }
}
process.on("exit", cleanup);

function assertPortFree(port) {
  return new Promise((resolve, reject) => {
    const req = http.get({ host: "127.0.0.1", port, path: "/", timeout: 600 }, (res) => {
      res.resume();
      reject(new Error(`port ${port} is already in use (stale smoke service?): kill it first`));
    });
    req.on("error", () => resolve());
    req.on("timeout", () => {
      req.destroy();
      resolve();
    });
  });
}

function waitForTcp(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const tryOnce = () => {
      const req = http.get({ host: "127.0.0.1", port, path: "/", timeout: 700 }, (res) => {
        res.resume();
        resolve();
      });
      req.on("error", () => {
        if (Date.now() > deadline) reject(new Error(`port ${port} never opened`));
        else setTimeout(tryOnce, 400);
      });
      req.on("timeout", () => {
        req.destroy();
        if (Date.now() > deadline) reject(new Error(`port ${port} timeout`));
        else setTimeout(tryOnce, 400);
      });
    };
    tryOnce();
  });
}

function httpJson(method, urlPath, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req = http.request(
      {
        host: "127.0.0.1",
        port: AGENT_PORT,
        path: urlPath,
        method,
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      },
      (res) => {
        let raw = "";
        res.setEncoding("utf8");
        res.on("data", (c) => (raw += c));
        res.on("end", () => {
          try {
            resolve({ status: res.statusCode, json: raw ? JSON.parse(raw) : null });
          } catch (e) {
            resolve({ status: res.statusCode, json: raw });
          }
        });
      },
    );
    req.on("error", reject);
    req.end(data || undefined);
  });
}

async function waitUntil(fn, timeoutMs, desc) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    last = await fn();
    if (last) return last;
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`timeout waiting for: ${desc}`);
}

// The panel's conversation-switch chain clears #promptInput mid-flight and
// sendAsk() drops an empty prompt, so wait until no switch is in flight and
// the composer is ready before typing (same contract as electron-smoke).
async function settleComposer(page, desc) {
  for (let attempt = 0; attempt < 12; attempt += 1) {
    const before = await page.evaluate(() => {
      const s = window.AiPanel.getState();
      return { token: s.loadToken, conv: s.conversationId };
    });
    await page.waitForTimeout(400);
    const after = await page.evaluate(() => {
      const s = window.AiPanel.getState();
      return {
        token: s.loadToken,
        conv: s.conversationId,
        ready: s.connected && s.selectedProjectId && s.conversationId && !s.running,
      };
    });
    if (after.ready && after.token === before.token && after.conv === before.conv) return;
  }
  throw new Error(`composer never settled: ${desc}`);
}

function startService() {
  const svc = track(spawn('python3', ['-m', 'agent', 'serve', '--host', '127.0.0.1', '--port', String(AGENT_PORT)], {
    cwd: repoRoot,
    env: isolatedSmokeEnv({
      AGENT_DATA_DIR: SMOKE_DATA, AGENT_CONFIG_PATH: path.join(SMOKE_DATA, 'test-config.yaml'),
      AGENT_WORKSPACES_DIR: path.join(SMOKE_DATA, 'workspaces'), AGENT_BUILDS_DIR: path.join(SMOKE_DATA, 'builds'),
      AGENT_BASE_URL: `http://127.0.0.1:${STUB_PORT}`, AGENT_API_KEY: 'sk-recovery-stub', AGENT_PROVIDER: 'deepseek',
      AGENT_REGISTRATION_ENABLED: '1', AGENT_REGISTRATION_TOKEN: REG_TOKEN,
      AGENT_MAX_REQUESTS_PER_MINUTE: '100000', PYTHONUNBUFFERED: '1',
      ANDROID_HOME: path.join(SMOKE_DATA, 'sdk'), ANDROID_SDK_ROOT: path.join(SMOKE_DATA, 'sdk'),
    }), stdio: ['ignore', 'pipe', 'pipe'],
  }));
  svc.stdout.on('data', data => { svcLog += data; });
  svc.stderr.on('data', data => { svcLog += data; });
  return svc;
}
async function main() {
 await assertPortFree(AGENT_PORT);await assertPortFree(STUB_PORT);
 const scenarios=path.join(SMOKE_DATA,'scenarios');fs.mkdirSync(scenarios);
 const argv=['python3','-c','from pathlib import Path; p=Path("app/src/test/submission-once.txt"); p.parent.mkdir(parents=True,exist_ok=True); p.open("a").write("ONCE\\n"); print("SUBMISSION_TOOL_DONE")'];
 fs.writeFileSync(path.join(scenarios,'submission.json'),JSON.stringify({id:'desktop_submission',steps:[{type:'tool',calls:[{name:'run_command',arguments:{argv}}]},{type:'final',text:'SUBMISSION_DONE'}]}));
 track(spawn('python3',[SHARED_STUB],{cwd:repoRoot,env:isolatedSmokeEnv({AGENT_E2E_STUB_PORT:String(STUB_PORT),AGENT_E2E_SCENARIO_DIR:scenarios}),stdio:'ignore'}));
 await waitForTcp(STUB_PORT,15000);startService();await waitForTcp(AGENT_PORT,30000);
 const reg=await httpJson('POST','/api/auth/register',{body:{email:ACCOUNT_EMAIL,password:ACCOUNT_PASSWORD,display_name:'Submissions Smoke',device:{device_id:'submissions-smoke',device_name:'Submissions Smoke',device_type:'desktop'}}});assert.equal(reg.status,201);const token=reg.json.token;
 const project=await httpJson('POST','/api/projects',{token,body:{name:'submissions-smoke'}});assert.equal(project.status,201,JSON.stringify(project.json));const projectId=project.json.project?.id||project.json.id;
 const workspace=path.join(SMOKE_DATA,'workspaces',reg.json.user_id,projectId);
 const app=await _electron.launch({args:['.'],cwd:desktopDir,env:isolatedSmokeEnv({AGENT_DESKTOP_USER_DATA:SMOKE_PROFILE,ANDROID_AGENT_SERVER_URL:SERVER_URL})});track(app.process());const page=await app.firstWindow();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.waitForFunction(()=>window.AiPanel?.submissions);await page.evaluate(()=>AiPanel.openSettings());await page.fill('#accountEmail',ACCOUNT_EMAIL);await page.fill('#accountPassword',ACCOUNT_PASSWORD);await page.click('#btnAccountLogin');await page.waitForFunction(()=>AiPanel.getState().connected);await page.evaluate(async id=>{await AiPanel.refreshProjects();await AiPanel.selectProject(id);},projectId);await page.evaluate(()=>document.getElementById('settingsDialog').close());await settleComposer(page,'submission project');
  let totalPosts=0,drop=false,original=null,created=null,lookupCount=0;const postStatuses=[];
  page.on('response',r=>{if(r.url().includes('/submissions/'))lookupCount++;});
  await page.route('**/api/conversations/*/ask',async route=>{if(route.request().method()!=='POST')return route.continue();totalPosts++;const body=route.request().postDataJSON();const response=await route.fetch();postStatuses.push(response.status());if(drop){drop=false;original=body;created=await response.json();assert.equal(response.status(),201);return route.abort('failed');}await route.fulfill({response});});
  for(const [index,surface] of ['ai','cx'].entries()){
   const conversation=await httpJson('POST',`/api/projects/${projectId}/conversations`,{token,body:{title:`Submit ${surface}`}});assert.equal(conversation.status,201);const conversationId=conversation.json.conversation?.id||conversation.json.id;
   if(await page.evaluate(()=>CodexiaAgentView.isVisible()))await page.locator('#cxSidebarDownload').click();
   await page.evaluate(id=>AiPanel.selectConversation(id,{loadHistory:true}),conversationId);await settleComposer(page,'fresh submission conversation');
   if(surface==='cx'){await page.evaluate(({projectId,conversationId})=>{const state=CodexiaAgentView._internal.getState();state.selectedProjectId=projectId;state.selectedConversationId=conversationId;state.selectedId=null;}, {projectId,conversationId});await page.locator('[data-mode="agent-windows"]').click();await page.evaluate(()=>CodexiaAgentView.refresh());}
   const input=surface==='ai'?'#promptInput':'#cxAgentPrompt',send=surface==='ai'?'#btnSend':'#cxSendAgent',host=surface==='ai'?'#aiTaskSubmission':'#cxTaskSubmission';
   await page.evaluate(s=>{if(s==='ai'){const el=document.getElementById('runModeSelect');el.value='workspace';el.dispatchEvent(new Event('change'));}else CodexiaAgentView._internal.getState().runMode='workspace';},surface);
   drop=true;await page.fill(input,'password=synthetic_submission_secret [[desktop_submission]]');await page.click(send);await page.locator(host).getByText('任务提交结果待确认',{exact:false}).waitFor();assert.ok(original.request_key);assert.equal(created.submission.job_id,created.job.id);
   const before=totalPosts,readsBefore=lookupCount;await page.reload();await page.waitForFunction(()=>AiPanel.getState().connected);await waitUntil(()=>lookupCount>readsBefore,15000,'reload scoped GET candidate');assert.equal(totalPosts,before,'reload never resends POST');
   if(surface==='cx'){await page.locator('[data-mode="agent-windows"]').click();await page.evaluate(()=>CodexiaAgentView.refresh());}
   await page.locator(host).getByText('已找到关联任务，原提交待确认',{exact:false}).waitFor();
   assert.equal(await page.evaluate(()=>AiPanel.getState().currentJobId),null,'cached/latest AI job cannot attach before body ACK');assert.equal(await page.evaluate(()=>CodexiaAgentView._internal.getState().selectedId),null,'cached/latest CX job cannot attach before body ACK');
   const awaiting=await waitUntil(async()=>{const job=(await httpJson('GET',`/api/jobs/${created.job.id}`,{token})).json.job;return job.status==='awaiting_approval'?job:null;},20000,'candidate stays awaiting explicit approval');assert.equal(awaiting.status,'awaiting_approval');assert.equal(fs.existsSync(path.join(workspace,'app/src/test/submission-once.txt')),index>0);
   await page.locator(host).getByRole('button',{name:'查看关联任务',exact:true}).click();await page.getByRole('dialog',{name:'关联任务详情'}).waitFor();assert.match(await page.getByRole('dialog').textContent(),/REDACTED/);await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).click();assert.equal((await httpJson('GET',`/api/jobs/${created.job.id}`,{token})).json.job.status,'awaiting_approval');
   await page.fill(input,'new draft survives original ACK');await page.locator(host).getByRole('button',{name:'核对并重试原提交'}).click();await page.waitForFunction(()=>AiPanel.submissions.records.size===0);assert.equal(postStatuses.at(-1),200);assert.equal(await page.inputValue(input),'new draft survives original ACK');
   assert.equal(await page.evaluate(s=>s==='ai'?AiPanel.getState().currentJobId:CodexiaAgentView._internal.getState().selectedId,surface),created.job.id);
   const approvals=(await httpJson('GET',`/api/jobs/${created.job.id}/approvals`,{token})).json.approvals;assert.equal(approvals.length,1);assert.deepEqual(approvals[0].payload.argv,argv);assert.equal(approvals[0].payload.tool_name,'run_command');assert.equal((await httpJson('POST',`/api/jobs/${created.job.id}/approvals/${approvals[0].id}`,{token,body:{approved:true}})).status,200);
   await waitUntil(async()=>(await httpJson('GET',`/api/jobs/${created.job.id}`,{token})).json.job.status==='succeeded',25000,'one real command completes');
   const replay=await httpJson('POST',`/api/conversations/${conversationId}/ask`,{token,body:original});assert.equal(replay.status,200);assert.equal(replay.json.job.id,created.job.id);assert.equal(replay.json.job.status,'succeeded');
   const jobs=(await httpJson('GET',`/api/projects/${projectId}/jobs?conversation_id=${conversationId}`,{token}));
   const listed=Array.isArray(jobs.json.jobs)?jobs.json.jobs:(await httpJson('GET',`/api/jobs?project_id=${projectId}&conversation_id=${conversationId}`,{token})).json.jobs;
   assert.equal(listed.length,1);assert.equal(fs.readFileSync(path.join(workspace,'app/src/test/submission-once.txt'),'utf8'),'ONCE\n'.repeat(index+1));
   const conflicting=await httpJson('POST',`/api/conversations/${conversationId}/ask`,{token,body:{...original,prompt:'different'}});assert.equal(conflicting.status,409);
   console.log(`ok - ${surface}: actual 201 lost, reload GET only, no auto-attach/approval, redacted candidate, original 200 retry, exact job, once-only tool, terminal replay/CAS`);
  }
  assert.deepEqual(errors,[]);console.log('electron-submissions-smoke.test: OK');
 }finally{await app.close().catch(()=>{});}
}
main().catch(error=>{console.error(error);console.error(svcLog.slice(-9000));process.exitCode=1;}).finally(cleanup);
