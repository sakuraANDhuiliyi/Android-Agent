/* Real HTTP + browser coverage. All databases and images live in a temporary directory. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const { chromium } = require("playwright");
const root = path.resolve(__dirname, "../..");
const token = "isolated-creative-browser-test-token";
const python = `
import os, socket, json
from pathlib import Path
from dataclasses import replace
import uvicorn
from PIL import Image
from agent.api import create_app
from agent.database import TaskStore
from agent.users import UserStore
from tests.test_accounts_api import settings
root = Path(os.environ["AGENT_DATA_DIR"])
Image.new("RGB", (120, 80), "#c1d6e8").save(root / "cover.png")
users = UserStore(root / "users.db")
community = os.environ.get("CREATIVE_TEST_COMMUNITY") == "1"
if community:
    accounts = [users.register_account(f"creator{i}@example.test", "isolated-creator-123", display_name=f"创作者 {i}") for i in range(2)]
    (root / "test-authors.json").write_text(json.dumps(accounts))
app = create_app(replace(settings(), creative_submissions_enabled=community, admin_ui_enabled=True, admin_token="${token}"), user_store=users, task_store=TaskStore(root / "agent.db"))
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print("CREATIVE_TEST_PORT=" + str(sock.getsockname()[1]), flush=True)
uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="warning")).run(sockets=[sock])
`;

async function run() {
  const data = await fs.mkdtemp(path.join(os.tmpdir(), "creative-live-"));
  const output = path.join(root, process.env.CREATIVE_TEST_COMMUNITY === "1" ? ".artifacts/creative-c2" : ".artifacts/creative-c1");
  await fs.mkdir(output, {recursive:true});
  const server = spawn(process.env.CREATIVE_TEST_PYTHON || "python3", ["-c", python], {
    cwd:root, env:{...process.env, AGENT_DATA_DIR:data}, stdio:["ignore", "pipe", "pipe"],
  });
  let logs = "", browser;
  server.stderr.on("data", chunk => { logs += chunk; });
  try {
    const port = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(Error("Test server startup timed out: " + logs)), 20000);
      server.stdout.on("data", chunk => {const match=String(chunk).match(/CREATIVE_TEST_PORT=(\d+)/);if(match){clearTimeout(timeout);resolve(Number(match[1]));}});
      server.once("error", error => {clearTimeout(timeout);reject(error);});
      server.once("exit", code => {clearTimeout(timeout);reject(Error(`Test server exited ${code}: ${logs}`));});
    });
    const base = `http://127.0.0.1:${port}`;
    for(let attempt=0;;attempt++) {
      try {await fetch(base+"/api/creative/capabilities");break;}
      catch(error){if(attempt>=50)throw error;await new Promise(r=>setTimeout(r,100));}
    }
    const json = async (route, admin=false) => {
      const response=await fetch(base+route,{headers:admin?{Authorization:`Bearer ${token}`}:{}});
      assert.equal(response.status,200,await response.clone().text());return response.json();
    };
    assert.equal((await fetch(base+"/api/admin/creative/items")).status,401);
    browser=await chromium.launch();
    const page=await browser.newPage({viewport:{width:1440,height:960}}),errors=[];
    page.on("pageerror", error=>errors.push(error.message));
    await page.goto(base+"/admin/");
    await page.locator("#adminToken").fill(token);
    await page.locator("#loginForm button").click();
    await page.locator("#appShell").waitFor({state:"visible"});
    await page.locator('[data-admin-view="creative"]').click();
    await page.getByRole("button",{name:"＋ 新建创意",exact:true}).click();
    await page.locator('[name="title"]').fill("云端卡片 v1");
    await page.locator('[name="summary"]').fill("来自真实管理后台的第一版");
    await page.getByLabel("文件相对路径",{exact:true}).fill("CloudCard.kt");
    await page.getByLabel("源码",{exact:true}).fill("@Composable fun CloudCard() { Text(\"v1\") }");
    await page.locator('input[type="file"]').setInputFiles(path.join(data,"cover.png"));
    await page.getByRole("button",{name:"保存草稿",exact:true}).click();
    await page.getByRole("button",{name:"发布已保存版本",exact:true}).waitFor();
    assert.deepEqual((await json("/api/creative/items")).items,[]);
    await page.getByRole("button",{name:"发布已保存版本",exact:true}).click();
    await page.locator("dialog[open]").waitFor({state:"hidden"});
    let card=(await json("/api/creative/items")).items[0];
    assert.equal(card.title,"云端卡片 v1");
    const id=card.id,cover=card.cover_asset_id;
    assert.equal((await fetch(base+`/api/creative/assets/${cover}`)).status,200);
    await page.getByRole("button",{name:"编辑与版本",exact:true}).click();
    await page.locator('[name="title"]').fill("云端卡片 v2");
    await page.getByRole("button",{name:"发布已保存版本",exact:true}).click();
    assert.match(await page.locator("dialog[open] .form-error").innerText(),/先保存/);
    await page.getByRole("button",{name:"保存草稿",exact:true}).click();
    await page.getByRole("button",{name:/v2 · 草稿/}).waitFor();
    assert.equal((await json(`/api/creative/items/${id}`)).title,"云端卡片 v1");
    await page.screenshot({path:path.join(output,"admin-version-editor.png")});
    await page.getByRole("button",{name:"发布已保存版本",exact:true}).click();
    await page.locator("dialog[open]").waitFor({state:"hidden"});
    assert.equal((await json(`/api/creative/items/${id}`)).title,"云端卡片 v2");
    // Roll back to an immutable historical snapshot via the actual editor.
    await page.getByRole("button",{name:"编辑与版本",exact:true}).click();
    await page.getByRole("button",{name:/^v1 ·/}).click();
    await page.waitForFunction(()=>document.querySelector('[name="title"]')?.value==="云端卡片 v1");
    await page.getByRole("button",{name:"发布已保存版本",exact:true}).click();
    await page.locator("dialog[open]").waitFor({state:"hidden"});
    assert.equal((await json(`/api/creative/items/${id}`)).title,"云端卡片 v1");
    await page.getByRole("button",{name:"编辑与版本",exact:true}).click();
    await page.getByLabel("推荐展示",{exact:true}).check();
    await page.getByLabel("展示排序",{exact:true}).fill("-20");
    await page.getByRole("button",{name:"更新展示设置",exact:true}).click();
    await page.waitForFunction(()=>document.querySelector("#toast")?.textContent==="展示设置已更新");
    assert.equal((await json("/api/creative/items")).items[0].featured,true);
    await page.locator("dialog[open]").waitFor();
    await page.getByLabel("操作原因",{exact:true}).fill("测试下架");
    await page.getByRole("button",{name:"下架",exact:true}).click();
    await page.locator("dialog[open]").waitFor({state:"hidden"});
    assert.deepEqual((await json("/api/creative/items")).items,[]);
    assert.equal((await fetch(base+`/api/creative/items/${id}`)).status,404);
    assert.equal((await fetch(base+`/api/creative/assets/${cover}`)).status,404);
    await page.getByRole("button",{name:"编辑与版本",exact:true}).click();
    await page.getByLabel("操作原因",{exact:true}).fill("复核完成");
    await page.getByRole("button",{name:"恢复为未发布",exact:true}).click();
    await page.locator("dialog[open]").waitFor({state:"hidden"});
    assert.deepEqual((await json("/api/creative/items")).items,[]);
    const audit=(await json("/api/admin/creative/audit",true)).items;
    for(const action of ["create","save_draft","publish","placement","unpublish","restore"])assert(audit.some(a=>a.action===action),action);
    assert(!JSON.stringify(audit).includes(token));
    await page.getByRole("button",{name:"导入内置创意",exact:true}).click();
    await page.getByRole("button",{name:"开始导入",exact:true}).click();
    await page.getByText("完成：新增 500，跳过 0，上架 0。",{exact:true}).waitFor({timeout:120000});
    assert.deepEqual((await json("/api/creative/items")).items,[],"default import must not publish drafts");
    await page.getByRole("button",{name:"完成",exact:true}).click();
    await page.locator('[data-admin-view="reviews"]').click();
    if(process.env.CREATIVE_TEST_COMMUNITY === "1")await require("./creative-community-live.test.js")({page,base,data,output});
    else await page.getByText("新投稿暂未开放，已有稿件仍可审核。",{exact:true}).waitFor();
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(output,"admin-mobile-review.png")});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.locator("#logoutButton").click();
    await page.locator("#loginGate").waitFor({state:"visible"});
    assert.deepEqual(errors,[]);
    console.log("creative-live: create + cover, draft isolation, publish, rollback, placement, takedown, explicit restore, audit, 500-item draft import, mobile layout and logout passed");
  } finally {
    if(browser)await browser.close();
    if(server.exitCode===null){const exited=once(server,"exit");server.kill("SIGTERM");await exited;}
    await fs.rm(data,{recursive:true,force:true});
  }
}
run().catch(error=>{console.error(error);process.exitCode=1;});
