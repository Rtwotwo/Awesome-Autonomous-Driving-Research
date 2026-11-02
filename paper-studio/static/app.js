const $ = (id) => document.getElementById(id);
const escapeHTML = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {token:'', active:null, source:null, article:null, busy:false, timer:null, crop:null, editFigure:null, dirty:false, metadataDirty:false, figuresDirty:false, reviewDirty:false, configured:false, desktop:false};
state.feedSelected=new Set();
state.categories=null;
state.reviewEdits=new Set();
state.preparingDelivery=false;
state.recovery=null;state.recoveryOptions={};state.lastTask=null;state.recoveryVersion='';
state.xhs={note:null,receipt:null,dirty:false,working:false,connectionWorking:false,connection:null,slide:0,stale:false,checked:false};
let toastTimer;
let xhsConnectionTimer, xhsConnectionPollUntil=0;
function toast(message, error=false) { $('toast').textContent=message; $('toast').classList.toggle('error',error); $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,error?9000:4500); }
function guarded(fn) { return async (...args)=>{try{await fn(...args);}catch(e){toast(e.message,true);}}; }
async function request(path, body, raw=false) {
  const options = path==='/api/desktop/action'?{}:{signal:AbortSignal.timeout(path.startsWith('/api/xhs/')||path==='/api/models/check'?150000:60000)};
  if(body!==undefined){options.method='POST';options.headers={'X-Studio-Token':state.token,'Content-Type':raw?'application/octet-stream':'application/json'};options.body=raw?body:JSON.stringify(body);}
  const response=await fetch(path,options);
  const result=await response.json();
  if(!response.ok) throw new Error(result.error||'请求未完成');
  return result;
}
const endpoint = (action) => `/api/jobs/${state.active}/${action}`;
const asset = (name) => endpoint('asset')+'?name='+encodeURIComponent(name)+'&v='+Date.now();
function settings() { return {base_url:$('baseUrl').value.trim(),model:$('modelId').value.trim(),api_key:state.desktop?'':$('apiKey').value,vision:$('vision').checked,output_tokens:Number($('outputTokens').value)||0,thinking:$('thinkingMode').value,response_mode:$('responseMode').value,request_timeout:Number($('requestTimeout').value)||600,json_mode:$('jsonMode').value,auth_mode:$('authMode').value,proxy_mode:$('proxyMode').value,context_window:Number($('contextWindow').value)||0,max_output_tokens:Number($('modelMaxOutput').value)||0,vision_capability:$('visionCapability').value===''?null:$('visionCapability').value==='true',tool_calling:$('toolCalling').value===''?null:$('toolCalling').value==='true'}; }
async function refreshModels(){
  const s=await request('/api/session');state.token=s.token;state.desktop=Boolean(s.desktop);state.configured=s.configured;state.profileId=s.active_profile||'';
  $('openData').hidden=!state.desktop;$('rememberRow').hidden=!state.desktop;
  $('modelProfiles').innerHTML='<option value="">新增模型配置</option>'+(s.profiles||[]).map(p=>`<option value="${escapeHTML(p.id)}">${escapeHTML(p.name||p.model)}</option>`).join('');
  $('modelProfiles').value=state.profileId;const selected=(s.profiles||[]).find(p=>p.id===state.profileId);
  $('profileName').value=selected?.name||'';$('baseUrl').value=s.base_url;$('modelId').value=s.model;
  $('outputTokens').value=s.output_tokens||'';$('thinkingMode').value=s.thinking||'high';
  $('responseMode').value=s.response_mode||'stream';$('requestTimeout').value=s.request_timeout||600;$('jsonMode').value=s.json_mode||'auto';
  $('authMode').value=s.auth_mode||'bearer';$('proxyMode').value=s.proxy_mode||'system';
  $('contextWindow').value=s.context_window||'';$('modelMaxOutput').value=s.max_output_tokens||'';
  $('visionCapability').value=typeof s.vision_capability==='boolean'?String(s.vision_capability):'';
  $('toolCalling').value=typeof s.tool_calling==='boolean'?String(s.tool_calling):'';
  $('vision').disabled=s.vision_capability===false;
  if(s.vision_capability===false||s.model?.toLowerCase()==='deepseek-v4-flash-0731')$('vision').checked=false;
  $('rememberModels').checked=s.profiles?.length?Boolean(s.remember_models):true;
  $('apiKey').value='';$('apiKey').placeholder=s.configured?'已设置（不回显）；留空沿用此配置的密钥':'填写服务商提供的密钥';
  $('deleteModel').disabled=!state.profileId;$('modelBadge').textContent=s.model||'尚未配置模型';
  if(s.model_warning)toast(s.model_warning,true);
}
function newModel(){state.profileId='';$('modelProfiles').value='';$('profileName').value='';$('baseUrl').value='';$('modelId').value='';$('apiKey').value='';$('apiKey').placeholder='新配置需要独立填写密钥';$('outputTokens').value='';$('thinkingMode').value='high';$('responseMode').value='stream';$('requestTimeout').value=600;$('jsonMode').value='auto';$('vision').checked=false;$('vision').disabled=false;$('authMode').value='bearer';$('proxyMode').value='system';['contextWindow','modelMaxOutput','visionCapability','toolCalling'].forEach(id=>$(id).value='');$('modelConnectionState').textContent='';$('deleteModel').disabled=true;$('profileName').focus();}
function options() { return {...settings(),style:$('style').value,auto_figures:$('autoFigures').checked,length:Number($('length').value),brief:$('brief').value,figure_ids:state.source?.figures.filter(f=>f.selected).map(f=>f.id)||[]}; }
function showTab(name) {if(name!=='xhs')stopXhsConnectionPolling();if(name==='review'&&state.active)guarded(refreshWechatReview)();if(name==='xhs'&&state.active){state.xhs.checked=false;updateXhsButtons();guarded(refreshXhs)();guarded(refreshXhsConnection)();} document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===name);b.setAttribute('aria-current',b.dataset.tab===name?'step':'false');});document.querySelectorAll('.tab-panel').forEach(p=>p.hidden=p.id!=='tab-'+name); }
function busy(value) {state.busy=value;updateXhsButtons();$('generateAutoDraft').disabled=value;$('editCategories').disabled=value;document.querySelectorAll('#tab-review input,#tab-review select,#tab-review textarea,#tab-review button').forEach(e=>e.disabled=value);if(!value)updateReviewButtons();document.querySelectorAll('#draftEditor input,#draftEditor textarea,#draftEditor button,#figureGallery input,#figureGallery textarea,#figureGallery button,#paperInfoPanel input,#paperInfoPanel textarea,#paperInfoPanel button,#repairCrops,#projectMediaPanel button,#projectVideos input,#materialsPanel button,#materialsPanel input,#materialsPanel select').forEach(el=>el.disabled=value);$('generate').disabled=value||!state.source?.can_generate;$('saveDraft').disabled=value||!state.article;$('saveFigures').disabled=value;$('saveCrop').disabled=value||!state.crop;['exportZip','exportMd','copyHtml','skillFormat'].forEach(id=>$(id).disabled=value||!state.article);}
function downloadText(text, filename, type='text/plain;charset=utf-8') {const url=URL.createObjectURL(new Blob([text],{type}));const a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}
function approveNavigation() {if(state.preparingDelivery||state.xhs.working){toast('正在保存并启动任务，请稍候。');return false;}return !(state.dirty||state.metadataDirty||state.figuresDirty||state.reviewDirty||state.xhs.dirty)||confirm('还有未保存的修改，离开会丢失这些修改。确定离开吗？');}

