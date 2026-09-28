(function () {
  'use strict';
  if (window.AfterClass) return;
  const E = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const date = (offset=0) => { const d=new Date(Date.now()+offset*86400000); return new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(d); };
  const S={user:null,tab:'todo',from:date(-14),to:date(),search:'',student:'',filter:'all',report:null,invites:[],scheduler:null,open:null,busy:false,serial:0};
  const labels={attended:'已上课',unknown:'出勤待核对',makeup_pending:'请假待补课',leave:'请假',cancelled:'已取消',content:'上课内容',homework:'家庭作业',notify_updated:'已发送，内容有更新',pending:'待家长确认',accepted:'家长已确认',declined:'不参加',alternative:'希望调整时间',published:'已发布',draft:'草稿',open:'待处理',resolved:'已回复',reply_pending:'微信回复待发送',website:'网站',wechat:'微信'};
  const stages={check:'出勤待核对',content:'待补课堂内容',homework:'待补家庭作业',notify:'待发家长通知',notify_updated:'已发送，内容有更新需重发',feedback:'家长反馈待处理',awaiting_feedback:'暂无已记录反馈',done:'反馈已处理',makeup:'请假待补课',cancelled:'已取消',scheduled:'已安排待上课'};
  const $=id=>document.getElementById(id);
  async function api(path,body){
    const token=localStorage.getItem('authToken');
    if(!token)throw Error('请先从登录页进入');
    const r=await fetch('/api/'+path,{method:body?'POST':'GET',cache:'no-store',headers:{Authorization:'Bearer '+token,...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
    let x;try{x=await r.json();}catch(e){throw Error('服务暂不可用，请稍后重试');}
    if(!r.ok)throw Error(x.error||'请求失败');return x;
  }
  function button(text,action,id='',extra=''){return '<button type="button" class="ac-btn" data-action="'+action+'" data-id="'+E(id)+'" '+extra+'>'+E(text)+'</button>';}
  function badge(text,kind=''){return '<span class="ac-badge '+kind+'">'+E(text)+'</span>';}
  function shell(){
    if($('ac-layer'))return;
    const layer=document.createElement('div');layer.id='ac-layer';layer.className='ac-layer';layer.hidden=true;
    layer.innerHTML='<section class="ac-panel" role="dialog" aria-modal="true" aria-labelledby="ac-title"><header class="ac-head"><h2 id="ac-title">课后跟进</h2><button type="button" class="ac-close" id="ac-close" aria-label="关闭">×</button></header><main class="ac-main"><p id="ac-status" role="status"></p><div id="ac-body"></div></main></section>';
    document.body.append(layer);$('ac-close').onclick=close;layer.addEventListener('click',e=>{if(e.target===layer)close();});
    layer.addEventListener('keydown',e=>{
      if(e.key==='Escape')close();
      if(e.key==='Tab'){const all=[...layer.querySelectorAll('button,input,select,textarea,a')].filter(x=>!x.disabled&&x.getClientRects().length);const i=all.indexOf(document.activeElement);if(e.shiftKey&&i<=0){e.preventDefault();all.at(-1)?.focus();}else if(!e.shiftKey&&i===all.length-1){e.preventDefault();all[0]?.focus();}}
    });
    $('ac-body').addEventListener('click',event=>{const b=event.target.closest('[data-action]');if(b)handle(b.dataset.action,b.dataset.id);});
  }
  function close(){if(S.busy)return;$('ac-layer').hidden=true;S.launch?.focus();}
  async function open(tab){shell();$('ac-body').innerHTML='';S.launch=document.activeElement;S.tab=tab||'todo';$('ac-layer').hidden=false;$('ac-close').focus();await load();}
  async function load(){
    const serial=++S.serial;$('ac-status').textContent='正在读取…';
    try{
      S.user=await api('auth/me');
      const context=window.AppSessionContext;
      if(context && (context.role!==S.user.role || context.role==='student' && context.username.toLowerCase()!==String(S.user.username).toLowerCase()))throw Error('登录身份已切换，请重新登录后打开课程确认');
      const teacher=S.user.role==='teacher';
      const results=await Promise.all([api('after-class/'+(teacher?'dashboard':'parent')+'?from='+S.from+'&to='+S.to),api('after-class/invitations'),teacher?api('after-class/scheduler'):Promise.resolve(null)]);
      if(serial!==S.serial)return;
      S.report=results[0];S.invites=results[1].invitations;S.scheduler=results[2];
      if(!teacher&&!['invites','feedback'].includes(S.tab))S.tab='invites';
      $('ac-status').textContent='';render();
    }catch(e){$('ac-status').textContent=e.message;}
  }
  async function save(body){
    if(S.busy)return;S.busy=true;
    const controls=[...$('ac-body').querySelectorAll('button,input,select,textarea')].map(x=>[x,x.disabled]);controls.forEach(([x])=>x.disabled=true);
    try{await api('after-class/action',{from:S.from,to:S.to,...body});await load();if(['invite_cancel','invite_reply','plan_makeup','unlink_makeup','teacher_leave','clear_session_time','cancel_class'].includes(body.action))window.dispatchEvent(new Event('after-class-schedule-changed'));return true;}
    catch(e){$('ac-status').textContent='保存失败：'+e.message;$('ac-status').scrollIntoView({block:'nearest'});return false;}
    finally{S.busy=false;controls.forEach(([x,v])=>{if(x.isConnected)x.disabled=v;});}
  }
  const row=id=>S.report.rows.find(x=>x.id===id);
  const recordBody=(r,action,extra={})=>({action,itemId:r.id,version:r.stateVersion,sourceVersion:r.sourceVersion,...extra});
  function rows(){
    const q=S.search.toLowerCase();return S.report.rows.filter(r=>(!S.student||r.studentId===S.student)&&(!q||(r.studentName+' '+r.course+' '+r.title).toLowerCase().includes(q))&&(S.filter==='all'||S.filter===r.stage||S.filter==='missing'&&['content','homework'].includes(r.stage)));
  }
  function visibleRows(){const list=rows();return S.tab==='wechat'?list.filter(r=>r.stage==='notify'):list;}
  function kpiRows(){
    const list=visibleRows();
    const groups=[['missing','待补课堂内容/家庭作业'],['notify','待发家长通知'],['feedback','家长反馈待处理'],['check','出勤待核对'],['makeup','请假待补课']];
    return '<div class="ac-kpis" id="ac-kpis">'+groups.map(([key,label])=>{const matched=list.filter(r=>key==='missing'?['content','homework'].includes(r.stage):key==='notify'?['notify','notify_updated'].includes(r.stage):r.stage===key);return '<div class="ac-kpi"><strong>'+new Set(matched.map(r=>r.sessionId)).size+' 班次 · '+new Set(matched.map(r=>r.studentId)).size+' 位</strong><span>'+label+'</span></div>';}).join('')+'</div>';
  }
  function render(){
    const teacher=S.user.role==='teacher';$('ac-title').textContent=teacher?'课后跟进与加课安排':'课程确认与家长反馈';
    const tabs=teacher?[['todo','课后待办'],['wechat','待发家长通知'],['feedback','家长反馈'],['smart','智能补课与假期排课'],['invites','排课邀请'],['contacts','微信备注']]:[['invites','课程确认'],['feedback','作业反馈']];
    const header='<nav class="ac-tabs">'+tabs.map(([k,v])=>'<button class="ac-tab '+(S.tab===k?'active':'')+'" data-action="tab" data-id="'+k+'">'+v+'</button>').join('')+'</nav>';
    const dates='<div class="ac-tools"><label>从 <input id="ac-from" type="date" value="'+S.from+'"></label><label>到 <input id="ac-to" type="date" value="'+S.to+'"></label>'+button('刷新','refresh')+'</div>';
    $('ac-body').innerHTML=header+(S.tab==='smart'?smartScheduler():S.tab==='invites'?invitations():dates+(S.tab==='contacts'?contacts():S.tab==='feedback'?feedback():teacherRows()));
    if($('ac-search'))$('ac-search').oninput=e=>{S.search=e.target.value;drawRows();};
    for(const id of ['ac-student','ac-filter'])if($(id))$(id).onchange=e=>{S[id==='ac-student'?'student':'filter']=e.target.value;drawRows();};
    if($('invite-course'))$('invite-course').onchange=drawInviteStudents;
    if($('invite-kind'))$('invite-kind').onchange=drawInviteStudents;
    if($('invite-all'))$('invite-all').onchange=e=>document.querySelectorAll('[data-recipient]').forEach(x=>x.checked=e.target.checked);
    if($('invite-course'))drawInviteStudents();
  }
  function teacherRows(){
    const filters='<div class="ac-tools"><select id="ac-student" aria-label="学员"><option value="">全部学员</option>'+S.report.students.map(s=>'<option value="'+E(s.id)+'" '+(S.student===s.id?'selected':'')+'>'+E(s.name)+'</option>').join('')+'</select><input id="ac-search" type="search" placeholder="搜索课程、学员或课题" value="'+E(S.search)+'"><select id="ac-filter" aria-label="跟进状态">'+[['all','全部状态'],['missing','待补课堂内容/家庭作业'],...Object.entries(stages)].map(([k,v])=>'<option value="'+k+'" '+(S.filter===k?'selected':'')+'>'+v+'</option>').join('')+'</select></div>';
    return filters+kpiRows()+'<div id="ac-rows">'+groupedRows()+'</div>';
  }
  function drawRows(){$('ac-kpis').outerHTML=kpiRows();$('ac-rows').innerHTML=groupedRows();}
  function groupedRows(){
    const list=visibleRows();
    const groups=new Map();for(const r of list){if(!groups.has(r.sessionId))groups.set(r.sessionId,[]);groups.get(r.sessionId).push(r);}
    return [...groups.values()].map(group=>'<section class="ac-session"><h3>'+E(group[0].date+' '+group[0].time+' · '+group[0].course)+'</h3><p class="ac-meta">当前筛选 '+group.length+' 位 · 已上课 '+group.filter(r=>r.attendance==='attended').length+' 位</p>'+group.map(rowView).join('')+'</section>').join('')||'<p class="ac-empty">没有匹配的课次</p>';
  }
  function rowView(r){
    const missing=r.missing.map(k=>labels[k]).join('、');
    const step=(ok,label,wait)=>'<span class="ac-step '+(ok?'done':'pending')+'">'+(ok?'✓ ':'○ ')+label+(ok?'':'：'+wait)+'</span>';
    const feedbackState=r.openFeedbackCount?'已收到 '+r.openFeedbackCount+' 条待处理':r.feedbackCount?'已收到并处理':'暂无已记录反馈';
    const leaveState=r.leaveRequestCount?(r.leaveRequests.some(x=>!x.handled)?'有待处理':'已处理'):'无记录';
    const makeupMeta=r.makeupSession?'<p class="ac-makeup-meta">补课已完成 · '+E(r.makeupSession.date+' '+r.makeupSession.time)+'</p>':'';
    const notificationStep=r.sent?step(true,'微信通知',''):r.notificationChanged?'<span class="ac-step pending">△ 微信通知：已发送，内容有更新需重发</span>':step(false,'微信通知',r.ready?'待发送':'尚未到发送步骤');
    return '<article class="ac-row '+(missing?'over':'')+'"><h4>'+E(r.studentName)+' <small>'+E(S.report.contacts[r.studentId]||'')+'</small></h4><p class="ac-meta">'+E(r.title||'未填写课题')+'</p>'+makeupMeta+'<div class="ac-badges">'+badge(stages[r.stage]||r.stage,['content','homework','notify','notify_updated','feedback','check','makeup'].includes(r.stage)?'warn':'good')+(r.makeupTarget?badge('补课','good'):'')+'</div><div class="ac-steps">'+step(r.contentDone,'课堂内容','待补充')+step(r.homeworkDone,'家庭作业','待补充')+notificationStep+'<span class="ac-step '+(r.openFeedbackCount?'pending':r.feedbackCount?'done':'')+'">家长反馈：'+E(feedbackState)+'</span></div><p class="ac-next">请假/留言：'+E(leaveState)+'</p>'+button(S.open===r.id?'收起':'查看与处理','detail',r.id)+(S.open===r.id?detail(r):'')+'</article>';
  }
  function detail(r){
    const targets=S.report.rows.filter(t=>t.studentId===r.studentId&&t.course===r.course&&t.id!==r.id&&t.recordCount&&t.attendance==='attended'&&!t.needsCheck);
    const makeup=r.makeupSession?'<div class="ac-makeup-box"><b>本次为补课</b><p>'+E(r.makeupSession.date+' '+r.makeupSession.time)+'</p></div>':'';
    const teacherLeave=!r.leaveRequestCount&&!r.recordCount?'<div class="ac-form"><b>家长通过微信请假</b><textarea id="leave-reason-'+r.id+'" placeholder="请假原因（可选）"></textarea>'+button('登记请假并建立补课任务','teacher-leave',r.id)+'</div>':'';
    const timing=!r.makeupSession&&r.recordCount?'<div class="ac-form"><b>实际课次时间</b><div class="ac-tools"><label>上课日期<input id="session-day-'+r.id+'" type="date" value="'+E(r.date)+'"></label><label>上课时间段<input id="session-time-'+r.id+'" type="text" value="'+E(r.time)+'" placeholder="17:15-18:15"></label>'+button('保存实际课次时间','session-time',r.id)+'</div><small class="ac-help">用于这位学员的课后跟进、班次统计和通知日期；不会自动改动原始上课记录或扣课时。</small><label>出勤或请假状态<select id="att-'+r.id+'">'+['unknown','attended','makeup_pending','leave','cancelled'].map(x=>'<option value="'+x+'" '+(r.attendance===x?'selected':'')+'>'+labels[x]+'</option>').join('')+'</select></label>'+button('保存状态','attendance',r.id)+'</div>':'';
    return '<div class="ac-detail open">'+makeup+'<b>上课内容</b><pre>'+E(r.content||'尚未填写')+'</pre><b>家庭作业</b><pre>'+E(r.homework||(r.homeworkImages?'已附 '+r.homeworkImages+' 张作业图片':r.noHomework?'本课无作业':'尚未填写'))+'</pre>'+(r.notificationChanged?'<p class="ac-note">这条通知之前已确认发送，但课堂内容或作业后来有更新；请核对下方最新内容，重新发送后再确认。</p>':'')+(r.warning?'<p class="ac-note">'+E(r.warning)+'</p>':'')+button(r.recordCount?'补充已有上课记录':'新增真实上课记录','record',r.id)+teacherLeave+timing+(r.actualSession&&Object.keys(r.actualSession).length?button('撤销时间调整','clear-session',r.id):'')+(r.makeupTarget||r.plannedMakeup?button('撤销补课安排或解除关联','unlink',r.id):'')+(r.attendance==='attended'?'<label class="ac-check"><input id="nohw-'+r.id+'" type="checkbox" '+(r.noHomework?'checked':'')+'>本课无需家庭作业</label>'+button('保存作业状态','nohomework',r.id):'')+((r.attendance==='makeup_pending'||r.attendance==='leave')&&!r.makeupTarget?'<div class="ac-actions">'+button('直接安排补课','plan-direct',r.id)+button('邀请家长确认补课','makeup',r.id)+'</div>'+(r.plannedMakeup?'<p>计划补课：'+E(r.plannedMakeup.date+' '+r.plannedMakeup.time)+'</p>':'')+(targets.length?'<label>已完成的补课<select id="target-'+r.id+'">'+targets.map(t=>'<option value="'+t.id+'">'+E(t.date+' '+t.time+' '+t.title)+'</option>').join('')+'</select></label>'+button('关联已完成补课','link',r.id):''):'')+(r.ready?'<div class="ac-form"><label>给家长的通知<textarea readonly id="notice-'+r.id+'">'+E(notice(r))+'</textarea></label><div class="ac-actions">'+button('复制通知','copy',r.id)+button(r.notificationChanged?'确认更新后的通知已发送':'确认已在微信发送','sent',r.id)+'</div></div>':'')+feedbackForm(r.id,true)+'</div>';
  }
  function notice(r){return r.studentName+'家长您好，'+r.date+' '+r.course+'的课堂内容和家庭作业已整理好。\n\n上课内容：\n'+r.content+'\n\n家庭作业：\n'+(r.homework||(r.homeworkImages?'请在课堂记录中查看作业图片':'本课无需作业'))+'\n\n请登录查看：https://emilyenstudio.com/lesson.html';}
  function feedbackForm(id,teacher){return '<div class="ac-form"><label>'+ (teacher?'记录家长微信反馈':'反馈类型')+'<small class="ac-help">“已完成未提交”表示家长说孩子已经做完，但还没有在 Practice 页面提交；用于提醒老师查看或补交。</small><select id="category-'+id+'">'+['已完成未提交','不会做','提交失败','申请延期','其他'].map(x=>'<option>'+x+'</option>').join('')+'</select></label><textarea id="message-'+id+'" placeholder="填写反馈内容"></textarea>'+button(teacher?'保存微信反馈':'提交反馈','feedback-save',id)+'</div>';}
  function feedback(){
    const teacher=S.user.role==='teacher';
    const form=teacher?'':'<label>选择课次<select id="feedback-record">'+S.report.rows.map(r=>'<option value="'+r.id+'">'+E(r.date+' '+r.course+' '+r.title)+'</option>').join('')+'</select></label>'+(S.report.rows.length?feedbackForm('parent',false):'<p>该日期范围内暂无上课记录。</p>');
    return form+S.report.feedback.map(f=>'<article class="ac-feedback"><h3>'+E(f.studentName+' · '+f.course+' · '+f.date)+'</h3>'+badge(f.category)+badge(labels[f.status]||f.status)+badge(labels[f.source]||f.source)+'<p class="ac-text">'+E(f.message)+'</p>'+(f.reply?'<p class="ac-text"><b>老师回复：</b>'+E(f.reply)+'</p>':'')+(teacher?'<textarea id="reply-'+f.id+'" aria-label="回复">'+E(f.reply||'')+'</textarea><div class="ac-actions">'+button(f.source==='wechat'?'保存回复草稿':'发布回复','reply',f.id)+(f.reply&&f.source==='wechat'?button('复制回复','reply-copy',f.id)+button('确认微信回复已发送','reply-sent',f.id):'')+'</div>':'')+'</article>').join('')+(S.report.feedback.length?'':'<p class="ac-empty">暂无反馈</p>');
  }
  function contacts(){return S.report.students.map(s=>'<div class="ac-row"><label>'+E(s.name)+'<input id="contact-'+E(s.id)+'" value="'+E(S.report.contacts[s.id]||'')+'" placeholder="微信中的家长备注名"></label>'+button('保存','contact',s.id)+'</div>').join('');}
  function invitations(){
    const teacher=S.user.role==='teacher';
    return (teacher?button('新增加课邀请','invite-new'):'')+'<div id="invite-form"></div>'+S.invites.map(inv=>'<article class="ac-row"><h3>'+E(inv.course)+' · '+(inv.kind==='makeup'?'补课':'加课')+'</h3><p>'+E(inv.date+' '+inv.time)+' · '+inv.duration+' 小时</p>'+badge(labels[inv.status]||inv.status)+(inv.expired?badge('已过确认截止时间','warn'):'')+'<p class="ac-note">'+E(inv.chargeLabel)+'</p><p class="ac-text">'+E(inv.note)+'</p><p class="ac-meta">确认截止：'+E(inv.deadline.slice(0,16).replace('T',' '))+'</p>'+inv.responses.map(r=>'<p>'+E(teacher?(S.report.students.find(s=>s.id===r.studentId)?.name||r.studentId):'您的选择')+'：'+E(labels[r.status]||r.status)+(r.message?' · '+E(r.message):'')+'</p>').join('')+'<div class="ac-actions">'+(teacher?(inv.status==='draft'?button('发布到学员后台','invite-publish',inv.id):'')+(inv.status==='published'?button('复制微信邀请','invite-copy',inv.id):'')+(inv.status!=='cancelled'?button('取消邀请及关联排课','invite-cancel',inv.id):''):inv.status==='published'&&!inv.expired?button('确认参加','invite-accept',inv.id)+button('不参加','invite-decline',inv.id)+button('希望调整时间','invite-alternative',inv.id):'')+'</div><div id="invite-response-'+inv.id+'"></div></article>').join('')+(S.invites.length?'':'<p class="ac-empty">暂无加课邀请</p>');
  }
  function smartScheduler(){
    if(!S.scheduler)return '<p class="ac-empty">正在计算排课建议…</p>';
    const region=Object.values(S.scheduler.region||{}).join(' / ');
    const tasks=S.scheduler.tasks||[];
    return '<section class="ac-smart-head"><h3>建议优先安排</h3><p>'+E(region)+' · 可排时段 '+E(S.scheduler.allowedWindows.join('、'))+'</p><small>整班补课保留原课长；个人补课采用重点补讲时长。每次确认后会自动重新计算冲突和排序。</small></section>'+tasks.map((task,index)=>{
      const kind=task.kind==='individual_makeup'?'个人补课':'整班假期补课';
      const picks=task.candidates.slice(0,4).map((slot,i)=>'<div class="ac-slot"><div><b>'+(i===0?'⭐ ':'')+E(slot.date+' '+slot.time+'-'+endLabel(slot.time,slot.duration))+'</b><small>'+E(slot.reason)+'</small></div>'+button('选择','scheduler-pick',index+':'+i)+'</div>').join('');
      return '<article class="ac-row"><div class="ac-badges">'+badge(kind,task.kind==='individual_makeup'?'warn':'')+badge('候选 '+task.candidateCount+' 个')+'</div><h3>'+E(task.label)+'</h3><p class="ac-meta">原课 '+E(task.originDate+' '+task.originalTime)+' · 原课长 '+task.originalDuration+' 小时 · 本次建议 '+task.duration+' 小时</p><p class="ac-next">'+E(task.priorityReason)+'</p>'+(picks||'<p class="ac-empty">当前没有无冲突候选，请先调整已有排课。</p>')+'</article>';
    }).join('')+(tasks.length?'':'<p class="ac-empty">当前没有待安排的请假补课或受假期影响的班课。</p>');
  }
  function endLabel(start,duration){const [h,m]=start.split(':').map(Number),total=h*60+m+Math.round(duration*60);return String(Math.floor(total/60)).padStart(2,'0')+':'+String(total%60).padStart(2,'0');}
  function pickSchedule(code){
    const [ti,ci]=code.split(':').map(Number),task=S.scheduler.tasks[ti],slot=task&&task.candidates[ci];if(!task||!slot)return;
    S.tab='invites';render();
    const origin=task.kind==='individual_makeup'?S.report.rows.find(r=>r.id===Object.values(task.originIds)[0]):null;
    newInvite(origin);
    $('invite-course').value=task.course;$('invite-kind').value=task.kind==='individual_makeup'?'makeup':'extra';
    $('invite-date').value=slot.date;$('invite-time').value=slot.time;$('invite-duration').value=slot.duration;
    drawInviteStudents();
    task.studentIds.forEach(id=>{const box=document.querySelector('[data-recipient="'+CSS.escape(id)+'"]');if(box)box.checked=true;});
  }
  function newInvite(origin){
    const courses=[...new Set(S.report.students.flatMap(s=>s.courses))];S.inviteId=crypto.randomUUID();S.origin=origin||null;
    $('invite-form').innerHTML='<form class="ac-form" id="ac-invite-form"><h3>新建加课邀请</h3><label>课程<select id="invite-course">'+courses.map(c=>'<option '+(origin?.course===c?'selected':'')+'>'+E(c)+'</option>').join('')+'</select></label><label>类型<select id="invite-kind"><option value="extra">节假日/临时加课</option><option value="makeup" '+(origin?'selected':'')+'>请假补课</option></select></label><div class="ac-tools"><label>日期<input id="invite-date" type="date" min="'+date()+'" value="'+date(7)+'" required></label><label>开始时间<input id="invite-time" type="time" value="17:15" required></label><label>时长（小时）<input id="invite-duration" type="number" min="0.25" max="8" step="0.25" value="1" required></label></div><label>确认截止<input id="invite-deadline" type="datetime-local" value="'+date(6)+'T18:00" required></label><label><input id="invite-all" type="checkbox">全选当前课程学员</label><div id="invite-students" class="ac-student-pick"></div><label>给家长的说明<textarea id="invite-note"></textarea></label><p class="ac-note">完成上课并保存课次记录后扣1次课；邀请和确认阶段不扣课时。</p>'+button('保存草稿','invite-create')+'</form>';
    const holiday=document.createElement('label');holiday.textContent='节假日日期';const holidaySelect=document.createElement('select');holidaySelect.id='invite-holiday';
    const holidayDays=[...new Map((S.scheduler?.tasks||[]).flatMap(t=>t.candidates).map(x=>[x.date,[x.date,x.holidayName]])).values()];
    holidaySelect.innerHTML='<option value="">自行选择日期</option>'+holidayDays.filter(([d])=>d>=date()).map(([d,n])=>'<option value="'+d+'">'+d+' · '+E(n)+'</option>').join('');holiday.append(holidaySelect);$('ac-invite-form').prepend(holiday);
    holidaySelect.onchange=()=>{if(holidaySelect.value){$('invite-date').value=holidaySelect.value;const prior=new Date(holidaySelect.value+'T12:00:00+08:00');prior.setDate(prior.getDate()-1);$('invite-deadline').value=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(prior)+'T18:00';}};
    $('ac-invite-form').onsubmit=e=>e.preventDefault();$('invite-course').onchange=drawInviteStudents;$('invite-kind').onchange=drawInviteStudents;$('invite-all').onchange=e=>document.querySelectorAll('[data-recipient]').forEach(x=>x.checked=e.target.checked);drawInviteStudents();
  }
  function drawInviteStudents(){
    const course=$('invite-course').value;const makeup=$('invite-kind').value==='makeup';
    $('invite-students').innerHTML=S.report.students.filter(s=>s.courses.includes(course)).map(s=>{
      const origins=S.report.rows.filter(r=>r.studentId===s.id&&r.course===course&&['makeup_pending','leave'].includes(r.attendance)&&!r.makeupTarget);
      if(makeup&&!origins.length)return '';
      return '<div><label><input type="checkbox" data-recipient="'+E(s.id)+'" '+(S.origin?.studentId===s.id?'checked':'')+'>'+E(s.name)+'</label>'+(makeup?'<select aria-label="原请假课次" data-origin="'+E(s.id)+'">'+origins.map(r=>'<option value="'+r.id+'" '+(S.origin?.id===r.id?'selected':'')+'>'+E(r.date+' '+r.time)+'</option>').join('')+'</select>':'')+'</div>';
    }).join('')||'<p>当前没有可选学员，请先核对课程及请假记录。</p>';
  }
  async function copy(text){try{await navigator.clipboard.writeText(text);return true;}catch(e){$('ac-status').textContent='无法自动复制，请选中下方文字后手动复制。';const area=document.createElement('textarea');area.value=text;area.readOnly=true;area.style.width='100%';area.rows=5;$('ac-status').append(area);area.focus();area.select();return false;}}
  async function handle(action,id){
    if(S.busy)return;
    if(S.user.role!=='teacher'&&!['tab','refresh','feedback-save','invite-accept','invite-decline','invite-alternative','invite-alternative-save'].includes(action))return;
    const r=row(id),inv=S.invites.find(x=>x.id===id),f=S.report.feedback.find(x=>x.id===id);
    if(action==='tab'){if(S.user.role!=='teacher'&&!['invites','feedback'].includes(id))return;S.tab=id;S.open=null;render();return;}
    if(action==='refresh'){S.from=$('ac-from').value;S.to=$('ac-to').value;await load();return;}
    if(action==='detail'){S.open=S.open===id?null:id;drawRows();return;}
    if(action==='contact'){await save({action:'contact',studentId:id,nickname:$('contact-'+id).value});return;}
    if(action==='attendance'){await save(recordBody(r,'attendance',{attendance:$('att-'+id).value}));return;}
    if(action==='teacher-leave'){if(confirm('确认代家长登记本课次请假，并建立个人补课任务？'))await save(recordBody(r,'teacher_leave',{reason:$('leave-reason-'+id).value.trim()}));return;}
    if(action==='clear-session'){if(confirm('撤销显示时间调整，恢复原课次日期？'))await save(recordBody(r,'clear_session_time'));return;}
    if(action==='unlink'){if(r.plannedMakeup?.invitationId){S.tab='invites';render();return;}if(confirm('撤销补课安排或关联，恢复请假待补课？已有真实上课记录保留。'))await save(recordBody(r,'unlink_makeup'));return;}
    if(action==='plan-direct'){
      const host=$('ac-body').querySelector('.ac-detail.open');if(!host)return;
      host.querySelector('.ac-direct-plan')?.remove();const form=document.createElement('div');form.className='ac-form ac-direct-plan';
      const peers=S.report.rows.filter(x=>x.id!==r.id&&x.course===r.course&&['leave','makeup_pending'].includes(x.attendance)&&!x.makeupTarget);
      form.innerHTML='<h4>安排同课补课班次</h4><p class="ac-note">同一课程的请假学员可以合并到一个补课班次，确认一次时间即可。</p><label>日期<input id="plan-day" type="date" min="'+date()+'" value="'+date(1)+'"></label><label>开始时间<input id="plan-time" type="time" value="09:00"></label><label>时长（小时）<input id="plan-duration" type="number" min="0.25" max="8" step="0.25" value="0.5"></label>'+(peers.length?'<fieldset><legend>一起补课的同课程学员</legend>'+peers.map(x=>'<label><input class="makeup-peer" type="checkbox" value="'+E(x.studentId)+'">'+E(x.studentName)+'</label>').join('')+'</fieldset>':'')+button('确认补课时间','plan-save',r.id);host.prepend(form);return;
    }
    if(action==='plan-save'){const day=$('plan-day').value,time=$('plan-time').value,duration=Number($('plan-duration').value),groupStudentIds=[r.studentId,...[...document.querySelectorAll('.makeup-peer:checked')].map(x=>x.value)];if(confirm('确认安排 '+groupStudentIds.length+' 位学员在 '+day+' '+time+' 至 '+endLabel(time,duration)+' 补课？'))await save(recordBody(r,'plan_makeup',{date:day,time,duration,groupStudentIds}));return;}
    if(action==='session-time'){await save(recordBody(r,'session_time',{date:$('session-day-'+id).value,time:$('session-time-'+id).value.trim()}));return;}
    if(action==='nohomework'){await save(recordBody(r,'no_homework',{value:$('nohw-'+id).checked}));return;}
    if(action==='link'){await save(recordBody(r,'link_makeup',{target:$('target-'+id).value}));return;}
    if(action==='record'){close();window.dispatchEvent(new CustomEvent('after-class-open-record',{detail:r}));return;}
    if(action==='copy'){if(await copy(notice(r)))await save(recordBody(r,'copied'));return;}
    if(action==='sent'){if(confirm('确认已把这条课次的通知发给对应家长？')){S.open=null;if(!await save(recordBody(r,'sent',{publication:r.publication}))){S.open=id;drawRows();}}return;}
    if(action==='feedback-save'){const itemId=id==='parent'?$('feedback-record').value:id;S.feedbackRequest=S.feedbackRequest||crypto.randomUUID();if(await save({action:'feedback',itemId,requestId:S.feedbackRequest,category:$('category-'+id).value,message:$('message-'+id).value}))S.feedbackRequest=null;return;}
    if(action==='reply'){await save({action:'reply',feedbackId:id,version:f.version,reply:$('reply-'+id).value});return;}
    if(action==='reply-copy'){await copy(f.reply);return;}
    if(action==='reply-sent'){if(confirm('确认已在微信发送这条回复？'))await save({action:'reply_sent',feedbackId:id,version:f.version});return;}
    if(action==='makeup'){S.tab='invites';render();newInvite(r);return;}
    if(action==='scheduler-pick'){pickSchedule(id);return;}
    if(action==='invite-new'){newInvite();return;}
    if(action==='invite-create'){
      if(!$('ac-invite-form').reportValidity())return;
      const ids=[...document.querySelectorAll('[data-recipient]:checked')].map(x=>x.dataset.recipient),origins={};
      document.querySelectorAll('[data-origin]').forEach(x=>{if(ids.includes(x.dataset.origin))origins[x.dataset.origin]=x.value;});
      await save({action:'invite_create',id:S.inviteId,course:$('invite-course').value,kind:$('invite-kind').value,date:$('invite-date').value,time:$('invite-time').value,duration:Number($('invite-duration').value),deadline:$('invite-deadline').value,note:$('invite-note').value,studentIds:ids,origins});return;
    }
    if(action==='invite-publish'){if(confirm('发布后，所选学员登录即可确认此时间。确定发布？'))await save({action:'invite_publish',id,version:inv.version});return;}
    if(action==='invite-cancel'){if(confirm('取消后将移除这项邀请产生的未来排课，已保存的上课记录保持原样。确定取消？'))await save({action:'invite_cancel',id,version:inv.version});return;}
    if(action==='invite-copy'){await copy('家长您好，老师邀请参加 '+inv.date+' '+inv.time+' 的'+inv.course+'（'+inv.duration+'小时）。请于 '+inv.deadline.slice(0,16).replace('T',' ')+' 前登录学员后台，在“课程确认”中确认或选择其他时间。\nhttps://emilyenstudio.com/lesson.html');return;}
    if(action==='invite-alternative'){$('invite-response-'+id).innerHTML='<textarea id="alternative-'+id+'" aria-label="方便的时间" placeholder="请填写方便的时间"></textarea>'+button('提交其他时间','invite-alternative-save',id);return;}
    if(['invite-accept','invite-decline','invite-alternative-save'].includes(action)){
      const status=action==='invite-accept'?'accepted':action==='invite-decline'?'declined':'alternative';
      if(!confirm('确认'+(status==='accepted'?'参加此课程':status==='declined'?'不参加此课程':'提交其他时间')+'？'))return;
      await save({action:'invite_reply',id,version:inv.version,replyVersion:inv.responses[0].version,status,message:status==='alternative'?$('alternative-'+id).value:''});
    }
  }
  function boot(){
    shell();
    for(const [selector,text,tab] of [['.teacher-nav','课后跟进','todo'],['.student-nav','课程确认与反馈','invites']]){
      const host=document.querySelector(selector);if(!host||host.querySelector('.ac-launch'))continue;
      const b=document.createElement('button');b.className='ac-launch';b.type='button';b.textContent=text;b.onclick=()=>open(tab);host.append(b);
    }
  }
  const originalNewInvite=newInvite;
  newInvite=function(origin){if(origin)S.focusStudent=origin.studentId;return originalNewInvite(origin);};
  const originalPickSchedule=pickSchedule;
  pickSchedule=function(code){
    const [ti,ci]=code.split(':').map(Number),task=S.scheduler?.tasks?.[ti],slot=task?.candidates?.[ci];
    if(!task||!slot)return originalPickSchedule(code);
    const sid=S.focusStudent||task.studentIds?.[0];
    const origin=task.kind==='individual_makeup'?S.report.rows.find(r=>r.studentId===sid&&r.course===task.course&&['makeup_pending','leave'].includes(r.attendance)):null;
    S.tab='invites';render();newInvite(origin);$('invite-course').value=task.course;$('invite-kind').value=task.kind==='individual_makeup'?'makeup':'extra';$('invite-date').value=slot.date;$('invite-time').value=slot.time;$('invite-duration').value=slot.duration;drawInviteStudents();
    task.studentIds?.forEach(id=>{const box=document.querySelector('[data-recipient="'+CSS.escape(id)+'"]');if(box)box.checked=true;});
  };
  const originalRender=render;
  render=function(){
    originalRender();
    if(S.user?.role==='teacher'&&S.report){
      for(const r of S.report.rows||[]){
        if(!r.plannedMakeup||S.open!==r.id)continue;
        const detail=document.querySelector('.ac-detail.open');
        if(detail&&!detail.querySelector('[data-action="delete-makeup"]'))detail.insertAdjacentHTML('beforeend',button('删除整个错误补课班次','delete-makeup',r.id));
      }
    }
  };
  const originalHandle=handle;
  handle=async function(action,id){
    if(action==='delete-makeup'){
      const r=row(id);if(!r?.plannedMakeup)return;
      if(confirm('确认删除 '+r.plannedMakeup.date+' '+r.plannedMakeup.time+' 的整个错误补课班次？原请假课次保留，课时和真实记录不变。')){
        if(await save(recordBody(r,'delete_makeup_class')))window.dispatchEvent(new Event('after-class-schedule-changed'));
      }
      return;
    }
    return originalHandle(action,id);
  };
  async function cancelClass(context){
    S.from=context.date||date();S.to=context.date||date();S.search=context.course||'';S.student='';S.focusStudent='';S.focusDate=S.from;S.focusCourse=S.search;S.filter='all';
    await open('todo');
    const rows=(S.report?.rows||[]).filter(r=>r.course===context.course&&r.date===context.date&&(!context.time||r.time===context.time||(r.plannedMakeup&&r.plannedMakeup.time===context.time)));
    const target=rows[0];
    if(!target){alert('没有找到可取消的班次，请先刷新页面。');return;}
    const isMakeup=rows.some(r=>r.plannedMakeup);
    if(isMakeup){
      if(confirm('确认取消 '+context.date+' '+context.time+' 的整个补课班次？同一班次的所有学生都会恢复为待补课，原请假记录和真实上课记录不变。')){
        if(await save(recordBody(target,'delete_makeup_class')))window.dispatchEvent(new Event('after-class-schedule-changed'));
      }
      return;
    }
    if(confirm('确认只取消 '+context.date+' '+context.time+' 这一次班次？未来的固定排课不变。')){
      await save(recordBody(target,'cancel_class',{reason:'教师取消本次班次'}));
    }
  }
  window.AfterClass={open,cancelClass,async openSession(context){S.from=context.date||date();S.to=context.date||date();S.search=context.course||'';S.student=context.studentId||'';S.focusStudent=context.studentId||'';S.focusDate=context.date||'';S.focusCourse=context.course||'';S.filter='all';await open(context.studentId?'smart':'todo');}};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();
