const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const http = require("node:http");
const { chromium } = require("playwright");
const { describe } = require("../src/delivery-ui");
assert.equal(describe({status:"succeeded"}).checks.filter(c=>c.available).length,0,"success alone must not imply any verification");
assert.match(describe({status:"running",cancel_requested:true}).label,/等待执行进程退出/);
assert.equal(describe({status:"canceled",cancel_requested:true}).terminal,true);
const root=path.resolve(__dirname,"../..");
const output=path.join(root,".artifacts/ui-v4");
const server=http.createServer(async(req,res)=>{try{const p=path.resolve(root,"agent/admin",new URL(req.url,"http://localhost").pathname.replace(/^\//,""));if(!p.startsWith(path.join(root,"agent/admin/")))throw Error("invalid");const data=await fs.readFile(p);res.writeHead(200,{"Content-Type":p.endsWith(".css")?"text/css":p.endsWith(".js")?"application/javascript":"text/html"});res.end(data);}catch{res.writeHead(404);res.end();}});
async function run(){
 await fs.mkdir(output,{recursive:true});await new Promise(r=>server.listen(0,"127.0.0.1",r));
 const browser=await chromium.launch();
 try {
 const page=await browser.newPage({viewport:{width:1440,height:960}}),errors=[],writes=[];
 page.on("pageerror",e=>errors.push(e.message));
 const content={schema_version:1,title:"灵动底部导航",summary:"为常用页面提供清晰、流畅的导航体验。",category_id:"component",ui_stack:"compose",min_sdk:24,files:[{path:"Navigation.kt",content:"@Composable fun Navigation() {}"}],tags:["导航"],license:"MIT"};
 const item={id:"creative-1",origin:"official",row_version:3,distribution_state:"listed",revision_id:"rev-2",published_revision_id:"rev-1",content,revisions:[{id:"rev-2",version_no:2,state:"draft"},{id:"rev-1",version_no:1,state:"approved"}]};
 let fail=false;
 await page.route("**/api/**",async route=>{
  const url=new URL(route.request().url()),p=url.pathname,method=route.request().method();
  let body={};
  if(method!=="GET"){writes.push({p,method,body:route.request().postDataJSON()});body=item;}
  else if(p==="/api/admin/overview")body={accounts:128,active_tokens:96,verified_accounts:112,disabled_accounts:3};
  else if(p==="/api/admin/accounts")body={accounts:[{user_id:"user-demo",display_name:"设计演示账号",email:"preview@example.test",email_verified:true,active_tokens:2}]};
  else if(p.endsWith("capabilities"))body={submissions_enabled:false};
  else if(p.endsWith("/reviews"))body={items:[],next_offset:null};
  else if(p.endsWith("/metrics"))body={published:8,community_published:3,pending_reviews:1,open_reports:1,favorites:12,submissions_24h:4};
  else if(p.endsWith("/reports"))body={items:[{id:"report-1",creative_id:"creative-1",title:"灵动底部导航",reason:"privacy",state:"open",duplicate_count:2}],next_offset:null};
  else if(p.endsWith("/reports/report-1"))body={id:"report-1",creative_id:"creative-1",revision_id:"rev-1",reporter_user_id:"reporter-demo",reason:"privacy",details:'包含 <img onerror="alert(1)"> 联系信息',state:"open",row_version:1,resolution:"",private_note:"",creative_row_version:3,distribution_state:"listed",title:"灵动底部导航"};
  else if(p.endsWith("categories"))body=[{id:"component",label:"组件",sort_order:0,enabled:true,row_version:1}];
  else if(p.endsWith("/items")){
   if(fail){await route.fulfill({status:503,json:{detail:"服务暂时不可用，请稍后重试"}});return;}
   const titles=["灵动底部导航","可展开的信息卡片","轻量登录页面","渐进式加载反馈","数据概览布局","柔和的深色主题"];
   body={items:titles.map((title,i)=>({id:i?`creative-${i+1}`:"creative-1",title,origin:i%2?"community":"official",version_no:2,review_state:i%2?"draft":"approved",distribution_state:i%3?"listed":"unpublished"})),next_offset:null};
  }else if(p.endsWith("audit"))body={items:[]};else body=item;
  await route.fulfill({status:200,json:body});
 });
 await page.goto(`http://127.0.0.1:${server.address().port}/index.html`);
 await page.locator("#adminToken").fill("offline-fixture-token");await page.locator("#loginForm button").click();await page.locator("#appShell").waitFor({state:"visible"});
 await page.evaluate(()=>{document.documentElement.dataset.theme="light";});
 await page.screenshot({path:path.join(output,"admin-accounts-light.png")});
 await page.locator('[data-admin-view="creative"]').click();await page.locator(".studio-table").getByText("灵动底部导航",{exact:true}).waitFor();
 assert.equal(await page.locator(".studio-table tbody tr").count(),6);
 await page.screenshot({path:path.join(output,"admin-creative-light.png")});
 await page.getByRole("button",{name:"编辑与版本"}).first().click();await page.locator("dialog[open]").waitFor();
 await page.locator('[name="title"]').fill('<img src=x onerror="alert(1)">安全标题');
 assert.equal(await page.locator(".studio-preview-card img").count(),0,"untrusted titles must be text");
 await page.getByRole("button",{name:"发布已保存版本"}).click();
 assert.equal(writes.length,0,"publishing dirty forms must not silently publish an old snapshot");
 assert.match(await page.locator(".studio-editor .form-error").innerText(),/先保存/);
 await page.locator('[name="title"]').fill("灵动底部导航");
 await page.screenshot({path:path.join(output,"admin-editor-light.png")});
 await page.getByRole("button",{name:"保存草稿",exact:true}).click();
 await page.waitForFunction(()=>document.querySelector(".toast")?.textContent.includes("草稿已保存"));
 assert.equal(writes[0].method,"PUT");assert.equal(writes[0].body.expected_version,3);assert.equal(writes[0].body.content.files[0].path,"Navigation.kt");
 await page.locator("dialog[open]").waitFor();await page.getByRole("button",{name:"关闭编辑器"}).click();
 await page.locator('[data-admin-view="reviews"]').click();await page.getByText("新投稿暂未开放，已有稿件仍可审核。",{exact:true}).waitFor();await page.getByText("当前没有待审稿件",{exact:true}).waitFor();
 await page.locator('[data-admin-view="reports"]').click();await page.locator(".studio-table tbody").getByText("灵动底部导航",{exact:true}).waitFor();assert.equal(await page.locator(".studio-metrics .metric").count(),4);
 await page.getByRole("button",{name:"查看并处理",exact:true}).click();await page.getByText('包含 <img onerror="alert(1)"> 联系信息',{exact:true}).waitFor();assert.equal(await page.locator("dialog[open] img").count(),0);
 await page.screenshot({path:path.join(output,"admin-report-light.png")});
 await page.getByLabel("举报人可见的处理结果",{exact:true}).fill("已核对并完成处理");await page.getByLabel("管理员内部备注（不向举报人或作者公开）",{exact:true}).fill("内部记录");await page.getByRole("button",{name:"确认已处理",exact:true}).click();await page.locator("dialog[open]").waitFor({state:"hidden"});
 const reportWrite=writes.find(row=>row.p.endsWith("/reports/report-1"));assert.equal(reportWrite.body.decision,"resolve");assert.equal(reportWrite.body.private_note,"内部记录");
 await page.setViewportSize({width:390,height:844});await page.evaluate(()=>{document.documentElement.dataset.theme="dark";});
 await page.screenshot({path:path.join(output,"admin-review-mobile-dark.png")});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,"mobile page must not overflow horizontally");
 assert.equal(await page.locator('[data-admin-view="creative"]').isVisible(),true,"mobile navigation remains accessible");
 await page.locator('[data-admin-view="creative"]').click();await page.locator(".studio-table").getByText("灵动底部导航",{exact:true}).waitFor();
 fail=true;await page.getByRole("button",{name:"刷新",exact:true}).click();await page.getByText("暂时无法读取",{exact:true}).waitFor();
 assert.equal(await page.getByRole("button",{name:"重新加载"}).isVisible(),true);
 await page.locator("#logoutButton").click();assert.equal(await page.locator("#loginGate").isVisible(),true);assert.equal(await page.locator("#studioView").innerText(),"");
 assert.deepEqual(errors,[]);console.log("studio-ui: safe preview, versioned save, publication guard, capability state, responsive navigation, retry and logout passed");
 } finally {await browser.close();server.close();}
}
run().catch(e=>{console.error(e);server.close();process.exitCode=1;});