async function refreshHistory() {
  if(!state.categories)state.categories=await request('/api/categories');
  state.jobs=await request('/api/jobs');renderLibrary();renderPaperCategories();
}
function renderLibrary(){
  const search=$('librarySearch').value.trim().toLowerCase();
  const scope=(state.jobs||[]).filter(j=>Boolean(j.trashed)===Boolean(state.showTrash));
  renderCategoryFilter('libraryCategory',scope);
  const category=$('libraryCategory').value;
  const jobs=scope.filter(j=>matchesCategory(j,category)&&(!search||((j.display_name||'')+' '+j.title).toLowerCase().includes(search)));
  $('libraryMatchCount').textContent=`显示 ${jobs.length} / ${scope.length} 篇`;
  $('jobCount').textContent=(state.jobs||[]).filter(j=>!j.trashed).length;
  $('showTrash').classList.toggle('active',Boolean(state.showTrash));
  $('history').innerHTML=jobs.length?jobs.map(j=>`<div class="library-entry"><button class="history-item ${j.id===state.active?'active':''}" ${j.trashed?'disabled':''} data-job="${j.id}" title="${escapeHTML(j.title)}"><strong>${j.pinned?'★ ':''}${escapeHTML(j.display_name||j.title)}</strong><span class="history-topics">${topicChips(j.topics,2)}</span><small><span class="library-status ${['ready','running','queued','error'].includes(j.status)?j.status:'ready'}">${escapeHTML({ready:'已保存',running:'处理中',queued:'排队中',error:'待处理'}[j.status]||j.status)}</span><span>${new Date(j.created_at*1000).toLocaleDateString('zh-CN')}</span></small></button>${j.trashed?`<button class="text-button" data-restore="${j.id}">恢复作品</button>`:''}</div>`).join(''):'<p class="muted">'+(state.showTrash?'回收站为空。':'没有匹配的作品。')+'</p>';
  const current=state.jobs?.find(j=>j.id===state.active);$('pinJob').textContent=current?.pinned?'取消置顶':'置顶';
}
function matchesCategory(item,key){return !key||(key==='unclassified'?!(item.topics||[]).length:(item.topics||[]).includes(key));}
function topicChips(topics=[],limit=99){
  const chosen=(state.categories||[]).filter(c=>topics.includes(c.id));
  if(!chosen.length)return '<span class="topic-chip is-unclassified">待分类</span>';
  return chosen.slice(0,limit).map(c=>`<span class="topic-chip">${escapeHTML(c.label)}</span>`).join('')+(chosen.length>limit?`<span class="topic-chip" title="${escapeHTML(chosen.map(c=>c.label).join(' · '))}">+${chosen.length-limit}</span>`:'');
}
function renderCategoryFilter(id,items){
  const el=$(id),selected=el.value;
  const choices=[{id:'',label:'全部方向'},...(state.categories||[]),{id:'unclassified',label:'待分类'}];
  const html=choices.map(c=>`<option value="${c.id}">${escapeHTML(c.label)} · ${items.filter(i=>matchesCategory(i,c.id)).length}</option>`).join('');
  if(el.innerHTML!==html){el.innerHTML=html;el.value=selected;}
}
function renderPaperCategories(){
  const job=state.jobs?.find(j=>j.id===state.active);
  $('paperCategories').innerHTML=topicChips(job?.topics||[]);
  $('categoryMode').textContent=job?.category_mode==='manual'?'手动分类':'自动建议 · 可调整';
}
$('librarySearch').oninput=renderLibrary;
$('libraryCategory').onchange=renderLibrary;
$('editCategories').onclick=guarded(async()=>{
  if(state.busy)throw new Error('请等待当前任务完成后调整分类。');
  await refreshHistory();const job=state.jobs.find(j=>j.id===state.active);if(!job)return;
  state.categoryJob=job.id;$('categoryPaperName').textContent=job.title;
  $('categoryChoices').innerHTML='<legend>选择研究方向</legend>'+state.categories.map(c=>`<label class="category-choice"><input type="checkbox" data-category="${c.id}" ${(job.topics||[]).includes(c.id)?'checked':''}><span><b>${escapeHTML(c.label)}</b><small>${escapeHTML((job.category_evidence?.[c.id]||[]).join(' · '))||'可手动添加'}</small></span></label>`).join('');
  $('categoryDialog').showModal();
});
async function saveCategories(override){
  $('saveCategories').disabled=$('resetCategories').disabled=true;
  try{await request(`/api/jobs/${state.categoryJob}/library`,{category_override:override});$('categoryDialog').close();await refreshHistory();toast(override===null?'已恢复自动分类。':'论文分类已保存。');}
  finally{$('saveCategories').disabled=$('resetCategories').disabled=false;}
}
$('saveCategories').onclick=guarded(()=>saveCategories([...document.querySelectorAll('[data-category]:checked')].map(e=>e.dataset.category)));
$('resetCategories').onclick=guarded(()=>saveCategories(null));
$('showLibrary').onclick=()=>{state.showTrash=false;renderLibrary();};
$('showTrash').onclick=()=>{state.showTrash=true;renderLibrary();};
$('history').addEventListener('click',guarded(async e=>{const b=e.target.closest('[data-restore]');if(b){await request(`/api/jobs/${b.dataset.restore}/library`,{trashed:false});await refreshHistory();toast('作品已恢复。');}}));
$('renameJob').onclick=guarded(async()=>{const j=state.jobs.find(j=>j.id===state.active);const name=prompt('作品名称（不改动论文原始标题；留空恢复原题）',j.display_name||j.title);if(name!==null){await request(endpoint('library'),{display_name:name});await refreshHistory();}});
$('pinJob').onclick=guarded(async()=>{const j=state.jobs.find(j=>j.id===state.active);await request(endpoint('library'),{pinned:!j.pinned});await refreshHistory();});
$('trashJob').onclick=guarded(async()=>{if(state.busy)throw new Error('请等待当前任务完成。');if(!approveNavigation()||!confirm('移入回收站？原文、草稿和素材保留，可随时恢复。'))return;await request(endpoint('library'),{trashed:true});state.dirty=state.metadataDirty=state.figuresDirty=state.reviewDirty=false;$('newPaper').click();toast('已移入回收站，可从侧边栏恢复。');});
$('refreshIdentity').onclick=guarded(async()=>{await savePaperInfo();await saveDraft(false);await saveFigures(false);await request(endpoint('refresh-identity'),{});await watchJob(state.active);});
async function openJob(id) {
  state.recovery=null;state.recoveryOptions={};state.lastTask=null;state.recoveryVersion='';$('generationNotice').hidden=true;
  clearTimeout(state.timer);state.reviewEdits.clear();state.writeThemeDirty=false;state.active=id;state.article=null;state.source=null;state.dirty=false;state.metadataDirty=false;state.figuresDirty=false;state.reviewDirty=false;state.wechatReview=null;
  showTab('write');resetDeliveryView();resetXhsView();renderEmpty();busy(true);
  $('workspace').hidden=true;$('welcome').hidden=false;
  await refreshHistory(); await watchJob(id);
}
async function watchJob(id) {
  if(id!==state.active) return;
  const task=await request(`/api/jobs/${id}/status`);
  if(id!==state.active) return;
  const running=['running','queued'].includes(task.status);busy(running);
  state.lastTask=task;
  $('taskBanner').hidden=!running;
  const operation=String(task.operation||''),automatic=operation.startsWith('wechat-auto-');
  $('cancelTask').hidden=operation.startsWith('wechat')&&!automatic;
  $('taskTitle').textContent=operation.startsWith('xhs')?(operation.includes('publish')?'正在发布小红书笔记':'正在设计小红书笔记'):automatic?(operation.endsWith('submit')?'正在自动整理与投递':'正在整理投递稿'):operation.startsWith('wechat')?'正在提交与核对公众号草稿':operation==='generate'?(task.auto_draft?'正在写作与自动投递':'正在研读与写作'):operation==='skill'?'正在排版':'正在整理论文材料';$('taskMessage').textContent=task.message;
  if(automatic&&running){$('deliveryStatus').textContent='正在处理';$('deliveryStatus').className='status-chip is-running';$('deliveryActionHint').textContent=task.message||'正在整理封面与检查投递稿…';$('autoSubmitWechat').setAttribute('aria-busy','true');}

  if(running){if(!state.source)await loadDocument(id,true);await loadGenerationProgress(id,task);if(id===state.active)state.timer=setTimeout(()=>guarded(()=>watchJob(id))(),2000);return;}
  if(task.operation==='generate'&&task.auto_draft&&task.status==='ready'){state.reviewDirty=false;state.reviewEdits.clear();state.writeThemeDirty=false;resetHumanChecks();}
  if(task.status==='error') toast(task.message,true);
  await loadDocument(id);
  if(id!==state.active)return;
  await refreshHistory();
  if(state.xhs.note&&!task.operation?.startsWith('xhs'))await refreshXhs();
  if(id!==state.active)return;
  if(task.operation?.startsWith('xhs')){await refreshXhs(true);if(id!==state.active)return;showTab('xhs');if(task.status==='ready')toast(task.operation.includes('publish')?'发布请求已处理，请查看小红书回执。':'图文笔记已设计，可以编辑、预览或发布。');}
  if(task.operation?.startsWith('wechat')){await refreshWechatReview();$('autoSubmitWechat').removeAttribute('aria-busy');}
  if(task.operation==='generate'&&task.auto_draft&&state.article){showTab('review');if(task.status==='ready')toast(task.message||'写作与投递流程已完成，请查看草稿回执。');}
  if(automatic&&task.status==='ready'){showTab('review');toast(operation.endsWith('submit')?'已完成处理，请查看投递回执。':'投递稿已整理，检查结果与预览已更新。');}
  if(task.operation==='direct-draft'&&task.status==='ready'){showTab('review');toast(task.message||'已处理，查看公众号草稿回执。');}
  if(task.operation==='skill'&&task.status==='ready') {
    $('theme').value='baoyu';renderPreview();
    state.reviewDirty=false;await refreshWechatReview();
    toast('宝玉排版已应用。前往“自动投递”，即可整理并上传正文、图片与封面。');
  }
}
async function loadDocument(id, running=false) {
  let source;try{source=await request(`/api/jobs/${id}/source`);}catch{return;}
  if(id!==state.active)return;state.source=source;
  $('welcome').hidden=true;$('workspace').hidden=false;
  $('paperTitle').textContent=source.title;
  $('sourceMeta').textContent=[source.arxiv_id?'arXiv '+source.arxiv_id:'本地 PDF',`${source.pages.length} 页`,`${source.figures.length} 张候选配图`,`${source.text_chars.toLocaleString()} 字符原文`].join('  /  ');
  $('openPdf').href=asset('source.pdf');
  const warnings=[...source.warnings,source.metadata_note].filter(Boolean);$('sourceWarnings').hidden=!warnings.length;$('sourceWarnings').textContent=warnings.join(' ');
  $('pageSelect').innerHTML=source.pages.map(p=>`<option value="${p.number}">第 ${p.number} 页</option>`).join('');showPage();renderFigures();await renderProjectMedia();if(id!==state.active)return;await renderMaterials();if(id!==state.active)return;
  try{const article=await request(`/api/jobs/${id}/article`);if(id!==state.active)return;state.article=article;}catch{if(id!==state.active)return;state.article=null;}
  if(state.article){try{const review=await request(`/api/jobs/${id}/wechat`);if(id!==state.active)return;$('theme').value=review.options.theme||'ink';}catch{/* A delivery-settings error must not hide a saved article. */}}
  renderPaperInfo();if(state.article){renderEditor();await renderAudit();renderPreview();}else{renderEmpty();}
  await loadGenerationProgress(id,state.lastTask, true);
  updateWorkspaceProgress();busy(running);
}
async function loadGenerationProgress(id, task, restoreOptions=false){
  if(id!==state.active)return;
  let data;try{data=await request(`/api/jobs/${id}/generation-draft`);}catch{return;}
  if(id!==state.active)return;
  state.recovery=data.recovery;
  state.recoveryOptions=Object.fromEntries(Object.entries(data.options||{}).filter(([key])=>['style','length','brief','auto_figures','vision','figure_ids'].includes(key)));
  if(restoreOptions&&!state.dirty){const o=data.options||{};for(const key of ['style','length','brief'])if(o[key]!==undefined)$(key).value=o[key];if(typeof o.auto_figures==='boolean')$('autoFigures').checked=o.auto_figures;if(typeof o.vision==='boolean')$('vision').checked=o.vision;if(typeof task?.auto_draft==='boolean'){$('generateAutoDraft').checked=task.auto_draft;$('generateAutoDraft').onchange();}}
  const recovery=state.recovery,running=['running','queued'].includes(task?.status),failed=task?.status==='error';
  const generation=['generate','direct-draft'].includes(task?.operation);
  const deliveryPending=Boolean(!recovery&&state.article&&failed&&(task?.operation==='wechat-auto-submit'||generation&&task?.auto_draft&&data.completed_article));
  state.deliveryPending=deliveryPending;
  $('generationNotice').hidden=!deliveryPending&&!recovery&&!(generation&&(running||failed));
  $('generationNotice').classList.toggle('is-warning',failed);
  $('generationNoticeTitle').textContent=deliveryPending?'文章已完成，待继续投递':recovery?.complete?'成稿已生成，可以恢复保存与投递':recovery?(running?'初稿已保存，正在继续处理':'初稿已保留，可以接着完成'):failed?'任务暂停，已完成的进度保留':'正在研读与写作';
  const steps=recovery?`${recovery.stage} · 已完成 ${recovery.completed_units}/${recovery.total_units} 个复核单元。`:'';
  $('generationNoticeText').textContent=[steps,task?.message||'',deliveryPending?'继续只处理已保存的成稿，不重复写作。超过单篇容量时自动分篇，保留完整正文与配图。':recovery?(recovery.complete?'已生成的内容保留在本机，恢复时不会再次请求写作模型。':'下方为只读待复核稿；全部阶段完成后才会自动投递。'):''].filter(Boolean).join(' ');
  const elapsed=task?.started_at?Math.max(0,Math.floor((task.finished_at||Date.now()/1000)-task.started_at)):Math.floor(recovery?.elapsed_seconds||0);
  $('generationElapsed').textContent=elapsed?`本次已用 ${Math.floor(elapsed/60)} 分 ${elapsed%60} 秒 · 已完成阶段在本机保存`:'';
  $('pendingPreview').hidden=!recovery;if(recovery)$('pendingPreview').href=`/api/jobs/${id}/generation-preview`;
  $('resumeGeneration').hidden=running||!recovery&&!deliveryPending;$('resumeGeneration').disabled=running;
  $('resumeGeneration').textContent=deliveryPending?'继续投递现有成稿':$('generateAutoDraft').checked?'继续完成并投递草稿':'继续完成文章';
  if(recovery&&!state.article){
    const version=JSON.stringify([id,recovery.stage,recovery.completed_units,recovery.total_units]);
    if(version!==state.recoveryVersion||restoreOptions){
      const a=recovery.article;
      $('draftEditor').innerHTML=`<div class="recovered-draft"><span class="pending-label">${recovery.complete?'成稿待恢复保存 · 只读':'已保存初稿 · 待复核 · 只读'}</span><h2>${escapeHTML(a.title)}</h2><p>${escapeHTML(a.lead||a.digest||'')}</p>${a.sections.map(s=>`<section><h3>${escapeHTML(s.heading)}</h3>${s.paragraphs.map(p=>`<p>${escapeHTML(p.text)}</p>`).join('')}</section>`).join('')}</div>`;
      $('draftStats').textContent='初稿已保存 · 复核 '+recovery.completed_units+'/'+recovery.total_units;
      $('preview').src=`/api/jobs/${id}/generation-preview?v=${Date.now()}`;$('previewEmpty').hidden=true;
      state.recoveryVersion=version;
    }
  }
}
$('resumeGeneration').onclick=guarded(async()=>{if(state.deliveryPending){showTab('review');await runAutomaticDelivery(true);}else await startGeneration(true);});
function updateWorkspaceProgress(){
  document.querySelector('[data-tab=write]').classList.toggle('is-complete',Boolean(state.article));
  document.querySelector('[data-tab=figures]').classList.toggle('is-complete',Boolean(state.source?.figures?.length));
  document.querySelector('[data-tab=evidence]').classList.toggle('is-complete',Boolean(state.article));
}
function renderPaperInfo(){
  const info=state.source.publication_info||state.source.paper_info||{title:state.source.title,authors:state.source.authors,paper_url:state.source.source_url};
  document.querySelectorAll('[data-paper-info]').forEach(el=>{const value=info[el.dataset.paperInfo];el.value=Array.isArray(value)?value.join('\n'):value||'';});
  state.metadataDirty=false;$('savePaperInfo').textContent='保存论文资料';
}
async function savePaperInfo(showToast=false){
  if(!state.metadataDirty)return;
  const info={};document.querySelectorAll('[data-paper-info]').forEach(el=>{const key=el.dataset.paperInfo;info[key]=['authors','affiliations'].includes(key)?el.value.split('\n').map(v=>v.trim()).filter(Boolean):el.value.trim();});
  info.verified_on=new Date().toLocaleDateString('en-CA');
  state.source.paper_info=await request(endpoint('paper-info'),info);state.source.publication_info=state.source.paper_info;
  if(info.title){state.source.title=info.title;$('paperTitle').textContent=info.title;}await refreshHistory();
  state.metadataDirty=false;$('savePaperInfo').textContent='已保存';renderPreview();if(state.article)await renderAudit();if(showToast)toast('论文信息卡已更新。');
}
function tableEditor(t,i,j){return `<div class="draft-result-table"><label>图表位置（跟在本章第几段后）<input type="number" min="1" data-table-position="${i}:${j}" value="${t.after_paragraph||state.article.sections[i].paragraphs.length||1}"></label><label>结果摘要标题<input data-table-field="${i}:${j}:title" value="${escapeHTML(t.title||'')}"></label><label>表头（用 Tab 分隔各列）<input data-table-columns="${i}:${j}" value="${escapeHTML(t.columns.join('\t'))}"></label><label>数据（每行一项，用 Tab 分隔各列；可从表格粘贴）<textarea data-table-rows="${i}:${j}">${escapeHTML(t.rows.map(r=>r.join('\t')).join('\n'))}</textarea></label><label>来源表号<input data-table-field="${i}:${j}:source_label" value="${escapeHTML(t.source_label||'')}"></label><label>评测条件与单位<input data-table-field="${i}:${j}:note" value="${escapeHTML(t.note||'')}"></label></div>`;}
function renderEmpty(){
  $('draftEditor').innerHTML='<div class="draft-empty"><span>Aa</span><h3>先读懂，再写好。</h3><p>确认配图后生成文章，或导出完整提示词，<br>用自己的模型写作，再导入成稿 JSON。</p><button class="secondary" data-configure>配置写作模型</button></div>';
  $('draftStats').textContent='等待成稿';$('previewEmpty').hidden=false;$('preview').removeAttribute('src');$('auditContent').innerHTML='<p class="muted">成稿后显示证据卡片。</p>';$('issueCount').textContent='';
}
function renderPreview(){if(state.article){$('preview').src=endpoint('preview')+'?theme='+$('theme').value+'&v='+Date.now();$('previewEmpty').hidden=true;}}
function renderEditor(){
  const a=state.article;
  $('draftEditor').innerHTML=`<label>推文主标题<textarea class="draft-title" data-field="title">${escapeHTML(a.title)}</textarea></label>${a.title_options?.length?`<div class="title-options">${a.title_options.map((t,i)=>`<button data-title-option="${i}">${escapeHTML(t)}</button>`).join('')}</div>`:''}<label>摘要<textarea data-field="digest">${escapeHTML(a.digest||'')}</textarea></label><label>导语<textarea data-field="lead">${escapeHTML(a.lead||'')}</textarea></label>`+
    a.sections.map((s,i)=>`<section class="draft-section"><p class="section-kind">${String(i+1).padStart(2,'0')} / ${escapeHTML(s.kind||'SECTION')}</p><input aria-label="章节标题" data-section-title="${i}" value="${escapeHTML(s.heading)}">${s.paragraphs.map((p,j)=>`<textarea aria-label="第 ${i+1} 章第 ${j+1} 段" data-paragraph="${i}:${j}">${escapeHTML(p.text)}</textarea><div class="source-ref">原文页码：${escapeHTML((p.pages||[]).join(', ')||'待补充')}</div>`).join('')}${(s.tables||[]).map((t,j)=>tableEditor(t,i,j)).join('')}${(s.figures||[]).map((f,j)=>`<div class="draft-figure-note">原图 ${escapeHTML(f.id)}<label>图表位置（跟在本章第几段后）<input type="number" min="1" data-fig-position="${i}:${j}" value="${f.after_paragraph||s.paragraphs.length||1}"></label><label>中文图注<input data-fig-caption="${i}:${j}" value="${escapeHTML(f.caption||'')}"></label><label>读图说明<textarea data-fig-explanation="${i}:${j}">${escapeHTML(f.explanation||'')}</textarea></label></div>`).join('')}</section>`).join('')+
    `<label>结尾要点（每行一条）<textarea data-field="takeaways">${escapeHTML((a.takeaways||[]).join('\n'))}</textarea></label>`;
  $('draftStats').textContent=a.sections.reduce((n,s)=>n+s.paragraphs.reduce((m,p)=>m+p.text.length,0),0).toLocaleString()+' 字';
}
function collectDraft(){
  if(!state.article)return null;
  const a=structuredClone(state.article);
  document.querySelectorAll('[data-field]').forEach(el=>a[el.dataset.field]=el.dataset.field==='takeaways'?el.value.split('\n').filter(Boolean):el.value);
  document.querySelectorAll('[data-section-title]').forEach(el=>a.sections[Number(el.dataset.sectionTitle)].heading=el.value);
  document.querySelectorAll('[data-paragraph]').forEach(el=>{const[i,j]=el.dataset.paragraph.split(':').map(Number);a.sections[i].paragraphs[j].text=el.value;});
  for(const [attr,key] of [['figCaption','caption'],['figExplanation','explanation']]) document.querySelectorAll(`[data-${attr==='figCaption'?'fig-caption':'fig-explanation'}]`).forEach(el=>{const[i,j]=el.dataset[attr].split(':').map(Number);a.sections[i].figures[j][key]=el.value;});
  document.querySelectorAll('[data-table-field]').forEach(el=>{const[i,j,key]=el.dataset.tableField.split(':');a.sections[i].tables[j][key]=el.value;});
  document.querySelectorAll('[data-table-columns]').forEach(el=>{const[i,j]=el.dataset.tableColumns.split(':');a.sections[i].tables[j].columns=el.value.split('\t').map(v=>v.trim());});
  document.querySelectorAll('[data-table-rows]').forEach(el=>{const[i,j]=el.dataset.tableRows.split(':');a.sections[i].tables[j].rows=el.value.split('\n').filter(v=>v.trim()).map(row=>row.split('\t').map(v=>v.trim()));});
  for(const kind of ['fig','table'])document.querySelectorAll(`[data-${kind}-position]`).forEach(el=>{const[i,j]=el.dataset[kind+'Position'].split(':');a.sections[i][kind==='fig'?'figures':'tables'][j].after_paragraph=Number(el.value);});
  if(state.dirty)a.manual_edit=true;
  return a;
}
async function saveDraft(showToast=true){await savePaperInfo();if(!state.article)return;const article=collectDraft();await request(endpoint('article'),article);state.article=await request(endpoint('article'));state.dirty=false;$('saveDraft').textContent='已保存';renderPreview();await renderAudit();if(showToast)toast('修改已保存，预览和证据提醒已更新。');}
async function renderAudit(){
  const audit=await request(endpoint('audit'));$('issueCount').textContent=audit.issues.length?audit.issues.length+' 项提醒':'';
  $('auditContent').innerHTML=`<p class="audit-note">${escapeHTML(audit.note)}</p>${state.article.manual_edit?'<p class="notice">正文经过人工编辑，请重新核对段落与原有证据卡是否仍然一致。</p>':''}`+(audit.figure_coverage?.missing||[]).map(m=>`<p class="notice">配图尚待补全：${escapeHTML(m)}</p>`).join('')+audit.issues.map(i=>`<p class="audit-note">↗ ${escapeHTML(i.message)}</p>`).join('')+(state.article.review_notes||[]).map(t=>`<p class="audit-note">编辑复核：${escapeHTML(t)}</p>`).join('')+audit.evidence.map(e=>`<article class="evidence-item ${e.quote_found?'match':''}"><span>${e.quote_found?'已定位原文引句 · 仍需核查解读':'引句未匹配，请人工核对'} / p. ${escapeHTML(e.page)}</span><h3>${escapeHTML(e.claim)}</h3><blockquote>${escapeHTML(e.quote)}</blockquote><button class="text-button" data-source-page="${Number(e.page)||1}">查看原文页 ↗</button></article>`).join('');
}
function renderFigures(){
  $('figureCount').textContent=state.source.figures.length;
  const target=$('materialTarget').value;
  $('materialTarget').innerHTML='<option value="">新增候选配图</option>'+state.source.figures.map(f=>`<option value="${f.id}">替换 ${f.id} · ${escapeHTML(f.caption.slice(0,60))}</option>`).join('');
  $('materialTarget').value=target;
  $('figureGallery').innerHTML=state.source.figures.length?state.source.figures.map(f=>`<article class="figure-card" data-figure="${f.id}"><header><span>${f.id} · ${f.kind==='project_image'?'作者项目页':f.origin==='local_image'?'本机导入'+(f.page?' · p. '+f.page:''):f.origin==='pdf_embedded'?'内嵌原图 · p. '+f.page:'p. '+f.page}</span><label><input type="checkbox" data-selected ${f.selected?'checked':''}>用于写稿</label></header><img src="${asset(f.image)}" alt="${escapeHTML(f.caption)}" loading="lazy"><p class="crop-status">${f.crop_status==='page_fallback'?'保留整页 · 请核对或手动框选':f.reviewed?'已核对配图':f.origin?'导入素材 · 请核对图例、标注与完整性':'候选裁剪 · 请核对表头、行列与脚注'}</p><textarea aria-label="原图图注" data-caption>${escapeHTML(f.caption)}</textarea><footer><label><input type="checkbox" data-reviewed ${f.reviewed?'checked':''}>已核对</label>${f.origin?`<button class="text-button" data-replace-figure="${f.id}">替换图片 ↗</button>${f.page?`<button class="text-button" data-open-page="${f.page}">对照原页 ↗</button>`:""}`:f.kind==='project_image'?`<a class="text-button" target="_blank" rel="noopener" href="${escapeHTML(f.project_url)}">素材来源 ↗</a>`:`<button class="text-button" data-view-figure="${f.id}">原页 / 重裁 ↗</button>`}</footer></article>`).join(''):'<p class="muted">没有识别到标准图注。请在左侧原文页拖选需要的图表。</p>';
}
async function saveFigures(showToast=true){
  const figures=[...document.querySelectorAll('[data-figure]')].map(card=>({id:card.dataset.figure,selected:card.querySelector('[data-selected]').checked,reviewed:card.querySelector('[data-reviewed]').checked,caption:card.querySelector('[data-caption]').value}));
  if(figures.filter(f=>f.selected).length>12)throw new Error('生成时最多选择 12 张原图，请减少勾选。');
  const media=[...document.querySelectorAll('[data-project-video]')].map(card=>({id:card.dataset.projectVideo,selected:card.querySelector('[data-video-selected]').checked,caption:card.querySelector('[data-video-caption]').value}));state.source=await request(endpoint('figures'),{figures,media});state.figuresDirty=false;if(showToast)toast('配图选择已保存。');
}
async function renderProjectMedia(){
  const catalog=await request(endpoint('project-media'));
  const saved=[...state.source.figures,...(state.source.project_media||[])].map(f=>f.source_url);
  $('projectMediaList').innerHTML=(catalog.note?`<p class="muted">${escapeHTML(catalog.note)}</p>`:'')+(catalog.items||[]).map(m=>`<div class="media-candidate"><span>${m.kind==='video'?'视频':'图片'} · ${escapeHTML(m.label)}<small>${escapeHTML(m.url)}</small></span><button class="secondary" data-import-media="${m.id}" ${saved.includes(m.url)?'disabled':''}>${saved.includes(m.url)?'已下载':'下载到本机'}</button></div>`).join('');
  $('projectVideos').innerHTML=(state.source.project_media||[]).map(m=>`<div class="media-candidate" data-project-video="${m.id}"><label><input type="checkbox" data-video-selected ${m.selected?'checked':''}> 加入图文包<input data-video-caption value="${escapeHTML(m.caption)}"><small>原视频已下载 · ${(m.bytes/1024/1024).toFixed(1)} MB</small></label><video controls preload="metadata" src="${asset(m.file)}" style="max-width:300px;width:100%"></video></div>`).join('');
}
$('scanProjectMedia').onclick=guarded(async()=>{await saveDraft(false);await saveFigures(false);await request(endpoint('scan-media'),{});await watchJob(state.active);showTab('figures');});
$('projectMediaList').addEventListener('click',guarded(async e=>{const b=e.target.closest('[data-import-media]');if(!b)return;await saveDraft(false);await saveFigures(false);await request(endpoint('import-media'),{id:b.dataset.importMedia});await watchJob(state.active);showTab('figures');}));
function showPage(number){
  if(number)$('pageSelect').value=String(number);
  const page=state.source.pages[Number($('pageSelect').value)-1];if(!page)return;
  $('sourcePage').src=asset(page.image);$('sourceText').textContent=page.text;resetCrop();
}
function resetCrop(){state.crop=null;state.editFigure=null;$('cropSelection').hidden=true;$('saveCrop').disabled=true;$('saveCrop').textContent='将框选区域加入配图';}
function drawCrop(){if(!state.crop)return;const[x0,y0,x1,y1]=state.crop;Object.assign($('cropSelection').style,{left:x0*100+'%',top:y0*100+'%',width:(x1-x0)*100+'%',height:(y1-y0)*100+'%'});$('cropSelection').hidden=false;$('saveCrop').disabled=state.busy||x1-x0<.02||y1-y0<.02;}
let dragStart=null;
function coords(e){const r=$('cropStage').getBoundingClientRect();return[Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))];}
$('cropStage').addEventListener('pointerdown',e=>{dragStart=coords(e);$('cropStage').setPointerCapture(e.pointerId);});
$('cropStage').addEventListener('pointermove',e=>{if(!dragStart)return;const p=coords(e);state.crop=[Math.min(dragStart[0],p[0]),Math.min(dragStart[1],p[1]),Math.max(dragStart[0],p[0]),Math.max(dragStart[1],p[1])];drawCrop();});
$('cropStage').addEventListener('pointerup',()=>dragStart=null);

