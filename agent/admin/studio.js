/* Creative management UI; domain transitions stay in the creative API. */
(() => {
  "use strict";
  const host = document.getElementById("studioView");
  if (!host) return;
  const base = "/api/admin/creative";
  let view = "accounts", generation = 0, offset = 0, query = "", filter = "", editor = null;
  const names = {creative:"创意目录",reviews:"审核队列",reports:"举报处理",categories:"分类与标签",audit:"操作日志"};
  const statuses = {listed:"已上架",unpublished:"未发布",admin_blocked:"已下架",author_hidden:"作者已撤回",archived:"已归档",draft:"草稿",approved:"已批准",submitted:"待审核",in_review:"审核中",changes_requested:"需修改",rejected:"已拒绝",withdrawn:"已撤回",open:"待处理",reviewing:"处理中",resolved:"已处理",dismissed:"已驳回"};
  const reportReasons={copyright:"版权授权",privacy:"隐私信息",malware:"危险内容",misleading:"描述不实",other:"其他"};
  const node = (tag, value, cls) => { const n = document.createElement(tag); if(value != null)n.textContent=value; if(cls)n.className=cls; return n; };
  const button = (label, action, cls="button ghost") => { const b=node("button",label,cls); b.type="button"; b.addEventListener("click",action); return b; };
  async function request(path, options={}) { return api(base+path,options); }
  function theme() {
    const value=document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme=value; localStorage.setItem("admin-ui-theme",value);
  }
  document.documentElement.dataset.theme=localStorage.getItem("admin-ui-theme") || (matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");
  document.querySelectorAll("[data-theme-toggle]").forEach(b=>b.addEventListener("click",theme));
  function empty(parent,title,description,retry) {
    const box=node("div",null,"studio-empty"); box.setAttribute("role","status");
    box.append(node("div","▦","empty-icon"),node("h2",title),node("p",description));
    if(retry)box.append(button("重新加载",retry)); parent.replaceChildren(box);
  }
  function table(headers) {
    const wrap=node("div",null,"table-wrap"),t=node("table",null,"studio-table"),head=node("thead"),r=node("tr"),body=node("tbody");
    headers.forEach(h=>r.append(node("th",h)));head.append(r);t.append(head,body);wrap.append(t);return {wrap,body};
  }
  function badge(value) {return node("span",statuses[value] || value,"badge "+(["listed","approved"].includes(value)?"": ["draft","submitted","in_review","changes_requested"].includes(value)?"pending":"neutral"));}
  async function show(next=view) {
    view=next; const version=++generation;
    document.querySelectorAll("[data-admin-view]").forEach(b=>{ const selected=b.dataset.adminView===view; b.classList.toggle("active",selected); if(selected)b.setAttribute("aria-current","page");else b.removeAttribute("aria-current"); });
    document.getElementById("accountsView").classList.toggle("hidden",view!=="accounts");host.classList.toggle("hidden",view==="accounts");
    if(view==="accounts")return;
    host.replaceChildren();
    const header=node("header",null,"topbar"),intro=node("div"),actions=node("div",null,"topbar-actions");
    intro.append(node("p","CONTENT STUDIO","eyebrow"),node("h1",names[view]),node("p",{creative:"从灵感到应用，管理每一项官方与社区创意。",reviews:"检查稿件内容和版本，让每一次发布有据可查。",reports:"核对用户反馈，记录处理结果并在必要时停止内容传播。",categories:"为创意建立清晰、稳定的分类。",audit:"追踪内容编辑、发布与展示变更。"}[view],"page-description"));
    actions.append(button("切换主题",theme),button("刷新",()=>show()));
    if(view==="creative")actions.append(button("导入内置创意",()=>importEditor()),button("＋ 新建创意",()=>openEditor(),"button primary"));
    if(view==="categories")actions.append(button("＋ 新增分类",()=>categoryEditor(),"button primary"));
    header.append(intro,actions);host.append(header);
    const panel=node("section",null,"panel");host.append(panel);
    if(view==="creative") {
      const toolbar=node("form",null,"studio-toolbar"),search=node("input"),select=node("select");
      search.type="search";search.placeholder="搜索创意名称或 ID";search.setAttribute("aria-label","搜索创意");search.value=query;
      for(const [v,l] of [["","全部状态"],["listed","已上架"],["unpublished","未发布"],["admin_blocked","已下架"],["archived","已归档"]]){ const o=node("option",l);o.value=v;select.append(o); }
      select.value=filter;select.setAttribute("aria-label","展示状态");select.onchange=()=>{query=search.value;filter=select.value;offset=0;show();};
      toolbar.onsubmit=e=>{e.preventDefault();query=search.value;offset=0;show();};
      const submit=node("button","搜索","button ghost");submit.type="submit";toolbar.append(search,select,submit);panel.append(toolbar);
    }
    const content=node("div");panel.append(content);empty(content,"正在加载","正在读取服务端内容…");
    try {
      if(view==="reviews") {
        const cap=await api("/api/creative/capabilities");if(version!==generation)return;
        if(!cap.submissions_enabled) panel.insertBefore(node("p","新投稿暂未开放，已有稿件仍可审核。","studio-note"),content);
        const data=await request(`/reviews?offset=${offset}`);if(version!==generation || !state.token)return;
        if(!data.items?.length){empty(content,"当前没有待审稿件","用户提交后，冻结的源码版本会出现在这里。退修和拒绝不会影响已经上架的旧版本。");return;}
        const {wrap,body}=table(["稿件","作者","审核状态","操作"]);
        data.items.forEach(item=>{const row=node("tr"),title=node("td"),status=node("td"),action=node("td");title.append(node("strong",item.title),node("small",` · v${item.version_no}`));status.append(badge(item.state));action.append(button("查看稿件与差异",()=>openReview(item.id,item.revision_id),"row-button"));row.append(title,node("td",item.author_name),status,action);body.append(row);});
        content.replaceChildren(wrap);const pager=node("div",null,"studio-pagination"),prev=button("上一页",()=>{offset=Math.max(0,offset-40);show();}),next=button("下一页",()=>{offset=data.next_offset;show();});prev.disabled=offset===0;next.disabled=data.next_offset==null;pager.append(prev,next);content.append(pager);return;
      }
      if(view==="reports") {
        const [data,metrics]=await Promise.all([request(`/reports?offset=${offset}`),request("/metrics")]);if(version!==generation || !state.token)return;
        const summary=node("section",null,"metrics studio-metrics");for(const [label,value,caption] of [["待处理举报",metrics.open_reports,"需要人工核对"],["待审稿件",metrics.pending_reviews,"冻结版本"],["社区上架",metrics.community_published,"当前可见"],["收藏关系",metrics.favorites,"账号级保存"]]){const card=node("article",null,"metric");card.append(node("span",label),node("strong",String(value)),node("small",caption));summary.append(card);}panel.insertBefore(summary,content);
        if(!data.items?.length){empty(content,"当前没有举报记录","用户提交的举报会在这里显示。处理结果会通知举报人，管理员内部备注不会公开。");return;}
        const {wrap,body}=table(["创意","原因","状态","重复项","操作"]);
        data.items.forEach(item=>{const row=node("tr"),title=node("td",null,"studio-title-cell"),status=node("td"),action=node("td");title.append(node("strong",item.title),node("small",item.creative_id));status.append(badge(item.state));action.append(button("查看并处理",()=>openReport(item.id),"row-button"));row.append(title,node("td",reportReasons[item.reason] || item.reason),status,node("td",String(item.duplicate_count || 1)),action);body.append(row);});
        content.replaceChildren(wrap);const pager=node("div",null,"studio-pagination"),prev=button("上一页",()=>{offset=Math.max(0,offset-40);show();}),next=button("下一页",()=>{offset=data.next_offset;show();});prev.disabled=offset===0;next.disabled=data.next_offset==null;pager.append(prev,next);content.append(pager);return;
      }
      const data=await request(view==="categories"?"/categories":view==="audit"?"/audit":`/items?q=${encodeURIComponent(query)}&state=${encodeURIComponent(filter)}&offset=${offset}`);
      if(version!==generation || !state.token)return;
      content.replaceChildren();
      if(view==="categories") {
        if(!data.length){empty(content,"还没有分类","新增分类后，可用于创意编辑与广场筛选。");return;}
        const {wrap,body}=table(["分类名称","稳定 ID","排序","状态","操作"]);
        data.forEach(item=>{const r=node("tr"),action=node("td");action.append(button("编辑",()=>categoryEditor(item),"row-button"));r.append(node("td",item.label),node("td",item.id),node("td",item.sort_order),node("td",item.enabled?"使用中":"已停用"),action);body.append(r);});content.append(wrap);return;
      }
      if(view==="audit") {
        if(!data.items?.length){empty(content,"暂无操作记录","首次编辑或发布创意后，记录将显示在这里。");return;}
        const {wrap,body}=table(["操作","创意","操作者标识","原因","时间"]);
        data.items.forEach(item=>{const r=node("tr");r.append(node("td",item.action),node("td",item.creative_id || "—"),node("td",item.actor),node("td",item.reason || "—"),node("td",item.created_at?new Date(item.created_at*1000).toLocaleString("zh-CN"):"—"));body.append(r);});content.append(wrap);return;
      }
      if(!data.items?.length)empty(content,query || filter?"没有匹配的创意":"让第一个创意在这里诞生",query || filter?"调整关键词或展示状态，再试一次。":"新建官方创意，填写源码与适用条件，预览后再发布。");
      else {
        const {wrap,body}=table(["创意","来源","展示状态","最新稿件","操作"]);
        data.items.forEach(item=>{
          const r=node("tr"),title=node("td",null,"studio-title-cell"),visibility=node("td"),review=node("td"),action=node("td");
          title.append(node("strong",item.title),node("small",`${item.legacy_id || item.id} · v${item.version_no}`));
          visibility.append(badge(item.distribution_state));review.append(badge(item.review_state));action.append(button("编辑与版本",()=>openEditor(item.id),"row-button"));
          r.append(title,node("td",item.origin==="official"?"官方":"社区"),visibility,review,action);body.append(r);
        });content.append(wrap);
      }
      const pager=node("div",null,"studio-pagination"),prev=button("上一页",()=>{offset=Math.max(0,offset-40);show();}),more=button("下一页",()=>{offset=data.next_offset;show();});prev.disabled=offset===0;more.disabled=data.next_offset==null;pager.append(node("span",`第 ${Math.floor(offset/40)+1} 页`),prev,more);content.append(pager);
    } catch(error) {if(version===generation && state.token)empty(content,"暂时无法读取",error.message,()=>show());}
  }
  function dialog(title) {
    editor?.remove();editor=node("dialog",null,"studio-editor");const form=node("form"),head=node("div",null,"dialog-head"),heading=node("div");
    heading.append(node("p","CREATIVE STUDIO","eyebrow"),node("h2",title));head.append(heading,button("×",()=>editor.close(),"icon-button"));head.lastChild.setAttribute("aria-label","关闭编辑器");form.append(head);editor.append(form);document.body.append(editor);editor.showModal();return form;
  }
  async function openEditor(id, revisionId) {
    let item=null,categories=[];
    try { [item,categories]=await Promise.all([id?request(`/items/${encodeURIComponent(id)}${revisionId?`?revision_id=${encodeURIComponent(revisionId)}`:""}`):null,request("/categories")]); }catch(e){toast(e.message);return;}
    if(!state.token)return;
    const form=dialog(item?"编辑创意":"新建官方创意"),body=node("div",null,"studio-editor-body"),fields=node("div",null,"studio-fields"),aside=node("aside",null,"studio-preview"),controls={};
    const activeDialog=form.parentElement;
    const initial=item?.content || {};
    function field(key,label,type="input",value=initial[key]??"") {const l=node("label",label),input=node(type);input.name=key;input.value=value;controls[key]=input;l.append(input);fields.append(l);return input;}
    field("title","创意名称").maxLength=80;controls.title.required=true;
    field("summary","一句话介绍","textarea").maxLength=300;controls.summary.rows=2;
    const category=field("category_id","分类","select");categories.filter(c=>c.enabled || c.id===initial.category_id).forEach(c=>{const o=node("option",c.label);o.value=c.id;category.append(o);});category.value=initial.category_id || categories.find(c=>c.enabled)?.id || "component";
    field("description","详细说明","textarea").rows=3;
    const stack=field("ui_stack","适用 UI 技术","select");["compose","xml","mixed"].forEach(v=>{const o=node("option",{compose:"Jetpack Compose",xml:"XML Views",mixed:"Compose + XML"}[v]);o.value=v;stack.append(o);});stack.value=initial.ui_stack || "compose";
    const sdk=field("min_sdk","最低 Android SDK","input",initial.min_sdk || 24);sdk.type="number";sdk.min=21;sdk.max=100;
    field("tags","标签（逗号分隔）","input",(initial.tags || []).join(", "));
    field("dependencies","依赖（每行一项）","textarea",(initial.dependencies || []).join("\n")).rows=2;
    field("integration","接入说明","textarea").rows=3;
    field("license","许可证");field("attribution","来源与署名");
    field("references","参考链接（每行：标题 | HTTPS 地址）","textarea",(initial.references || []).map(r=>`${r.title} | ${r.url}`).join("\n")).rows=2;
    const files=node("div",null,"studio-fields");files.style.padding="0";
    const fileInputs=[];
    function addFile(file={path:"",content:""}) {const block=node("div"),pathLabel=node("label","文件相对路径"),path=node("input"),sourceLabel=node("label","源码"),source=node("textarea",null,"studio-code");path.value=file.path;path.placeholder="app/src/main/java/Example.kt";source.value=file.content;pathLabel.append(path);sourceLabel.append(source);const record={path,source,block};fileInputs.push(record);block.append(pathLabel,sourceLabel,button("移除此文件",()=>{block.remove();fileInputs.splice(fileInputs.indexOf(record),1);}));files.append(block);}
    (initial.files?.length?initial.files:[{path:"",content:""}]).forEach(addFile);fields.append(node("h3","源码文件"),files,button("＋ 添加文件",()=>addFile()));
    let coverId=initial.cover_asset_id || null;
    const cover=field("cover","封面图片（JPEG / PNG / WebP，最多 1.5 MiB）");cover.type="file";cover.accept="image/jpeg,image/png,image/webp";cover.removeAttribute("name");
    aside.append(node("h3","卡片预览"));const card=node("div",null,"studio-preview-card"),previewTitle=node("strong"),previewSummary=node("p"),art=node("div","✧","studio-preview-art");card.append(art,previewTitle,previewSummary,badge(item?.revisions?.find(r=>r.id===item.revision_id)?.state || "draft"));aside.append(card,node("p","预览用于核对标题与介绍。审核状态、展示状态和构建验证分别记录。","studio-note"));
    const updatePreview=()=>{previewTitle.textContent=controls.title.value || "为你的创意命名";previewSummary.textContent=controls.summary.value || "让别人一眼了解它的用途。";};controls.title.oninput=updatePreview;controls.summary.oninput=updatePreview;updatePreview();
    let coverPreviewGeneration=0;
    const renderCover=async blob=>{const version=++coverPreviewGeneration;const url=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(blob);});if(version!==coverPreviewGeneration || !activeDialog.open || !state.token)return;const img=node("img");img.alt="创意封面预览";img.src=url;art.replaceChildren(img);};
    if(coverId){const version=coverPreviewGeneration;fetch(`${base}/assets/${encodeURIComponent(coverId)}`,{headers:{Authorization:`Bearer ${state.token}`}}).then(response=>{if(!response.ok)throw Error("封面不可用");return response.blob();}).then(blob=>{if(version===coverPreviewGeneration)return renderCover(blob);}).catch(()=>{if(activeDialog.open && version===coverPreviewGeneration)art.textContent="封面不可用";});}
    cover.onchange=()=>{coverPreviewGeneration++;const file=cover.files[0];if(!file)return;if(file.size>1572864 || !["image/jpeg","image/png","image/webp"].includes(file.type)){error.textContent="请选择不超过 1.5 MiB 的 JPEG、PNG 或 WebP 图片";cover.value="";return;}renderCover(file).catch(()=>{art.textContent="封面不可预览";});};
    fields.append(button("移除封面",()=>{coverPreviewGeneration++;coverId=null;cover.value="";art.textContent="✧";}));
    if(item){aside.append(node("h3","版本记录"));(item.revisions || []).forEach(r=>aside.append(button(`v${r.version_no} · ${statuses[r.state] || r.state}${r.id===item.published_revision_id?" · 当前线上版本":""}`,()=>{if(dirty()){error.textContent="请先保存当前修改，再切换版本。";return;}openEditor(item.id,r.id);},"studio-note")));}
    body.append(fields,aside);form.append(body);const error=node("p","","form-error");error.setAttribute("role","alert");form.append(error);const actions=node("div",null,"dialog-actions");
    const save=button("保存草稿",()=>{},"button primary");save.type="submit";actions.append(button("取消",()=>activeDialog.close()),save);form.append(actions);
    if(item?.origin==="community"){save.disabled=true;fields.append(node("p","社区源码由作者维护，请在审核队列中查看差异、退修或批准。此处可管理上下架及推荐设置。","studio-note"));}
    const lock=busy=>form.querySelectorAll("button").forEach(b=>b.disabled=busy || (b===save && item?.origin==="community"));
    async function mutate(path,payload) {lock(true);error.textContent="";try {await request(path,{method:"POST",body:JSON.stringify(payload)});activeDialog.close();toast("创意状态已更新");show();}catch(e){error.textContent=e.message;}finally{lock(false);}}
    if(item){
      const reason=node("textarea");reason.placeholder="发布或上下架原因";reason.setAttribute("aria-label","操作原因");reason.rows=2;fields.append(reason);
      const actionPayload=()=>({expected_version:item.row_version,revision_id:item.revision_id,reason:reason.value});
      // Publish the saved snapshot, never silently discard the editor's changes.
      const publish=button("发布已保存版本",()=>{if(dirty()){error.textContent="请先保存当前修改，再发布新的已保存版本。";return;}mutate(`/items/${item.id}/publish`,actionPayload());});publish.disabled=["admin_blocked","archived"].includes(item.distribution_state);actions.prepend(publish);
      const visibility=item.distribution_state==="listed"?"unpublish":["admin_blocked","archived"].includes(item.distribution_state)?"restore":null;
      if(visibility)actions.prepend(button(visibility==="unpublish"?"下架":"恢复为未发布",()=>{if(!reason.value.trim()){error.textContent="请填写操作原因。";reason.focus();return;}mutate(`/items/${item.id}/visibility/${visibility}`,actionPayload());}));
      if(item.distribution_state!=="archived")actions.prepend(button("归档",()=>{if(!reason.value.trim()){error.textContent="请填写归档原因。";reason.focus();return;}mutate(`/items/${item.id}/visibility/archive`,actionPayload());}));
    }
    if(item){
      const placementLabel=node("label","推荐展示"),featured=node("input");featured.type="checkbox";featured.checked=item.featured;placementLabel.append(featured);aside.append(placementLabel);
      const sortLabel=node("label","展示排序"),sort=node("input");sort.type="number";sort.min=-10000;sort.max=10000;sort.value=item.sort_order;sortLabel.append(sort);aside.append(sortLabel);
      aside.append(button("更新展示设置",async()=>{
        if(dirty()){error.textContent="请先保存当前内容修改，再更新展示设置。";return;}
        if(!sort.reportValidity())return;lock(true);error.textContent="";
        try{await request(`/items/${item.id}/placement`,{method:"PATCH",body:JSON.stringify({expected_version:item.row_version,featured:featured.checked,sort_order:Number(sort.value)})});if(!activeDialog.open)return;activeDialog.close();toast("展示设置已更新");show();openEditor(item.id);}catch(e){error.textContent=e.message;}finally{lock(false);}
      }));
    }
    const snapshot=()=>JSON.stringify(Object.entries(controls).filter(([k])=>k!=="cover").map(([k,v])=>[k,v.value]).concat(fileInputs.map(f=>[f.path.value,f.source.value])));
    const clean=snapshot(),dirty=()=>snapshot()!==clean || cover.files.length>0 || coverId!==(initial.cover_asset_id || null);
    form.onsubmit=async e=>{e.preventDefault();if(!form.reportValidity())return;lock(true);error.textContent="";try{
      if(cover.files[0]){if(cover.files[0].size>1572864)throw Error("封面不能超过 1.5 MiB");const uploaded=await request("/covers",{method:"POST",body:cover.files[0],headers:{"Content-Type":cover.files[0].type}});coverId=uploaded.id;}
      const content={...initial,title:controls.title.value.trim(),summary:controls.summary.value,description:controls.description.value,category_id:category.value,ui_stack:stack.value,min_sdk:Number(sdk.value),tags:controls.tags.value.split(/[,，]/).map(s=>s.trim()).filter(Boolean),dependencies:controls.dependencies.value.split("\n").map(s=>s.trim()).filter(Boolean),integration:controls.integration.value,license:controls.license.value,attribution:controls.attribution.value,files:fileInputs.filter(f=>f.path.value || f.source.value).map(f=>({path:f.path.value.trim(),content:f.source.value})),cover_asset_id:coverId};
      content.references=controls.references.value.split("\n").map(s=>s.trim()).filter(Boolean).map(line=>{const split=line.indexOf("|");if(split<1)throw Error("参考链接格式：标题 | HTTPS 地址");return {title:line.slice(0,split).trim(),url:line.slice(split+1).trim()};});
      const saved=await request(item?`/items/${item.id}/draft`:"/items",{method:item?"PUT":"POST",body:JSON.stringify(item?{expected_version:item.row_version,content}:content)});
      if(!activeDialog.open || !state.token)return;activeDialog.close();toast("草稿已保存，尚未发布");show();openEditor(saved.id);
    }catch(e){error.textContent=e.message;}finally{lock(false);}};
  }
  async function openReview(id,revisionId) {
    let data;try{data=await request(`/reviews/${encodeURIComponent(id)}?revision_id=${encodeURIComponent(revisionId)}`);}catch(error){toast(error.message);return;}
    if(!state.token)return;
    const item=data.item,revision=item.revisions.find(r=>r.id===revisionId),form=dialog("审核社区稿件"),activeDialog=form.parentElement;
    const fields=node("div",null,"studio-fields");fields.append(node("h3",`${item.content.title} · v${revision.version_no}`),node("p",`作者：${item.author_name} · ${statuses[revision.state] || revision.state} · 尚未构建验证`));
    if(item.content.cover_asset_id){const preview=node("div","正在读取投稿封面…");fields.append(preview);fetch(`${base}/assets/${encodeURIComponent(item.content.cover_asset_id)}`,{headers:{Authorization:`Bearer ${state.token}`}}).then(r=>{if(!r.ok)throw Error("封面不可用");return r.blob();}).then(blob=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(blob);})).then(url=>{if(!activeDialog.open || !state.token)return;const img=node("img");img.alt="投稿封面";img.className="review-cover";img.src=url;preview.replaceChildren(img);}).catch(()=>{preview.textContent="封面无法读取，请刷新并核对素材后再批准";});}
    const metadata=value=>{if(!value)return "首次投稿，没有线上版本";const {files,...rest}=value;return JSON.stringify(rest,null,2);};
    function comparison(title,before,after){const section=node("details",null,"review-section");section.open=true;section.append(node("summary",title));const columns=node("div",null,"review-columns");for(const [label,body] of [["当前线上版本",before],["本次提交版本",after]]){const column=node("div");column.append(node("strong",label),node("pre",body,"studio-code"));columns.append(column);}section.append(columns);fields.append(section);}
    comparison("信息与适用条件对比",metadata(data.published_content),metadata(item.content));
    const previous=new Map((data.published_content?.files || []).map(f=>[f.path,f.content])),current=new Map((item.content.files || []).map(f=>[f.path,f.content]));
    for(const path of new Set([...previous.keys(),...current.keys()]))comparison(`${path} · ${!previous.has(path)?"新增":!current.has(path)?"删除":previous.get(path)===current.get(path)?"未变更":"已修改"}`,previous.get(path) ?? "（无此文件）",current.get(path) ?? "（已删除）");
    if(data.reviews.length){const history=node("details");history.append(node("summary","历史审核记录"));data.reviews.forEach(r=>history.append(node("p",`${statuses[r.decision] || r.decision}：${r.feedback || "无公开意见"}；内部备注：${r.private_note || "无"}`)));fields.append(history);}
    const feedbackLabel=node("label","作者可见的审核意见"),feedback=node("textarea");feedback.rows=3;feedback.maxLength=2000;feedbackLabel.append(feedback);
    const privateLabel=node("label","管理员内部备注（不向作者公开）"),privateNote=node("textarea");privateNote.rows=2;privateNote.maxLength=2000;privateLabel.append(privateNote);fields.append(feedbackLabel,privateLabel);form.append(fields);
    const error=node("p","","form-error");error.setAttribute("role","alert");const actions=node("div",null,"dialog-actions");form.append(error,actions);
    const send=async decision=>{if(["changes_requested","reject"].includes(decision) && !feedback.value.trim()){error.textContent="请填写作者可见的修改意见或拒绝原因。";feedback.focus();return;}actions.querySelectorAll("button").forEach(b=>b.disabled=true);try{await request(`/reviews/${id}`,{method:"POST",body:JSON.stringify({expected_version:item.row_version,revision_id:revision.id,content_hash:revision.content_hash,decision,feedback:feedback.value,private_note:privateNote.value})});activeDialog.close();toast("审核结果已保存并通知作者");show("reviews");}catch(e){error.textContent=e.message;}finally{actions.querySelectorAll("button").forEach(b=>b.disabled=false);}};
    actions.append(button("关闭",()=>activeDialog.close()));if(["submitted","in_review"].includes(revision.state)){if(revision.state==="submitted")actions.append(button("开始审核",()=>send("claim")));actions.append(button("退回修改",()=>send("changes_requested")),button("拒绝稿件",()=>send("reject")),button("批准并发布",()=>send("approve"),"button primary"));}
  }
  async function openReport(id) {
    let report;try{report=await request(`/reports/${encodeURIComponent(id)}`);}catch(error){toast(error.message);return;}
    if(!state.token)return;
    const form=dialog("处理内容举报"),activeDialog=form.parentElement,fields=node("div",null,"studio-fields");
    fields.append(node("h3",report.title),node("p",`${reportReasons[report.reason] || report.reason} · ${statuses[report.state] || report.state}`));
    const detail=node("section",null,"report-detail");detail.append(node("strong","用户说明"),node("p",report.details),node("small",`举报版本：${report.revision_id} · 举报人标识：${report.reporter_user_id}`));fields.append(detail);
    if(report.resolution)fields.append(node("p",`处理结果：${report.resolution}`,"studio-note"));
    const resolutionLabel=node("label","举报人可见的处理结果"),resolution=node("textarea");resolution.rows=3;resolution.maxLength=2000;resolutionLabel.append(resolution);
    const privateLabel=node("label","管理员内部备注（不向举报人或作者公开）"),privateNote=node("textarea");privateNote.rows=2;privateNote.maxLength=2000;privateLabel.append(privateNote);fields.append(resolutionLabel,privateLabel);form.append(fields);
    const error=node("p","","form-error");error.setAttribute("role","alert");const actions=node("div",null,"dialog-actions");form.append(error,actions);
    const send=async decision=>{if(decision!=="claim" && !resolution.value.trim()){error.textContent="请填写举报人可见的处理结果。";resolution.focus();return;}actions.querySelectorAll("button").forEach(b=>b.disabled=true);try{await request(`/reports/${id}`,{method:"POST",body:JSON.stringify({expected_version:report.row_version,expected_creative_version:decision==="block"?report.creative_row_version:null,decision,resolution:resolution.value,private_note:privateNote.value})});activeDialog.close();toast(decision==="block"?"举报已处理，创意已停止公开":"举报处理结果已保存");show("reports");}catch(e){error.textContent=e.message;}finally{actions.querySelectorAll("button").forEach(b=>b.disabled=false);}};
    actions.append(button("关闭",()=>activeDialog.close()));if(["open","reviewing"].includes(report.state)){if(report.state==="open")actions.append(button("开始处理",()=>send("claim")));actions.append(button("驳回举报",()=>send("dismiss")),button("确认已处理",()=>send("resolve"),"button primary"));if(report.distribution_state==="listed")actions.append(button("处理并下架",()=>send("block"),"button danger"));}
  }
  function importEditor() {
    const form=dialog("导入内置创意"),activeDialog=form.parentElement,fields=node("div",null,"studio-fields");
    fields.append(node("p","将内置示例复制到服务端目录。已导入的条目会跳过，后台编辑和下架状态会保留。"));
    const label=node("label","同时上架本次新增条目"),publish=node("input");publish.type="checkbox";label.append(publish);fields.append(label,node("p","默认仅导入草稿；批量导入可能需要一分钟，请等待结果。","studio-note"));
    const error=node("p","","form-error"),actions=node("div",null,"dialog-actions"),submit=button("开始导入",()=>{},"button primary");submit.type="submit";actions.append(submit);form.append(fields,error,actions);
    form.onsubmit=async event=>{event.preventDefault();submit.disabled=true;error.textContent="正在导入…";try{const result=await request("/import-builtins",{method:"POST",body:JSON.stringify({publish:publish.checked})});if(!activeDialog.open || !state.token)return;error.textContent=`完成：新增 ${result.created}，跳过 ${result.skipped}，上架 ${result.published}。`;submit.remove();actions.append(button("完成",()=>{activeDialog.close();offset=0;show("creative");}));}catch(e){error.textContent=e.message;submit.disabled=false;}};
  }
  function categoryEditor(item) {
    const form=dialog(item?"编辑分类":"新增分类"),fields=node("div",null,"studio-fields"),inputs={};
    for(const [key,label,value] of [["id","稳定 ID",item?.id || ""],["label","分类名称",item?.label || ""],["sort_order","排序",item?.sort_order || 0]]){const l=node("label",label),i=node("input");i.value=value;i.required=true;if(key==="sort_order"){i.type="number";i.min=-10000;i.max=10000;}if(key==="id"){i.pattern="[a-z][a-z0-9_-]{0,47}";i.readOnly=!!item;}inputs[key]=i;l.append(i);fields.append(l);}
    const enabled=node("input");enabled.type="checkbox";enabled.checked=item?.enabled ?? true;const label=node("label","启用分类");label.append(enabled);fields.append(label);form.append(fields);const error=node("p","","form-error");form.append(error);const actions=node("div",null,"dialog-actions"),save=button("保存",()=>{},"button primary");save.type="submit";actions.append(save);form.append(actions);
    form.onsubmit=async e=>{e.preventDefault();save.disabled=true;try{await request("/categories",{method:"PUT",body:JSON.stringify({id:inputs.id.value,label:inputs.label.value,sort_order:Number(inputs.sort_order.value),enabled:enabled.checked,expected_version:item?.row_version ?? null})});editor.close();show();}catch(e){error.textContent=e.message;}finally{save.disabled=false;}};
  }
  document.querySelectorAll("[data-admin-view]").forEach(b=>b.addEventListener("click",()=>{offset=0;show(b.dataset.adminView);}));
  // Clear private editor contents and invalidate late reads when the account gate closes.
  new MutationObserver(()=>{if(document.getElementById("appShell").classList.contains("hidden")){generation++;editor?.remove();editor=null;host.replaceChildren();view="accounts";document.getElementById("accountsView").classList.remove("hidden");host.classList.add("hidden");document.querySelectorAll("[data-admin-view]").forEach(b=>{b.classList.toggle("active",b.dataset.adminView==="accounts");});}}).observe(document.getElementById("appShell"),{attributes:true,attributeFilter:["class"]});
})();