$('arxivForm').addEventListener('submit',guarded(async e=>{e.preventDefault();if(!approveNavigation())return;$('arxivSubmit').disabled=true;try{const r=await request('/api/import/arxiv',{url:$('arxivUrl').value,auto_draft:$('arxivAutoDraft').checked});await openJob(r.id);if(r.message)toast(r.message);}finally{$('arxivSubmit').disabled=false;}}));
$('arxivAutoDraft').onchange=()=>{$('arxivSubmit').textContent=$('arxivAutoDraft').checked?'生成并投递草稿 →':'解析论文 →';};
async function upload(file){if(!file)return;if(file.size>60*1024*1024)throw new Error('PDF 不能超过 60 MB。');const r=await request('/api/import/pdf',file,true);await openJob(r.id);$('pdfInput').value='';}
$('pdfInput').addEventListener('change',guarded(e=>upload(e.target.files[0])));
for(const name of ['dragenter','dragover'])$('dropZone').addEventListener(name,e=>{e.preventDefault();$('dropZone').classList.add('drag');});
$('dropZone').addEventListener('dragleave',()=>$('dropZone').classList.remove('drag'));
$('dropZone').addEventListener('drop',guarded(async e=>{e.preventDefault();$('dropZone').classList.remove('drag');await upload(e.dataTransfer.files[0]);}));
$('exampleLink').onclick=()=>$('arxivUrl').value='https://arxiv.org/abs/2212.10156';
$('newPaper').onclick=()=>{if(!approveNavigation())return;clearTimeout(state.timer);state.reviewEdits.clear();state.active=null;state.dirty=false;resetXhsView();$('workspace').hidden=true;$('welcome').hidden=false;$('taskBanner').hidden=true;refreshHistory().catch(e=>toast(e.message,true));};
$('settingsButton').onclick=()=>$('settingsDialog').showModal();$('configureInline').onclick=()=>$('settingsDialog').showModal();
$('helpButton').onclick=()=>$('helpDialog').showModal();
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
$('settingsForm').onsubmit=guarded(async e=>{e.preventDefault();const remember=$('rememberModels').checked;if(state.desktop){await request('/api/credentials',{...settings(),api_key:$('apiKey').value,profile_id:state.profileId||'',name:$('profileName').value.trim(),remember});await refreshModels();} $('settingsDialog').close();$('modelBadge').textContent=$('modelId').value||'模型已设置';toast(remember&&state.desktop?'全部模型配置已在软件目录加密保存。':'模型配置仅保留在本次运行内存中。');});
$('visionCapability').onchange=()=>{$('vision').disabled=$('visionCapability').value==='false';if($('vision').disabled)$('vision').checked=false;};
$('checkModel').onclick=guarded(async()=>{if(!state.desktop){toast('连接检测请在桌面版使用。',true);return;}if(!$('settingsForm').reportValidity())return;$('checkModel').disabled=true;$('modelConnectionState').textContent='保存配置并发送简短连接测试；不发送论文…';try{await request('/api/credentials',{...settings(),api_key:$('apiKey').value,profile_id:state.profileId||'',name:$('profileName').value.trim(),remember:$('rememberModels').checked});await refreshModels();const r=await request('/api/models/check',{});$('modelConnectionState').textContent=r.message;}catch(e){$('modelConnectionState').textContent=e.message;throw e;}finally{$('checkModel').disabled=false;}});
$('newModel').onclick=newModel;
$('modelProfiles').onchange=guarded(async()=>{const id=$('modelProfiles').value;if(!id){newModel();return;}await request('/api/credentials',{activate:id});await refreshModels();toast('已切换写作模型。');});
$('deleteModel').onclick=guarded(async()=>{if(!state.profileId)return;if(!confirm('删除这个模型配置及其保存的密钥？'))return;await request('/api/credentials',{delete:state.profileId});await refreshModels();toast('配置已删除。');});
$('clearKey').onclick=guarded(async()=>{if(!confirm('清除全部模型配置与加密文件？之后需要重新填写。'))return;if(state.desktop){await request('/api/credentials',{clear:true});await refreshModels();} $('apiKey').value='';$('modelBadge').textContent='密钥已清除';toast('内存配置与加密文件已清除；正在执行的请求需取消或退出软件。');});
$('openData').onclick=guarded(()=>request('/api/desktop/action',{action:'open-data'}));
$('openPdf').addEventListener('click',guarded(async e=>{if(state.desktop){e.preventDefault();await request('/api/desktop/action',{action:'open-pdf',id:state.active});}}));
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>showTab(b.dataset.tab));
$('history').addEventListener('click',guarded(async e=>{const b=e.target.closest('[data-job]');if(b&&approveNavigation())await openJob(b.dataset.job);}));
async function startGeneration(resume=false){
  if(state.busy||state.preparingDelivery)return;
  if(resume&&(state.dirty||state.metadataDirty||state.figuresDirty))throw new Error('正文或素材有未保存的修改。请先保存，再使用生成按钮应用修改；继续任务会沿用原来的写作设置与配图。');
  if(!$('modelId').value){$('settingsDialog').showModal();return;}
  const id=state.active,deliver=$('generateAutoDraft').checked,deliveryOptions=collectDeliveryEdits();
  state.preparingDelivery=true;busy(true);let queued=false;
  try{
    if(deliver){const account=await request('/api/wechat');if(id!==state.active)return;if(!account.configured){await openWechatSettings();throw new Error('先连接公众号，再使用生成后自动投递。');}}
    if(state.metadataDirty)await savePaperInfo();if(state.figuresDirty)await saveFigures(false);if(state.dirty)await saveDraft(false);
    if(id!==state.active)return;
    const effective={...options(),...(resume?state.recoveryOptions:{})};
    await request(`/api/jobs/${id}/generate`,{...effective,auto_draft:deliver,delivery_options:deliveryOptions,force_regenerate:Boolean(state.article&&!state.recovery)});
    queued=true;
  }finally{state.preparingDelivery=false;if(!queued)busy(false);}
  if(queued)await watchJob(id);
}
$('generate').onclick=guarded(()=>startGeneration());
$('generateAutoDraft').onchange=()=>{$('generate').innerHTML=$('generateAutoDraft').checked?'生成并投递草稿 <span>↗</span>':'生成完整推文 <span>✧</span>';};
$('saveDraft').onclick=guarded(()=>saveDraft());
$('draftEditor').addEventListener('input',()=>{state.dirty=true;$('saveDraft').textContent='保存修改';});
$('draftEditor').addEventListener('click',e=>{const b=e.target.closest('[data-title-option]');if(b){document.querySelector('[data-field=title]').value=state.article.title_options[Number(b.dataset.titleOption)];state.dirty=true;$('saveDraft').textContent='保存修改';}if(e.target.closest('[data-configure]'))$('settingsDialog').showModal();});
$('theme').onchange=()=>{state.writeThemeDirty=true;renderPreview();};
$('mobilePreview').onclick=()=>{$('preview').parentElement.classList.remove('wide');$('mobilePreview').classList.add('active');$('widePreview').classList.remove('active');};
$('widePreview').onclick=()=>{$('preview').parentElement.classList.add('wide');$('widePreview').classList.add('active');$('mobilePreview').classList.remove('active');};
$('paperInfoPanel').addEventListener('input',()=>{state.metadataDirty=true;$('savePaperInfo').textContent='保存论文资料';});
$('savePaperInfo').onclick=guarded(()=>savePaperInfo(true));
$('repairCrops').onclick=guarded(async()=>{await saveDraft(false);await saveFigures(false);await request(endpoint('repair-crops'),{});await watchJob(state.active);});
$('saveFigures').onclick=guarded(()=>saveFigures());$('pageSelect').onchange=()=>showPage();$('resetCrop').onclick=resetCrop;
$('figureGallery').addEventListener('click',e=>{const replace=e.target.closest('[data-replace-figure]');if(replace){$('materialsPanel').open=true;$('materialTarget').value=replace.dataset.replaceFigure;$('materialsPanel').scrollIntoView({behavior:'smooth'});return;}const pageButton=e.target.closest('[data-open-page]');if(pageButton){showPage(Number(pageButton.dataset.openPage));$('cropStage').scrollIntoView();return;}const b=e.target.closest('[data-view-figure]');if(!b)return;const f=state.source.figures.find(f=>f.id===b.dataset.viewFigure);showPage(f.page);const p=state.source.pages[f.page-1];state.crop=[f.bbox[0]/p.width,f.bbox[1]/p.height,f.bbox[2]/p.width,f.bbox[3]/p.height];state.editFigure=f.id;$('cropCaption').value=f.caption;$('saveCrop').textContent='更新这张配图';drawCrop();$('cropStage').scrollIntoView({behavior:'smooth',block:'center'});});
$('saveCrop').onclick=guarded(async()=>{await saveFigures(false);state.source=await request(endpoint('crop'),{page:Number($('pageSelect').value),box:state.crop,caption:$('cropCaption').value,figure_id:state.editFigure});renderFigures();resetCrop();renderPreview();toast('配图已保存，保留原页来源。');});
$('auditContent').addEventListener('click',e=>{const b=e.target.closest('[data-source-page]');if(b){showTab('figures');showPage(Number(b.dataset.sourcePage));}});
$('cancelTask').onclick=guarded(async()=>{await request(endpoint('cancel'),{});toast('已请求停止。已完成的内容会保留；如已开始上传，请查看投递回执。');});
$('exportPrompt').onclick=guarded(async()=>{await savePaperInfo();await saveFigures(false);if(state.desktop){await request('/api/desktop/action',{action:'prompt',id:state.active,options:options()});}else{const r=await request(endpoint('prompt'),options());downloadText(r.prompt,'paper-writing-prompt.txt');}toast('提示词包含全文、配图 ID、页码和成稿 JSON 格式。');});
$('importDraft').onclick=()=>$('jsonDialog').showModal();
$('confirmJson').onclick=guarded(async()=>{let article;try{article=JSON.parse($('articleJson').value.trim().replace(/^```(?:json)?\s*|\s*```$/g,''));}catch{throw new Error('JSON 格式错误，请粘贴完整对象。');}await request(endpoint('article'),article);state.article=await request(endpoint('article'));state.dirty=false;$('jsonDialog').close();renderEditor();renderPreview();await renderAudit();busy(false);toast('成稿已导入，请查看证据提醒。');});
async function exportFile(format){await saveFigures(false);await saveDraft(false);if(state.desktop){const r=await request('/api/desktop/action',{action:'export',id:state.active,format,theme:$('theme').value});if(!r.cancelled)toast('文件已保存：'+r.name);return;}const a=document.createElement('a');a.href=endpoint('export')+'?format='+format+'&theme='+$('theme').value;a.download='';a.click();}
$('exportZip').onclick=guarded(()=>exportFile('zip'));$('exportMd').onclick=guarded(()=>exportFile('md'));
$('copyHtml').onclick=guarded(async()=>{await saveFigures(false);await saveDraft(false);const r=await fetch(endpoint('export')+'?format=html&theme='+$('theme').value);if(!r.ok)throw new Error('导出失败');const html=await r.text();if(!navigator.clipboard?.write||!window.ClipboardItem){if(state.desktop)await exportFile('html');else downloadText(html,'wechat.html','text/html');toast('浏览器不支持富文本复制，已下载 HTML。');return;}try{await navigator.clipboard.write([new ClipboardItem({'text/html':new Blob([html],{type:'text/html'}),'text/plain':new Blob([state.article.sections.flatMap(s=>s.paragraphs.map(p=>p.text)).join('\n\n')],{type:'text/plain'})})]);toast('已复制正文。微信可能不保留剪贴板图片，请逐张上传并确认。');}catch{if(state.desktop)await exportFile('html');else downloadText(html,'wechat.html','text/html');toast('复制权限未开启，已下载 HTML。');}});
$('skillFormat').onclick=guarded(async()=>{if(state.reviewDirty)await saveReviewOptions();await saveFigures(false);await saveDraft(false);await request(endpoint('skill'),{});await watchJob(state.active);});
window.addEventListener('beforeunload',e=>{if(state.dirty||state.metadataDirty||state.figuresDirty||state.reviewDirty||state.xhs.dirty){e.preventDefault();e.returnValue='';}});
guarded(async()=>{await refreshModels();await refreshHistory();const account=await request('/api/wechat');$('generateAutoDraft').checked=Boolean(account.configured);$('generateAutoDraft').onchange();await refreshXhsConnection();window.paperStudioReady=true;})();

window.paperStudioStatus=()=>({dirty:state.dirty||state.metadataDirty||state.figuresDirty||state.reviewDirty||state.xhs.dirty,busy:state.busy||state.xhs.working});


// Automated checks and optional human review remain distinct records.
let wechatConfig={},wechatWorking=false;
function resetHumanChecks(){document.querySelectorAll('[data-review-check]').forEach(e=>e.checked=false);}
function invalidateReviewLocally(){if(!state.active)return;if(state.wechatReview)state.wechatReview.approved=false;resetHumanChecks();updateReviewButtons();$('wechatReviewState').textContent='内容有修改，已有人工审核不再适用。';$('deliveryStatus').textContent='有未保存修改';$('deliveryStatus').className='status-chip is-pending';$('deliveryActionHint').textContent='自动投递会先保存你的修改，再重新检查当前版本。';}
function markReviewDirty(field){if(!state.active)return;state.reviewDirty=true;if(field)state.reviewEdits.add(field);invalidateReviewLocally();}
function updateWechatCounts(){
  for(const [id,limit] of [['wechatTitle',32],['wechatDigest',120]]){
    const count=Array.from($(id).value.trim()).length;
    $(id+'Count').textContent=`${count} / ${limit} 个字符`+(count>limit?' · 超出时保存会自动整理':'');
    $(id+'Count').classList.toggle('field-over-limit',count>limit);
  }
}
function renderFieldAdjustments(changes=[]){
  state.wechatFieldAdjustments=changes;
  $('wechatFieldAdjustments').hidden=!changes.length;
  $('wechatFieldAdjustmentText').textContent=changes.map(c=>`${c.field==='title'?'标题':'摘要'}：${c.reason}\n原文（${Array.from(c.before).length} 字符）：${c.before}\n提交版（${Array.from(c.after).length} 字符）：${c.after}`).join('\n\n');
}
function updateReviewButtons(){
  const r=state.wechatReview||{},changed=state.reviewDirty||state.dirty||state.metadataDirty||state.figuresDirty;
  const active=state.busy||wechatWorking;
  $('fitWechatFields').disabled=active||!state.article;
  const receiptBlocks=['unknown','submitted','submitted_unverified','uploading'].includes(r.submission?.state);
  const canRecheck=r.submission?.state==='submitted_unverified'&&Boolean(r.submission?.media_id);
  $('autoSubmitWechat').disabled=active||!state.article||(receiptBlocks&&!canRecheck);
  $('autoSubmitWechat').textContent=canRecheck?'核对已创建草稿（不重复上传）':'一键投递公众号草稿';
  $('autoPrepareWechat').disabled=active||!state.article||receiptBlocks;
  const terminal=['unknown','submitted','submitted_unverified','uploading'].includes(r.submission?.state);
  $('submitWechat').disabled=active||changed||!r.approved||!wechatConfig.configured||terminal;
  $('approveWechat').disabled=active||changed||!state.article||!wechatConfig.configured||Boolean(r.blocker);
  $('revokeWechat').disabled=active||!r.approved;
  $('recheckWechat').disabled=active||!r.submission?.media_id;
  $('resetWechat').disabled=active;
}
function renderWechatAccount(){
  $('wechatAccountLabel').textContent=wechatConfig.configured?'投递到 '+(wechatConfig.name||'已连接公众号')+' · 仅创建草稿':'连接公众号后，一键投递到你的草稿箱';
  $('deliveryPreviewAccount').textContent=wechatConfig.name||'公众号草稿';
}
function allCovers(){return [...(state.source?.covers||[]).slice().reverse(),...(state.source?.figures||[])];}
function fillCovers(){const previous=$('wechatCover').value;$('wechatCover').innerHTML='<option value="">自动选择 overview</option>'+allCovers().map(f=>`<option value="${escapeHTML(f.id)}">${escapeHTML((f.kind==='designed_cover'?'设计封面':f.id)+' · '+(f.caption||'').slice(0,90))}</option>`).join('');$('wechatCover').value=previous;}
function updateCoverPreview(){const f=allCovers().find(x=>x.id===$('wechatCover').value);$('wechatCoverPreview').hidden=!f;if(f)$('wechatCoverPreview').src=asset(f.image);updateDeliveryPreview();}
function resetDeliveryView(){
  for(const id of ['wechatTitle','wechatAuthor','wechatDigest','wechatSource'])$(id).value='';
  $('wechatCover').innerHTML='<option value="">自动选择 overview</option>';
  for(const id of ['wechatCoverPreview','deliverySocialCover']){$(id).hidden=true;$(id).removeAttribute('src');}
  $('wechatPreview').removeAttribute('src');$('deliveryPreviewEmpty').hidden=false;$('deliveryCoverEmpty').hidden=false;
  $('deliverySocialTitle').textContent='一篇值得读懂的研究';$('deliverySocialDigest').textContent='完成加载后展示本篇论文的投递稿。';$('deliverySocialAuthor').textContent='Paper Studio';
  $('deliveryStatus').textContent='等待整理';$('deliveryStatus').className='status-chip is-pending';$('deliveryCheckedAt').textContent='尚未运行';
  $('deliveryReportContent').innerHTML='<p class="muted">点击自动投递工作区，查看本篇论文的检查结果。</p>';
  $('wechatSubmission').textContent='尚未提交。完成上传后，这里显示草稿编号与核对结果。';$('wechatSubmission').classList.remove('has-receipt');
  $('wechatWarnings').textContent='';$('wechatReviewState').textContent='';$('recheckWechat').hidden=true;$('resetWechat').hidden=true;$('deliveryTabStatus').hidden=true;
  $('deliveryCoverLabel').textContent='论文 overview 优先';$('deliveryCoverNote').textContent='保留完整原图，自动适配横版封面。';$('deliveryEditor').textContent='Paper Studio';
  $('deliveryThemeLabel').textContent='松墨 · 研究手记';$('deliveryLayoutNote').textContent='保留当前排版与手动调整。';$('deliveryActionHint').textContent='自动整理字段与封面，检查后上传。不会公开发表或群发。';
  for(const id of ['deliveryStepPrepare','deliveryStepCheck','deliveryStepSubmit'])$(id).classList.remove('is-complete');
  renderFieldAdjustments([]);resetHumanChecks();updateReviewButtons();
}
function updateDeliveryPreview(){
  const f=allCovers().find(x=>x.id===$('wechatCover').value),title=$('wechatTitle').value||state.article?.title;
  $('deliverySocialCover').hidden=!f;$('deliveryCoverEmpty').hidden=Boolean(f);if(f)$('deliverySocialCover').src=asset(f.image);
  $('deliverySocialTitle').textContent=title||'一篇值得读懂的研究';$('deliverySocialDigest').textContent=$('wechatDigest').value||state.article?.digest||'成稿后，这里展示投稿标题、封面与摘要。';
  const author=$('wechatAuthor').value;const editor=state.wechatReview?.automation?.editor||'Paper Studio';
  $('deliverySocialAuthor').textContent=author||'未署名';$('deliveryEditor').textContent=author||(!state.wechatReview?.automation?.checked_at&&!state.reviewEdits.has('wechatAuthor')?editor+'（默认）':'未署名');
  $('deliveryThemeLabel').textContent=$('wechatTheme').selectedOptions[0]?.textContent||'松墨 · 研究手记';
}
function collectDeliveryEdits(){
  const fields={wechatTitle:'title',wechatAuthor:'author',wechatDigest:'digest',wechatCover:'cover_id',wechatTheme:'theme',baoyuTheme:'baoyu_theme',wechatSource:'source_url'},values={};
  for(const id of state.reviewEdits){if(fields[id])values[fields[id]]=$(id).value;}
  if(state.writeThemeDirty&&!('theme' in values))values.theme=$('theme').value;
  return values;
}
function renderAutomationReport(r){
  const a=r.automation||{},sub=r.submission||{},changed=state.reviewDirty||state.dirty||state.metadataDirty||state.figuresDirty;
  const delivered=['submitted','submitted_unverified'].includes(sub.state),allBlockers=a.blockers||[],warnings=a.warnings||[];
  const receiptProtected=delivered&&allBlockers.some(v=>String(v).startsWith('已有提交记录'));
  const blockers=receiptProtected?allBlockers.filter(v=>!String(v).startsWith('已有提交记录')):allBlockers;
  let label='等待整理',tone='pending';
  if(!state.article){label='等待成稿';}else if(sub.state==='submitted_unverified'){label='草稿已创建 · 核对待完成';tone='warning';}else if(delivered){label='已投递草稿';tone='ready';}else if(sub.state==='uploading'){label=state.busy?'正在上传':'上次投递待核对';tone=state.busy?'running':'warning';}else if(sub.state==='unknown'){label='创建结果待核对';tone='warning';}else if(changed){label='有未保存修改';}else if(blockers.length&&a.checked_at){label='需要处理 '+blockers.length+' 项';tone='warning';}else if(a.ready){label='已就绪，可投递';tone='ready';}
  $('deliveryStatus').textContent=label;$('deliveryStatus').className='status-chip is-'+tone;
  $('deliveryTabStatus').hidden=!a.ready&&!delivered;$('deliveryTabStatus').classList.toggle('is-delivered',delivered);
  const timestamp=a.checked_at;const date=timestamp?new Date(typeof timestamp==='number'?timestamp*1000:timestamp):null;
  $('deliveryCheckedAt').textContent=date&&!Number.isNaN(date.valueOf())?'检查于 '+date.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}):'准备后运行检查';
  const items=(a.stages||[]).map(stage=>typeof stage==='string'?{label:stage,status:'complete'}:stage);
  $('deliveryReportContent').innerHTML=items.map(t=>`<div class="check-row ${t.status==='complete'?'is-complete':'is-pending'}"><span aria-hidden="true">${t.status==='complete'?'✓':'·'}</span><div><b>${escapeHTML(t.label||t.id||'检查')}</b>${t.detail?`<small>${escapeHTML(t.detail)}</small>`:''}</div></div>`).join('')+(receiptProtected?'<div class="check-row is-complete"><span aria-hidden="true">✓</span><div><b>草稿已创建</b><small>防重复投递保护已启用。后续修改可在公众号草稿箱继续处理。</small></div></div>':'')+(blockers.length&&a.checked_at?'<div class="check-alert"><b>需要先处理</b><ul>'+blockers.map(v=>`<li>${escapeHTML(v)}</li>`).join('')+'</ul></div>':'')+(warnings.length?`<details class="check-warnings"><summary>${warnings.length} 项编辑提醒 · 可在草稿箱审阅</summary><ul>${warnings.map(v=>`<li>${escapeHTML(v)}</li>`).join('')}</ul></details>`:'')+(!items.length&&(!blockers.length||!a.checked_at)?'<p class="muted">检查将覆盖提交字段、正文版式、封面和配图。科学结论与转载权限仍需在发布前核对。</p>':'');
  const cover=a.cover||{},selected=allCovers().find(f=>f.id===$('wechatCover').value);
  $('deliveryCoverLabel').textContent=cover.selection_reason==='overview'?'论文 overview':cover.selection_reason==='figure_fallback'?'论文原图封面':cover.selection_reason==='editorial_fallback'?'编辑设计封面':selected?'沿用所选封面':'论文 overview 优先';
  $('deliveryCoverNote').textContent=cover.selection_note||'保留完整原图，自动适配横版封面。';
  $('deliveryLayoutNote').textContent=r.parts?.length>1?`全文保留 · ${r.parts.length} 篇有序图文 · 同一份草稿`:r.html_chars?'投递版式 '+r.html_chars.toLocaleString()+' 字符':'保留当前排版与手动调整。';
  $('deliveryStepPrepare').classList.toggle('is-complete',Boolean(selected&&state.article));$('deliveryStepCheck').classList.toggle('is-complete',Boolean(a.ready||delivered));$('deliveryStepSubmit').classList.toggle('is-complete',delivered);
  $('deliveryActionHint').textContent=delivered?'这篇已创建草稿。需要改动时请先到微信后台核对。':sub.state==='uploading'&&!state.busy?'上次投递中断，请先到后台核对。确认没有对应草稿后，可使用下方恢复操作。':['unknown','submitted_unverified','uploading'].includes(sub.state)?'已有上传记录，请先核对回执，避免重复创建。':!state.article?'先在「撰写与排版」生成或导入完整文章。':!wechatConfig.configured?'连接公众号后，即可自动整理并投递。':changed?'自动投递会先保存修改，再检查当前版本。':'自动整理字段与封面，检查后上传。不会公开发表或群发。';
  updateDeliveryPreview();
}
async function runAutomaticDelivery(submit,explicitOptions=null){
  if(state.busy||wechatWorking)return;
  if(!state.article)throw new Error('请先生成或导入完整文章。');
  const id=state.active;wechatWorking=true;state.preparingDelivery=true;busy(true);updateReviewButtons();
  let queued=false;
  try{
    const account=await request('/api/wechat');if(id!==state.active)return;
    if(submit&&!account.configured){await openWechatSettings();throw new Error('先连接公众号，即可一键投递草稿。');}
    const edits=explicitOptions??collectDeliveryEdits();
    if(state.figuresDirty)await saveFigures(false);
    if(state.dirty)await saveDraft(false);else await savePaperInfo();
    if(id!==state.active)return;
    await request(`/api/jobs/${id}/wechat-auto-${submit?'submit':'prepare'}`,{options:edits});
    state.reviewDirty=false;state.reviewEdits.clear();state.writeThemeDirty=false;resetHumanChecks();queued=true;
    busy(true);$('deliveryStatus').textContent='正在处理';$('deliveryStatus').className='status-chip is-running';
  }finally{wechatWorking=false;state.preparingDelivery=false;if(!queued)busy(false);updateReviewButtons();}
  if(queued&&id===state.active)await watchJob(id);
}
$('autoPrepareWechat').onclick=guarded(()=>runAutomaticDelivery(false));
$('autoSubmitWechat').onclick=guarded(()=>runAutomaticDelivery(true));

$('generateCover').onclick=guarded(async()=>{await saveDraft(false);const covers=await request(endpoint('cover'),{title:$('coverTitle').value.trim()||$('wechatTitle').value,label:$('coverLabel').value,theme:$('wechatTheme').value==='baoyu'?'ink':$('wechatTheme').value});state.source.covers=[...(state.source.covers||[]),...covers];fillCovers();$('wechatCover').value=covers[0].id;updateCoverPreview();markReviewDirty('wechatCover');toast('已生成横版和方形封面；当前选择横版，请保存提交设置。');});
async function refreshWechatReview(){
  if(!state.active)return;
  const id=state.active,account=await request('/api/wechat');if(id!==state.active)return;wechatConfig=account;renderWechatAccount();
  const r=await request(`/api/jobs/${id}/wechat`);if(id!==state.active)return;state.wechatReview=r;
  if(!state.reviewDirty){
    $('wechatTitle').value=r.options.title||'';$('wechatAuthor').value=r.options.author??'Paper Studio';$('wechatDigest').value=r.options.digest||'';
    $('wechatSource').value=r.options.source_url||state.source?.publication_info?.paper_url||'';$('wechatTheme').value=r.options.theme||'ink';$('baoyuTheme').value=r.options.baoyu_theme||'simple';
    fillCovers();
    $('wechatCover').value=r.options.cover_id||'';updateCoverPreview();
    renderFieldAdjustments(r.field_adjustments||[]);state.reviewEdits.clear();
  }
  updateWechatCounts();
  const lengthNote=r.parts?.length>1?`全文已无损分为 ${r.parts.length} 篇有序图文，放入同一份草稿。各篇 HTML：${r.parts.map(p=>p.html_chars.toLocaleString()).join(' / ')} 字符；预览展示实际分篇边界。`:r.html_chars?`待上传 HTML：${r.html_chars.toLocaleString()} 字符。`:'';
  $('wechatWarnings').textContent=[r.automation?.checked_at?r.blocker:'',r.video_count?`含 ${r.video_count} 段视频来源卡。视频文件不会自动插入公众号，请提交后在微信后台补入。`:'',lengthNote,r.html_chars?'超过 1 MB 的图片将尝试转为 JPEG，保持原尺寸；预览使用实际待上传图片。':''].filter(Boolean).join('\n');
  $('wechatReviewState').textContent=r.approved&&!state.reviewDirty&&!state.dirty&&!state.metadataDirty&&!state.figuresDirty?'人工审核已通过，可提交当前版本。':'人工审核可选；自动投递不要求逐项勾选，可在公众号草稿箱审阅。';
  const canPreview=Boolean(state.article&&!r.blocker);$('deliveryPreviewEmpty').hidden=canPreview;if(canPreview)$('wechatPreview').src=endpoint('wechat-preview')+'?v='+Date.now();else $('wechatPreview').removeAttribute('src');
  const sub=r.submission||{};$('wechatSubmission').hidden=false;
  $('wechatSubmission').textContent=(sub.state==='uploading'&&!state.busy?'上次投递中断，请先到后台核对。只有确认实际状态后，再恢复提交。\n':'')+(sub.message||'尚未提交。完成上传后，这里显示草稿编号与核对结果。')+(sub.media_id?'\n草稿 ID：'+sub.media_id:'');$('wechatSubmission').classList.toggle('has-receipt',Boolean(sub.media_id));
  const v=sub.verification||{},mismatches=[];for(const [key,ok] of Object.entries(v.field_matches||{}))if(ok===false)mismatches.push(({title:'标题',author:'署名',digest:'摘要',content_source_url:'来源链接',thumb_media_id:'封面'})[key]||key);if(v.text_match===false)mismatches.push('正文文字');if(v.image_count_match===false)mismatches.push('图片数量');else if(v.image_addresses_match===false)mismatches.push('图片地址');if(mismatches.length)$('wechatSubmission').textContent+='\n待核对差异：'+mismatches.join('、');if(v.error_code===40007)$('wechatSubmission').textContent+='\n微信已无法读取这个草稿 ID；可能已发表或被移除，请在后台确认。';
  if(v.part_count_match===false)$('wechatSubmission').textContent+=`\n图文篇数不一致：预期 ${v.expected_part_count}，实际 ${v.actual_part_count}；请核对已有草稿。`;
  $('recheckWechat').hidden=!sub.media_id;$('resetWechat').hidden=!['submitted','submitted_unverified','unknown','uploading'].includes(sub.state);
  renderAutomationReport(r);updateReviewButtons();
}
async function saveReviewOptions(){
  if(state.figuresDirty)await saveFigures(false);
  if(state.dirty)await saveDraft(false);else await savePaperInfo();
  await request(endpoint('wechat-options'),{title:$('wechatTitle').value,author:$('wechatAuthor').value,digest:$('wechatDigest').value,cover_id:$('wechatCover').value,cover_explicit:state.reviewEdits.has('wechatCover'),theme:$('wechatTheme').value,baoyu_theme:$('baoyuTheme').value,source_url:$('wechatSource').value,field_adjustments:state.wechatFieldAdjustments||[]});
  state.reviewDirty=false;state.reviewEdits.clear();resetHumanChecks();await refreshWechatReview();
}
async function saveWechatConfig(check=false){
  if(wechatWorking)return;wechatWorking=true;
  try{
    wechatConfig=await request('/api/wechat/config',{name:$('wechatName').value,appid:$('wechatAppId').value.trim(),secret:$('wechatSecret').value.trim(),remember:$('rememberWechat').checked});
    $('wechatSecret').value='';$('wechatSecret').placeholder='已配置；留空保留当前账号密钥';
    $('wechatConnectionState').textContent='配置已保存。';renderWechatAccount();
    if(check){$('wechatConnectionState').textContent='正在连接微信官方接口…';wechatConfig=await request('/api/wechat/check',{});$('wechatConnectionState').textContent=wechatConfig.check.message;}
    if(state.active)await refreshWechatReview();
  }catch(e){$('wechatConnectionState').textContent=e.message;throw e;}
  finally{wechatWorking=false;updateReviewButtons();}
}
async function openWechatSettings(){
  wechatConfig=await request('/api/wechat');$('wechatName').value=wechatConfig.name;$('wechatAppId').value=wechatConfig.appid;$('wechatSecret').value='';$('rememberWechat').checked=wechatConfig.remember;
  $('wechatSecret').placeholder=wechatConfig.configured?'已配置；留空保留当前账号密钥':'仅在本机填写 AppSecret';$('wechatConnectionState').textContent=wechatConfig.warning||wechatConfig.check?.message||'';$('wechatDialog').showModal();
}
$('wechatSettings').addEventListener('click',guarded(openWechatSettings));
$('wechatForm').addEventListener('submit',guarded(async e=>{e.preventDefault();await saveWechatConfig();}));
$('checkWechat').addEventListener('click',guarded(()=>saveWechatConfig(true)));
$('clearWechat').addEventListener('click',guarded(async()=>{if(!confirm('清除本机公众号配置与加密文件？不会删除公众号文章。'))return;wechatConfig=await request('/api/wechat/config',{clear:true});$('wechatSecret').value='';$('wechatAppId').value='';$('wechatName').value='';$('wechatConnectionState').textContent='公众号配置已清除。';if(state.active)await refreshWechatReview();}));
$('saveWechatOptions').addEventListener('click',guarded(async()=>{await saveReviewOptions();toast('已更新投递稿，可自动检查并提交；人工审核仍可选。');}));
$('fitWechatFields').addEventListener('click',guarded(async()=>{
  const fitted=await request(endpoint('wechat-fit'),{title:$('wechatTitle').value,digest:$('wechatDigest').value});
  $('wechatTitle').value=fitted.options.title;$('wechatDigest').value=fitted.options.digest;
  const merged=new Map((state.wechatFieldAdjustments||[]).map(c=>[c.field,c]));
  fitted.field_adjustments.forEach(c=>merged.set(c.field,c));
  renderFieldAdjustments([...merged.values()]);updateWechatCounts();markReviewDirty('wechatTitle');markReviewDirty('wechatDigest');updateDeliveryPreview();
  toast(fitted.field_adjustments.length?'已整理提交字段，原稿不变；请检查并保存。':'标题与摘要已符合长度要求。');
}));
$('approveWechat').addEventListener('click',guarded(async()=>{
  const checks={};document.querySelectorAll('[data-review-check]').forEach(e=>checks[e.dataset.reviewCheck]=e.checked);
  await request(endpoint('wechat-approve'),{checks,reviewer:$('wechatReviewer').value});await refreshWechatReview();toast('当前版本已通过人工审核。');
}));
$('revokeWechat').addEventListener('click',guarded(async()=>{await request(endpoint('wechat-revoke'),{});resetHumanChecks();await refreshWechatReview();}));
$('submitWechat').addEventListener('click',guarded(async()=>{
  if(state.reviewDirty||state.dirty||state.metadataDirty||state.figuresDirty)throw new Error('有未保存修改，请先保存并重新审核。');
  if(wechatWorking)return;wechatWorking=true;updateReviewButtons();
  try{await request(endpoint('wechat-submit'),{});busy(true);await watchJob(state.active);}finally{wechatWorking=false;updateReviewButtons();}
}));
$('recheckWechat').addEventListener('click',guarded(async()=>{await request(endpoint('wechat-recheck'),{});busy(true);await watchJob(state.active);}));
$('resetWechat').addEventListener('click',guarded(async()=>{if(!confirm('请先检查公众号草稿箱。继续将允许创建另一份草稿，已有草稿不会删除。确定已检查并需要重新提交？'))return;await request(endpoint('wechat-reset'),{confirmed:true});await refreshWechatReview();}));
for(const id of ['wechatTitle','wechatAuthor','wechatDigest','wechatCover','wechatTheme','wechatSource','baoyuTheme'])$(id).addEventListener('input',()=>{markReviewDirty(id);updateWechatCounts();updateDeliveryPreview();if(id==='wechatCover')updateCoverPreview();});
for(const id of ['draftEditor','paperInfoPanel'])$(id)?.addEventListener('input',invalidateReviewLocally);
for(const id of ['figureGallery','projectVideos'])$(id)?.addEventListener('input',()=>{state.figuresDirty=true;invalidateReviewLocally();});

$('reviewBaoyu').onclick=guarded(async()=>{await saveReviewOptions();await request(endpoint('skill'),{});await watchJob(state.active);});

async function renderMaterials(){
  const id=state.active;const c=await request(endpoint('materials'));if(id!==state.active)return;
  $('materialsNotes').textContent=(c.notes||[]).join(' ');
  $('materialGallery').innerHTML=(c.items||[]).map(i=>`<article class="material-card"><a href="${asset(i.image)}" target="_blank" rel="noopener"><img src="${asset(i.image)}" loading="lazy" alt="${escapeHTML(i.caption)}"></a><p>${i.width} × ${i.height} px · p. ${i.pages.join(', ')}</p><div class="button-row"><button class="secondary" data-accept-material="${i.id}">使用此素材</button><button class="text-button" data-material-page="${i.pages[0]}">对照原页</button></div></article>`).join('');
}
async function prepareMaterialChange(){if(state.busy)throw new Error('请等待当前任务完成。');await saveDraft(false);await saveFigures(false);}
async function materialChanged(source){state.source=source;renderFigures();renderPreview();invalidateReviewLocally();await renderAudit();toast('图片已保存。请核对完整性；替换图片后需重新审核。');}
$('scanMaterials').onclick=guarded(async()=>{await prepareMaterialChange();await request(endpoint('scan-materials'),{});await watchJob(state.active);});
$('imageInput').onchange=guarded(async()=>{const file=$('imageInput').files[0];if(!file)return;try{if(file.size>20*1024*1024)throw new Error('图片不能超过 20 MB。');await prepareMaterialChange();const q=new URLSearchParams({filename:file.name,caption:$('materialCaption').value,page:$('materialPage').value,replace_id:$('materialTarget').value});await materialChanged(await request(endpoint('import-image')+'?'+q,file,true));}finally{$('imageInput').value='';}});
$('materialGallery').onclick=guarded(async e=>{const page=e.target.closest('[data-material-page]');if(page){showPage(Number(page.dataset.materialPage));$('cropStage').scrollIntoView();return;}const b=e.target.closest('[data-accept-material]');if(!b)return;await prepareMaterialChange();await materialChanged(await request(endpoint('accept-material'),{id:b.dataset.acceptMaterial,replace_id:$('materialTarget').value||null,caption:$('materialCaption').value}));});

let feedTimer;
async function refreshFeed(fill=false){
  const f=await request('/api/autofeed');state.feed=f;
  $('feedBadge').textContent=f.running?'运行中':f.config.enabled?'已启用':'已暂停';
  $('feedStatus').textContent=`已入列 ${f.candidate_count??f.items.length} 篇标题 · ${f.message}`;
  if(fill){
    const session=await request('/api/session'),account=await request('/api/wechat');
    $('feedModel').innerHTML=(session.profiles||[]).map(p=>`<option value="${escapeHTML(p.id)}">${escapeHTML(p.name||p.model)}</option>`).join('');
    $('feedModel').value=f.config.model_profile||session.active_profile||'';
    $('feedMax').value=f.config.max_daily;$('feedStyle').value=f.config.style;
    $('feedVision').checked=f.config.vision;$('feedEnabled').checked=f.config.enabled;
    $('feedAlphaXiv').checked=(f.config.sources||[]).includes('alphaxiv');
    $('feedTopics').innerHTML=f.topics.map(t=>`<label class="checkbox"><input type="checkbox" data-feed-topic="${t.id}" ${f.config.topics.includes(t.id)?'checked':''}>${escapeHTML(t.label)}</label>`).join('');
    $('feedBinding').textContent='目标公众号：'+(account.name||account.appid||'尚未配置')+'。自动配置绑定此账号与所选模型；切换账号后须重新保存。';
  }
  $('feedSave').disabled=f.running;$('feedRun').disabled=f.running||!f.config.enabled;$('feedDiscover').disabled=f.running;
  renderFeedQueue();
  const q=f.selected_queue||{};
  $('feedFinish').disabled=!f.running||f.mode==='discovery';$('feedPause').disabled=!f.running&& !f.config.enabled;
  $('feedResume').disabled=f.running||!q.urls?.length||q.next>=q.urls.length;
  $('feedSerial').textContent=(q.urls?.length?`所选队列：已完成 ${q.next}/${q.urls.length} 篇 · ${f.running?'处理中':q.state==='complete'?'已完成':'已停止，可继续'}。未开始的论文不会下载 PDF。`:'每次选 1–5 篇，使用当前模型与公众号。停止会在当前网络请求结束后生效，后续 PDF 不会提前下载。')+' 新稿自适应写作与排版，模型读取文本，原图用于配图；编辑作品前请先停止队列。';
  clearTimeout(feedTimer);
  if($('feedDialog').open)feedTimer=setTimeout(()=>guarded(()=>refreshFeed())(),2000);
}
function visibleFeedItems(){const q=$('feedSearch').value.trim().toLowerCase(),category=$('feedCategory').value;return(state.feed?.items||[]).filter(i=>matchesCategory(i,category)&&(!q||((i.title||'')+' '+i.id).toLowerCase().includes(q)));}
function renderFeedQueue(){
  const f=state.feed;if(!f)return;renderCategoryFilter('feedCategory',f.items||[]);const items=visibleFeedItems();
  const labels={candidate:'标题已取得',resources_pending:'项目/代码待核实',processing:'正在生成',held:'待处理',submitted:'已投递草稿',submitted_unverified:'待核对提交',unknown:'提交结果待核对',existing:'已在作品库'};
  $('feedCount').textContent=`当前显示 ${items.length} 篇 · 已选 ${state.feedSelected.size} 篇`;
  $('feedSelected').disabled=f.running||!state.feedSelected.size;
  $('feedQueue').innerHTML=items.length?items.map(i=>`<article class="feed-card"><label class="feed-pick"><input type="checkbox" data-feed-pick="${escapeHTML(i.url)}" ${state.feedSelected.has(i.url)?'checked':''} ${f.running?'disabled':''}><span><a href="${escapeHTML(i.url)}" target="_blank" rel="noopener">${escapeHTML(i.title)}</a><small>${escapeHTML((i.published||'').slice(0,10))} · ${escapeHTML(labels[i.state]||i.state)} · ${escapeHTML((i.discovered_via||['arxiv']).join(' + '))}</small><span class="topic-chips">${topicChips(i.topics)}</span></span></label><p class="muted">${escapeHTML((i.priority?.reasons||[]).join(' · '))}</p><p class="muted">${escapeHTML(i.affiliations?.length?'作者机构：'+i.affiliations.join(' / '):'作者机构：元数据未提供，待原文核实')}</p><div class="button-row"><button class="secondary" data-feed-direct="${escapeHTML(i.url)}" ${f.running?'disabled':''}>一键生成并投递</button><a href="${escapeHTML(i.alphaxiv_url||'https://www.alphaxiv.org/abs/'+i.id)}" target="_blank" rel="noopener">AlphaXiv ↗</a>${i.job_id?`<button class="text-button" data-feed-job="${i.job_id}">打开本地稿件</button>`:''}</div></article>`).join(''):'<p class="muted">暂无匹配标题；点击“获取最新标题与链接”开始。</p>';
}
$('feedSearch').oninput=renderFeedQueue;
$('feedCategory').onchange=renderFeedQueue;
$('feedCopy').onclick=guarded(async()=>{await navigator.clipboard.writeText(visibleFeedItems().map(i=>`${i.title}\n${i.url}`).join('\n\n'));toast('已复制当前列表的标题与 arXiv 链接。');});
$('feedQueue').onchange=e=>{const el=e.target.closest('[data-feed-pick]');if(!el)return;if(el.checked&&state.feedSelected.size>=5){el.checked=false;toast('每次最多选择 5 篇。',true);return;}if(el.checked)state.feedSelected.add(el.dataset.feedPick);else state.feedSelected.delete(el.dataset.feedPick);renderFeedQueue();};
async function startSelected(urls){if(!approveNavigation())return;const r=await request('/api/autofeed/links',{urls});state.feedSelected.clear();await refreshFeed();if(urls.length===1){$('feedDialog').close();await openJob(r.id);}if(r.message)toast(r.message);}
$('feedSelected').onclick=guarded(()=>startSelected([...state.feedSelected]));
$('feedFinish').onclick=guarded(async()=>{await request('/api/autofeed/finish-current',{});await refreshFeed();});
$('feedResume').onclick=guarded(async()=>{if(!approveNavigation())return;await request('/api/autofeed/resume',{});await refreshFeed();});
$('openAutoFeed').onclick=guarded(async()=>{await refreshFeed(true);$('feedDialog').showModal();await refreshFeed();});
$('feedDialog').addEventListener('close',()=>clearTimeout(feedTimer));
$('feedForm').onsubmit=guarded(async e=>{e.preventDefault();await request('/api/autofeed/config',{enabled:$('feedEnabled').checked,max_daily:Number($('feedMax').value),style:$('feedStyle').value,vision:$('feedVision').checked,model_profile:$('feedModel').value,sources:$('feedAlphaXiv').checked?['arxiv','alphaxiv']:['arxiv'],topics:[...document.querySelectorAll('[data-feed-topic]:checked')].map(e=>e.dataset.feedTopic)});await refreshFeed(true);toast('自动配置已保存。');});
$('feedDiscover').onclick=guarded(async()=>{await request('/api/autofeed/discover',{});await refreshFeed();});
$('feedRun').onclick=guarded(async()=>{await request('/api/autofeed/run',{});await refreshFeed();});
$('feedPause').onclick=guarded(async()=>{await request('/api/autofeed/pause',{});await refreshFeed(true);});
$('feedQueue').onclick=guarded(async e=>{const direct=e.target.closest('[data-feed-direct]');if(direct){await startSelected([direct.dataset.feedDirect]);return;}const b=e.target.closest('[data-feed-job]');if(!b||!approveNavigation())return;$('feedDialog').close();await openJob(b.dataset.feedJob);});
// Xiaohongshu is a separate, explicitly published channel. Opening or polling it never publishes.
function xhsTags(){return $('xhsTags').value.split(/[\s,#，；;]+/u).map(t=>t.trim()).filter(Boolean);}
function collectXhsNote(){return {title:$('xhsTitle').value.trim(),content:$('xhsContent').value.trim(),tags:xhsTags(),visibility:$('xhsVisibility').value,image_ids:[...(state.xhs.note?.image_ids||[])]};}
function xhsBlockedReceipt(){return ['publishing','published','submitted','submitted_unverified','outcome_unknown','unknown'].includes(state.xhs.receipt?.state);}
function xhsHasAccount(){const c=state.xhs.connection;return Boolean(!c?.blocked&&c?.running&&c.connected!==false&&c.logged_in&&c.account?.id);}
function xhsSourceChanged(){return state.dirty||state.metadataDirty||state.figuresDirty||state.xhs.stale;}
function xhsValidNote(){const n=state.xhs.note;if(!n)return false;const p=collectXhsNote(),count=Array.from(p.content+(p.tags.length?'\n\n'+p.tags.map(t=>'#'+t).join(' '):'')).length;return Boolean(p.title&&p.content&&Array.from(p.title).length<=20&&count<=1000&&p.tags.length<=10&&new Set(p.tags).size===p.tags.length&&p.tags.every(t=>/^[\w\u3400-\u9fff-]{1,20}$/u.test(t))&&p.image_ids.length>0&&p.image_ids.length<=9);}
function xhsNoteAsset(image){return typeof image==='string'&&/^xhs\/[a-zA-Z0-9_-]+\.png$/.test(image)?`/api/jobs/${state.active}/asset?name=${encodeURIComponent(image)}`:'';}
function updateXhsButtons(){
  const x=state.xhs;if(!x||!$('xhsPrepare'))return;
  const occupied=state.busy||state.preparingDelivery||x.working,locked=xhsBlockedReceipt(),valid=xhsValidNote();
  $('xhsPrepare').disabled=occupied||!state.article||locked;
  $('xhsPublish').disabled=occupied||!valid||!xhsHasAccount()||locked||xhsSourceChanged()||!x.checked;
  $('xhsPublish').setAttribute('aria-busy',Boolean(occupied&&x.receipt?.state==='publishing'));
  $('xhsSave').disabled=occupied||!x.note||!x.dirty||locked;
  $('xhsExport').disabled=occupied||!x.note||!valid;
  document.querySelectorAll('#xhsEditor input,#xhsEditor textarea,#xhsEditor button,#xhsVisibility').forEach(e=>e.disabled=occupied||locked||!x.note);
  document.querySelectorAll('[data-xhs-move]').forEach(e=>{const ids=x.note?.image_ids||[],i=ids.indexOf(e.dataset.xhsMove),next=i+Number(e.dataset.direction);e.disabled=occupied||locked||i<0||next<0||next>=ids.length;});
  $('xhsSave').disabled=occupied||!x.note||!x.dirty||locked;
  ['xhsConnect','xhsLogin','xhsCheck','xhsStop','xhsLoginCheck'].forEach(id=>$(id).disabled=x.connectionWorking||x.receipt?.state==='publishing');
  if(x.connection?.blocked)['xhsConnect','xhsLogin','xhsCheck','xhsLoginCheck'].forEach(id=>$(id).disabled=true);
  $('xhsLogin').disabled=$('xhsLogin').disabled||!x.connection?.running||!x.connection?.connected;
  $('xhsStop').disabled=$('xhsStop').disabled||!x.connection?.running;
  $('xhsReset').disabled=occupied;
  $('xhsPublishHint').textContent=locked?'本次发布已有回执。请先核对后台，不会自动重发。':xhsSourceChanged()?'原文或配图已改变，请重新设计笔记，避免发布旧版本。':!x.note?'先完成笔记设计；文章原稿会保留。':!valid?'请检查标题与正文长度、最多 10 个不重复话题，并选择 1–9 张图卡。':!xhsHasAccount()?'请连接并检查小红书账号，确认当前发布身份。':'点击后保存笔记修改，并向上方显示的账号提交这篇图文笔记。';
  if(x.connection?.blocked)$('xhsPublishHint').textContent='自动发布已因连接器安全告警暂停。可继续设计并下载图文包，到官方创作平台发布。';
  $('xhsPreparationHint').textContent=!state.article?'先在第 01 节完成文章。这里独立保存笔记，不改变公众号原稿。':locked?'本篇已有发布回执，保留当前笔记供核对或导出。':x.note?'重新设计会更新笔记文字和图卡，公众号原稿保持不变。':'自动提炼已保存文章，并用可用的论文原图制作 3:4 图卡。';
}
function resetXhsView(){
  stopXhsConnectionPolling();
  const connection=state.xhs.connection,connectionWorking=state.xhs.connectionWorking;
  state.xhs={note:null,receipt:null,dirty:false,working:false,connectionWorking,connection,slide:0,stale:false,checked:false};
  ['xhsTitle','xhsContent','xhsTags'].forEach(id=>$(id).value='');$('xhsVisibility').value='公开可见';
  $('xhsEditor').hidden=true;$('xhsCards').innerHTML='';$('xhsWarnings').hidden=true;$('xhsWarnings').innerHTML='';
  renderXhsPreview();renderXhsReceipt();updateXhsButtons();
}
async function refreshXhs(force=false){
  const id=state.active;if(!id)return;
  const r=await request(`/api/jobs/${id}/xhs-note`);if(id!==state.active)return;
  const x=state.xhs;x.receipt=r.receipt||null;x.stale=Boolean(r.stale);x.checked=true;
  if(!x.dirty||force){x.note=r.note||null;x.dirty=false;fillXhsFields();}
  const warnings=[...(x.note?.warnings||[]),...(r.stale?[r.message||'原稿或素材已有更新，请重新设计笔记。']:[])];
  $('xhsWarnings').hidden=!warnings.length;$('xhsWarnings').innerHTML=warnings.length?'<strong>编辑提示</strong>'+warnings.map(w=>`<p>${escapeHTML(typeof w==='string'?w:w.message||'请核对笔记素材。')}</p>`).join(''):'';
  renderXhsReceipt();updateXhsButtons();
}
function fillXhsFields(){
  const n=state.xhs.note;$('xhsEditor').hidden=!n;
  $('xhsTitle').value=n?.title||'';$('xhsContent').value=n?.content||'';$('xhsTags').value=(n?.tags||[]).join(' ');$('xhsVisibility').value=n?.visibility||'公开可见';
  $('xhsSaveState').textContent='已保存';$('xhsSaveState').classList.remove('unsaved');
  renderXhsCards();renderXhsPreview();
}
function markXhsDirty(){state.xhs.dirty=true;$('xhsSaveState').textContent='有未保存修改';$('xhsSaveState').classList.add('unsaved');renderXhsPreview();updateXhsButtons();}
function selectedXhsCards(){const n=state.xhs.note;return (n?.image_ids||[]).map(id=>(n.cards||[]).find(c=>c.id===id)).filter(Boolean);}
function renderXhsCards(){
  const n=state.xhs.note;if(!n){$('xhsCards').innerHTML='';return;}
  const chosen=new Set(n.image_ids||[]),ordered=[...selectedXhsCards(),...(n.cards||[]).filter(c=>!chosen.has(c.id))];
  $('xhsCards').innerHTML=ordered.map(c=>{const index=(n.image_ids||[]).indexOf(c.id),sel=index>=0;return `<article class="xhs-image-card ${sel?'selected':''}" data-xhs-card="${escapeHTML(c.id)}"><button class="xhs-thumbnail" data-xhs-view="${escapeHTML(c.id)}" aria-label="预览${escapeHTML(c.title||c.kind||'图卡')}"><img src="${escapeHTML(xhsNoteAsset(c.image))}" alt="${escapeHTML(c.caption||c.title||'论文图卡')}" loading="lazy"></button><p title="${escapeHTML(c.caption||c.title||'')}">${sel?String(index+1).padStart(2,'0')+' · ':''}${escapeHTML(c.title||({cover:'研究封面',overview:'方法概览',figure:'论文原图',summary:'研究摘要'}[c.kind])||'论文图卡')}</p><div class="xhs-card-tools"><label><input type="checkbox" data-xhs-select="${escapeHTML(c.id)}" ${sel?'checked':''}>${index===0?'封面':'选用'}</label><button data-xhs-move="${escapeHTML(c.id)}" data-direction="-1" aria-label="将图卡前移" ${index<=0?'disabled':''}>←</button><button data-xhs-move="${escapeHTML(c.id)}" data-direction="1" aria-label="将图卡后移" ${index<0||index>=n.image_ids.length-1?'disabled':''}>→</button></div></article>`;}).join('');
  $('xhsImageCount').textContent=`${n.image_ids.length} / 9`;
}
function renderXhsPreview(){
  const x=state.xhs,n=x.note,p=n?collectXhsNote():null,cards=selectedXhsCards();
  const count=p?Array.from(p.content+(p.tags.length?'\n\n'+p.tags.map(t=>'#'+t).join(' '):'')).length:0;
  $('xhsTitleCount').textContent=`${Array.from(p?.title||'').length} / 20`;$('xhsTitleCount').classList.toggle('field-over-limit',Array.from(p?.title||'').length>20);
  $('xhsContentCount').textContent=`${count.toLocaleString()} / 1,000 · 含话题`;$('xhsContentCount').classList.toggle('field-over-limit',count>1000);
  const privatePost=$('xhsVisibility').value==='仅自己可见';$('xhsVisibilityNotice').textContent=privatePost?'会发布为仅自己可见的笔记，不是草稿。':'会公开发布到当前小红书账号，不是草稿。';
  $('xhsPreviewTitle').textContent=p?.title||'值得被看见的，不止是结论。';$('xhsPreviewContent').textContent=p?.content||'把研究问题、方法与证据，放进可以慢慢翻阅的图文笔记里。';$('xhsPreviewTags').textContent=p?p.tags.map(t=>'#'+t).join(' '):'#论文研读 #研究分享';$('xhsPreviewEditor').textContent='编辑 '+(n?.editor||'Paper Studio');
  x.slide=Math.max(0,Math.min(x.slide,cards.length-1));const card=cards[x.slide];
  $('xhsPreviewEmpty').hidden=Boolean(card);$('xhsPreviewImage').hidden=!card;if(card)$('xhsPreviewImage').src=xhsNoteAsset(card.image);else $('xhsPreviewImage').removeAttribute('src');
  $('xhsSlideCount').hidden=!card;$('xhsSlideCount').textContent=card?`${x.slide+1} / ${cards.length}`:'';
  $('xhsPrev').hidden=cards.length<2;$('xhsNext').hidden=cards.length<2;$('xhsPrev').disabled=x.slide===0;$('xhsNext').disabled=x.slide===cards.length-1;
  $('xhsDots').innerHTML=cards.map((c,i)=>`<button data-xhs-slide="${i}" class="${i===x.slide?'active':''}" aria-label="预览第 ${i+1} 张图卡" aria-current="${i===x.slide?'true':'false'}"></button>`).join('');
  document.querySelectorAll('[data-xhs-card]').forEach(el=>el.classList.toggle('is-preview',el.dataset.xhsCard===card?.id));
}
function renderXhsReceipt(){
  const r=state.xhs.receipt||{},labels={publishing:'正在发布',published:'已核对笔记',submitted:'已提交',submitted_unverified:'已提交 · 待核对',outcome_unknown:'结果待核对',unknown:'结果待核对',failed:'发布未完成',reset:'已允许重试'};
  $('xhsReceiptState').textContent=labels[r.state]||'尚未发布';
  $('xhsReceipt').textContent=(r.message||'每次发布都会保留回执。结果不明时先在小红书后台核对，避免重复发布。')+(r.note_id?'\n笔记编号：'+r.note_id:'');
  let safeUrl='';try{const u=new URL(r.url);if(u.protocol==='https:'&&(u.hostname==='xiaohongshu.com'||u.hostname.endsWith('.xiaohongshu.com')||u.hostname==='xhslink.com'||u.hostname.endsWith('.xhslink.com')))safeUrl=u.href;}catch{}
  $('xhsPublishedLink').hidden=!safeUrl;if(safeUrl)$('xhsPublishedLink').href=safeUrl;else $('xhsPublishedLink').removeAttribute('href');
  $('xhsReset').hidden=!['outcome_unknown','unknown','submitted_unverified','submitted','failed'].includes(r.state);
}
async function saveXhsNote(showToast=true){
  const id=state.active;if(!id||!state.xhs.note)return;
  if(!xhsValidNote())throw new Error('请检查：标题不超过 20 个字符，正文与话题合计不超过 1,000 个字符，话题最多 10 个，每个不超过 20 字且不含特殊标点，并选择 1–9 张图卡。');
  await request(`/api/jobs/${id}/xhs-note`,collectXhsNote());if(id!==state.active)return;
  state.xhs.dirty=false;await refreshXhs(true);if(showToast)toast('小红书笔记已保存，公众号原稿保持不变。');
}
async function runXhsAction(action){
  if(state.busy||state.preparingDelivery||state.xhs.working)return;
  if(!state.active||!state.article)throw new Error('请先生成或导入完整文章。');
  const id=state.active,x=state.xhs;
  if(action==='prepare'&&x.note&&!confirm('重新设计将替换当前小红书笔记文字与图卡。公众号原稿不变，是否继续？'))return;
  if(action==='publish'&&xhsSourceChanged())throw new Error('原文或配图已改变，请重新设计笔记后发布。');
  if(action==='publish'&&!xhsHasAccount())throw new Error('请先检查小红书连接，确认发布账号。');
  const accountId=action==='publish'?x.connection.account.id:null;
  x.working=true;busy(true);let queued=false;
  try{
    if(action==='prepare'){
      if(state.figuresDirty)await saveFigures(false);
      if(state.dirty)await saveDraft(false);else await savePaperInfo();
    }else if(x.dirty)await saveXhsNote(false);
    if(id!==state.active)return;
    await request(`/api/jobs/${id}/xhs-${action}`,action==='publish'?{publish:true,account_id:accountId}:{});
    x.dirty=false;x.checked=false;queued=true;
  }finally{x.working=false;if(!queued)busy(false);updateXhsButtons();}
  if(queued&&id===state.active)await watchJob(id);
}
function renderXhsConnection(){
  const c=state.xhs.connection||{},logged=Boolean(c.connected&&c.logged_in&&c.account?.id),name=c.account?.name||'';
  $('xhsConnectionTitle').textContent=logged?(name||'已连接小红书账号'):c.running?'本机连接已启动':'连接你的小红书账号';
  $('xhsConnectionMessage').textContent=c.message||(logged?'请核对当前账号。点击发布后，笔记将提交到此账号。':c.installed===false?'尚未安装本机发布组件。请展开下方连接说明完成安装。':'启动本机组件后，使用小红书 App 扫码登录。');
  $('xhsConnectionStatus').textContent=logged?'已登录':c.running?(c.connected?'等待登录':'正在准备浏览器'):c.installed===false?'需安装组件':'未连接';$('xhsConnectionStatus').className='status-chip '+(logged?'is-ready':'is-pending');
  $('xhsConnect').textContent=c.running?'刷新连接':'启动连接';$('xhsLogin').textContent=logged?'重新扫码':'扫码登录';
  $('xhsPublishTarget').textContent=logged?'当前账号：'+(name||'已核对账号')+(c.account?.id?' · '+c.account.id:''):'登录并检查后显示当前发布账号。';$('xhsPreviewAccount').textContent=name||'你的研究笔记';
  if(c.blocked){
    $('xhsConnectionTitle').textContent='自动连接已暂停';
    $('xhsConnectionStatus').textContent='组件安全告警';
    $('xhsConnect').textContent='等待组件更新';
    $('xhsPublishTarget').textContent='本地设计和导出可用；自动登录与发布暂不可用。';
    $('xhsConnectionHelp').open=true;
    $('xhsQr').removeAttribute('src');
    if($('xhsLoginDialog').open)$('xhsLoginDialog').close();
  }
  if(c.installed===false)$('xhsConnectionHelp').open=true;
  updateXhsButtons();
}
async function refreshXhsConnection(){
  const c=await request('/api/xhs/status');state.xhs.connection=c;renderXhsConnection();scheduleXhsConnectionPolling();
}
function stopXhsConnectionPolling(){clearTimeout(xhsConnectionTimer);xhsConnectionPollUntil=0;}
function scheduleXhsConnectionPolling(){
  clearTimeout(xhsConnectionTimer);
  const c=state.xhs.connection;
  if(!state.active||$('tab-xhs').hidden||c?.blocked||!c?.running||c.connected)return;
  if(!xhsConnectionPollUntil)xhsConnectionPollUntil=Date.now()+300000;
  if(Date.now()>=xhsConnectionPollUntil){$('xhsConnectionMessage').textContent='本机组件仍未就绪，自动检测已暂停。可稍后点击“检查连接”；首次启动可能正在下载浏览器。';return;}
  xhsConnectionTimer=setTimeout(async()=>{
    if(!state.active||$('tab-xhs').hidden)return;
    if(state.xhs.connectionWorking){scheduleXhsConnectionPolling();return;}
    try{await refreshXhsConnection();}catch{scheduleXhsConnectionPolling();}
  },3000);
}
async function xhsConnectionAction(action){
  if(state.xhs.connectionWorking)return;
  if(state.xhs.connection?.blocked&&action!=='stop'){toast(state.xhs.connection.message,true);return;}
  clearTimeout(xhsConnectionTimer);if(action==='start')xhsConnectionPollUntil=Date.now()+300000;if(action==='stop')stopXhsConnectionPolling();
  state.xhs.connectionWorking=true;updateXhsButtons();
  try{
    $('xhsConnectionMessage').textContent=action==='login'?'正在准备登录二维码…':action==='check'?'正在核对当前登录账号…':'正在处理本机连接…';
    const r=await request('/api/xhs/'+action,{});
    if(action==='login'){
      const raw=r.img||r.data?.img||'',img=typeof raw==='string'&&/^data:image\/(png|jpe?g|webp);base64,[A-Za-z0-9+/=\r\n]+$/.test(raw)?raw:typeof raw==='string'&&raw.length>100&&/^[A-Za-z0-9+/=\r\n]+$/.test(raw)?'data:image/png;base64,'+raw:'';
      if(img){$('xhsQr').src=img;$('xhsQrMessage').textContent='请在手机上确认登录，再点击下方按钮检查状态。二维码过期后可重新扫码。';$('xhsLoginDialog').showModal();}
      else if(!r.is_logged_in&&!r.logged_in)toast(r.message||'暂未取得二维码，请稍后重新扫码。',true);
    }
    await refreshXhsConnection();
    if(action==='check'&&xhsHasAccount()){if($('xhsLoginDialog').open)$('xhsLoginDialog').close();toast('已核对小红书账号，请确认上方发布身份。');}
    else if(action==='check')toast(r.message||'尚未确认登录，请先完成手机扫码。');
  }finally{state.xhs.connectionWorking=false;updateXhsButtons();scheduleXhsConnectionPolling();}
}
function wireXhs(){
  for(const event of ['input','change'])document.addEventListener(event,e=>{if(state.xhs.note&&e.target.closest('#draftEditor,#paperInfoPanel,#figureGallery,#projectVideos')){state.xhs.stale=true;state.xhs.checked=false;updateXhsButtons();}});
  $('xhsPrepare').onclick=guarded(()=>runXhsAction('prepare'));$('xhsPublish').onclick=guarded(()=>runXhsAction('publish'));
  $('xhsSave').onclick=guarded(async()=>{if(state.xhs.working||state.busy)return;state.xhs.working=true;busy(true);try{await saveXhsNote();}finally{state.xhs.working=false;busy(false);}});
  ['xhsTitle','xhsContent','xhsTags'].forEach(id=>$(id).addEventListener('input',markXhsDirty));$('xhsVisibility').onchange=markXhsDirty;
  $('xhsCards').onchange=e=>{const b=e.target.closest('[data-xhs-select]');if(!b)return;const n=state.xhs.note,id=b.dataset.xhsSelect;if(b.checked){if(n.image_ids.length>=9){b.checked=false;toast('每篇笔记最多选择 9 张图卡。',true);return;}if(!n.image_ids.includes(id))n.image_ids.push(id);}else n.image_ids=n.image_ids.filter(v=>v!==id);markXhsDirty();renderXhsCards();renderXhsPreview();updateXhsButtons();};
  $('xhsCards').onclick=e=>{const view=e.target.closest('[data-xhs-view]');if(view){const i=state.xhs.note.image_ids.indexOf(view.dataset.xhsView);if(i>=0){state.xhs.slide=i;renderXhsPreview();}else toast('先勾选这张图卡，即可加入笔记预览。');return;}const b=e.target.closest('[data-xhs-move]');if(!b)return;const ids=state.xhs.note.image_ids,i=ids.indexOf(b.dataset.xhsMove),next=i+Number(b.dataset.direction);if(i<0||next<0||next>=ids.length)return;[ids[i],ids[next]]=[ids[next],ids[i]];state.xhs.slide=next;markXhsDirty();renderXhsCards();renderXhsPreview();updateXhsButtons();};
  $('xhsPrev').onclick=()=>{state.xhs.slide--;renderXhsPreview();};$('xhsNext').onclick=()=>{state.xhs.slide++;renderXhsPreview();};$('xhsDots').onclick=e=>{const b=e.target.closest('[data-xhs-slide]');if(b){state.xhs.slide=Number(b.dataset.xhsSlide);renderXhsPreview();}};
  $('xhsConnect').onclick=guarded(()=>xhsConnectionAction(state.xhs.connection?.running?'check':'start'));$('xhsLogin').onclick=guarded(()=>xhsConnectionAction('login'));$('xhsCheck').onclick=guarded(()=>xhsConnectionAction('check'));$('xhsLoginCheck').onclick=guarded(()=>xhsConnectionAction('check'));$('xhsStop').onclick=guarded(()=>xhsConnectionAction('stop'));
  $('xhsLoginDialog').addEventListener('close',()=>{$('xhsQr').removeAttribute('src');});
  $('xhsReset').onclick=guarded(async()=>{if(state.busy||state.xhs.working)return;if(!confirm('请先在小红书后台确认本次笔记没有发布成功。允许重试可能创建重复笔记；确定已核对并重新发布吗？'))return;state.xhs.working=true;busy(true);try{await request(endpoint('xhs-reset'),{confirmed:true});await refreshXhs(true);toast('已重置本次回执。只有再次点击“发布到小红书”才会发送。');}finally{state.xhs.working=false;busy(false);}});
  $('xhsExport').onclick=guarded(async()=>{const id=state.active;if(!id||!state.xhs.note||state.busy||state.xhs.working)return;state.xhs.working=true;busy(true);try{if(state.xhs.dirty)await saveXhsNote(false);if(id!==state.active)return;if(state.desktop){const r=await request('/api/desktop/action',{action:'export',id,format:'xhs'});if(!r.cancelled)toast('图文包已保存：'+r.name);}else{const a=document.createElement('a');a.href=`/api/jobs/${id}/xhs-export`;a.download='xiaohongshu-note.zip';a.click();}}finally{state.xhs.working=false;busy(false);}});
}

wireXhs();
