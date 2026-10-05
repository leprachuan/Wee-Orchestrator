// Shared API inbox: all authenticated clients read the same durable requests.
export function permanentScope(request) {
  const {agent, operation, host, resource} = request.scope;
  return `${agent} · ${operation} · ${host} · ${resource}`;
}
export function canAlwaysAllow(request) {
  return !['shell.execute', 'python.execute', 'browser.execute', 'delegate.execute'].includes(request.scope.operation);
}
export function initAutonomy({request, isAuthenticated}) {
  let selectedAgent = '', generation = 0;
  const rawRequest = request;
  request = (method, path, body) => rawRequest(method, path + (path.includes('?') ? '&' : '?') + 'agent=' + encodeURIComponent(selectedAgent), body);
  const modal = document.createElement('div');
  modal.className = 'modal-overlay hidden';
  const box = document.createElement('section');
  box.className = 'modal-box glass-panel';
  box.style.cssText = 'max-width:800px;max-height:90vh;overflow:auto;padding:20px;width:95%;';
  box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true');
  box.setAttribute('aria-label', 'Agent Always-On');
  const heading = document.createElement('h2'); heading.textContent = 'Always-On';
  const close = document.createElement('button'); close.className = 'btn btn-ghost'; close.textContent = 'Close';
  close.onclick = () => { modal.classList.add('hidden'); document.getElementById('asf-always-on')?.focus(); };
  const status = document.createElement('p'); status.setAttribute('role', 'status');
  const responsibilities = document.createElement('div');
  const inbox = document.createElement('div');
  const rules = document.createElement('div');
  const budgets = document.createElement('section');
  const ruleHeading = document.createElement('h3'); ruleHeading.textContent = 'Saved action rules';
  const editor = document.createElement('form');
  let editing = null, busy = false, last = '', cursor = 0, identityPresent = false, responsibilityForm = null, modelLoaded = false;
  const modelFields = {};
  const budgetForm = document.createElement('form');
  const budgetTitle = document.createElement('h3'); budgetTitle.textContent = 'Routine model and budgets';
  const budgetStatus = document.createElement('p');
  for (const [key,label] of [['routine_runtime','Routine runtime'],['routine_model','Default model for selected runtime'],['escalation_runtime','Escalation runtime'],['escalation_models','Permitted escalation models (comma-separated, optional)'],['max_requests_per_run','Maximum requests per run (1–3)'],['max_output_tokens','Requested output tokens (128–2048)'],['daily_requests','Daily request limit (1–100)'],['daily_token_budget','Daily reserved token budget (1024–200000)']]) {
    const input=document.createElement(key.endsWith('_runtime') ? 'select' : 'input');input.className='glass-input';input.style.cssText='display:block;width:100%;margin:4px 0 10px';modelFields[key]=input;
    const wrapper=document.createElement('label');wrapper.textContent=label;wrapper.append(input);budgetForm.append(wrapper);
  }
  const budgetSave=document.createElement('button');budgetSave.className='btn btn-primary';budgetSave.textContent='Save model budgets';budgetForm.append(budgetSave);
  budgetForm.onsubmit=e=>{e.preventDefault();const body={routine_runtime:modelFields.routine_runtime.value,escalation_runtime:modelFields.escalation_runtime.value,routine_model:modelFields.routine_model.value.trim(),escalation_models:modelFields.escalation_models.value.split(',').map(v=>v.trim()).filter(Boolean)};for(const key of ['max_requests_per_run','max_output_tokens','daily_requests','daily_token_budget'])body[key]=Number(modelFields[key].value);mutate(async()=>{await request('PUT','/autonomy/model-settings',body);modelLoaded=false;});};
  async function loadRuntimeModels(kind) {
    const runtime = modelFields[kind+'_runtime'].value; const opened = generation;
    try {
      const catalog = await request('GET', '/autonomy/runtime-catalog?runtime='+encodeURIComponent(runtime));
      if (opened !== generation || modelFields[kind+'_runtime'].value !== runtime) return;
      const id = 'autonomy-'+kind+'-models';
      document.getElementById(id)?.remove();
      const list = document.createElement('datalist'); list.id=id;
      for (const model of catalog.models) { const opt=document.createElement('option');opt.value=model.id;list.append(opt); }
      modelFields[kind === 'routine' ? 'routine_model' : 'escalation_models'].setAttribute('list',id);
      budgetForm.append(list);
    } catch(error) { status.textContent=error.message; }
  }
  for (const kind of ['routine','escalation']) modelFields[kind+'_runtime'].onchange=()=>loadRuntimeModels(kind);
  budgets.append(budgetTitle,budgetStatus,budgetForm);
  const inputs = {};
  for (const key of ['agent', 'operation', 'host', 'resource']) {
    const label = document.createElement('label'); label.textContent = key[0].toUpperCase() + key.slice(1);
    const input = document.createElement('input'); input.required = true; input.maxLength = 1024;
    input.className = 'glass-input'; input.style.cssText = 'display:block;width:100%;margin:4px 0 10px';
    if (key === 'agent') input.readOnly=true; inputs[key] = input; label.append(input); editor.append(label);
  }
  const decision = document.createElement('select');
  for (const value of ['ask','allow','deny']) { const opt = document.createElement('option'); opt.value = value; opt.textContent = value; decision.append(opt); }
  const save = document.createElement('button'); save.className = 'btn btn-primary'; save.textContent = 'Save explicit rule';
  const reset = document.createElement('button'); reset.type = 'button'; reset.className = 'btn btn-ghost'; reset.textContent = 'New rule';
  reset.onclick = () => { editing = null; editor.reset(); inputs.agent.value=selectedAgent; };
  editor.append(decision, save, reset);
  editor.onsubmit = async e => {
    e.preventDefault();
    const body = {decision: decision.value, path_prefix: false};
    for (const [key,input] of Object.entries(inputs)) body[key] = input.value.trim();
    if (!window.confirm(`Save this exact ${body.decision} rule?\n${body.agent} · ${body.operation} · ${body.host} · ${body.resource}`)) return;
    await mutate(() => request(editing ? 'PUT' : 'POST', '/autonomy/rules' + (editing ? '/' + encodeURIComponent(editing) : ''), body));
    editing = null; editor.reset(); inputs.agent.value=selectedAgent;
  };
  box.append(heading, close, status, inbox, responsibilities, budgets, ruleHeading, rules, editor); modal.append(box); document.body.append(modal);
  window.addEventListener('wee:agent-always-on', e => {
    if (busy || !e.detail?.agent) return;
    selectedAgent = e.detail.agent; generation++; modelLoaded=false; last=''; cursor=0;
    responsibilityForm=null; editing=null; editor.reset(); inputs.agent.value=selectedAgent;
    heading.textContent=selectedAgent+' · Always-On'; status.textContent='';
    inbox.replaceChildren(); rules.replaceChildren(); responsibilities.replaceChildren();
    modal.classList.remove('hidden'); close.focus(); refresh(true);
  });
  modal.addEventListener('keydown', e => { if (e.key === 'Escape') close.click(); });
  function text(parent, tag, value) { const el = document.createElement(tag); el.textContent = value; parent.append(el); return el; }
  async function mutate(call) {
    if (busy) return;
    busy = true;
    box.querySelectorAll('button').forEach(b => b.disabled = true);
    try { await call(); status.textContent = 'Saved. All connected clients receive the same result.'; last = ''; }
    catch (error) { status.textContent = error.message; }
    finally { busy = false; box.querySelectorAll('button').forEach(b => b.disabled = false); await refresh(true); }
  }
  function render(data, policy, work, models, catalog) {
    if (!modelLoaded) {
      for (const key of ['routine_runtime','escalation_runtime']) {
        modelFields[key].replaceChildren();
        for (const runtime of catalog.runtimes) { const opt=document.createElement('option');opt.value=runtime.id;opt.textContent=runtime.label+(runtime.available?'':' (unavailable on API host)');modelFields[key].append(opt); }
      }
      for (const [key,input] of Object.entries(modelFields)) input.value = key === 'escalation_models' ? models.config[key].join(', ') : models.config[key]; modelLoaded=true; loadRuntimeModels('routine'); loadRuntimeModels('escalation'); }
    budgetStatus.textContent = `Today: ${models.usage.requests} requests · ${models.usage.reserved_tokens} reserved tokens · ${models.usage.unknown_usage} unknown usage readings. Escalation needs recorded failed checks, an allowed model, budget and shared approval. Price in dollars is unavailable. ${models.cost_note || ""}`;
    responsibilities.replaceChildren();
    text(responsibilities, 'h3', 'Always-On responsibilities');
    text(responsibilities, 'p', 'Opt-in agents draft reports in isolated workspaces. New responsibilities start paused.');
    for (const row of work.responsibilities) {
      const card = document.createElement('article');
      text(card, 'h4', row.agent + ' · ' + row.goal);
      text(card, 'p', row.status + ' · ' + row.phase + ' · every ' + row.interval_seconds + ' seconds');
      if (row.report) text(card, 'pre', row.report).style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';
      if (row.error) text(card, 'p', row.error);
      if (row.status !== 'cancelled') {
        for (const command of ['resume','pause','cancel',...(row.phase === 'attention' ? ['reconcile'] : [])]) {
          const b = document.createElement('button'); b.className='btn btn-ghost btn-sm'; b.textContent = command;
          b.onclick = () => {
            if (command === 'reconcile' && !window.confirm('Acknowledge the interrupted/uncertain run? Inspect the report and action history first. A new run will start paused.')) return;
            mutate(() => request('POST', '/autonomy/responsibilities/'+encodeURIComponent(row.id)+'/control',{command}));
          }; card.append(b);
        }
        const revise = document.createElement('button'); revise.className='btn btn-ghost btn-sm'; revise.textContent='Revise goal';
        revise.onclick=()=>{const goal=window.prompt('Revise the responsibility (pauses it and discards its pending plan):',row.goal);if(goal)mutate(()=>request('PUT','/autonomy/responsibilities/'+encodeURIComponent(row.id),{goal}));};card.append(revise);
      }
      if (row.status === 'cancelled') {
        const remove = document.createElement('button'); remove.className='btn btn-ghost btn-sm'; remove.textContent='Delete goal';
        remove.onclick=()=>{if(window.confirm('Remove this cancelled goal from the list? Its action history is retained.')) mutate(()=>request('DELETE','/autonomy/responsibilities/'+encodeURIComponent(row.id)));};
        card.append(remove);
      }
      responsibilities.append(card);
    }
    if (!responsibilityForm) {
    const form = document.createElement('form');
    const values = {};
    for (const [key,label,value] of [['agent','Agent name',''],['goal','Responsibility',''],['interval_seconds','Interval in seconds (minimum 300)','3600']]) {
      const el=document.createElement('input');el.className='glass-input';el.required=true;el.value=key==='agent'?selectedAgent:value;el.readOnly=key==='agent';el.maxLength=1024;
      const wrapper=document.createElement('label');wrapper.textContent=label;wrapper.append(el);form.append(wrapper);values[key]=el;
    }
    const create=document.createElement('button');create.className='btn btn-primary';create.textContent='Create paused responsibility';form.append(create);
    form.onsubmit=e=>{e.preventDefault();mutate(()=>request('POST','/autonomy/responsibilities',{agent:values.agent.value.trim(),goal:values.goal.value.trim(),interval_seconds:Number(values.interval_seconds.value)}));};responsibilityForm=form; }
    responsibilities.append(responsibilityForm);
    inbox.replaceChildren(); rules.replaceChildren();
    const pending = data.requests.filter(r => ['pending','rule_pending'].includes(r.status));
    text(inbox, 'h3', `Approvals for ${selectedAgent} (${pending.length} pending)`);
    if (!pending.length) text(inbox, 'p', 'No pending approvals.');
    for (const item of data.requests) {
      const card = document.createElement('article'); card.style.cssText = 'border-bottom:1px solid #8885;padding:12px 0';
      text(card, 'h3', item.preview.summary); text(card, 'p', permanentScope(item));
      if (item.preview.details) text(card, 'pre', item.preview.details).style.cssText = 'white-space:pre-wrap;overflow-wrap:anywhere';
      text(card, 'p', `${item.status} · Expires ${new Date(item.expires_at * 1000).toLocaleString()}`);
      if (item.status === 'pending') {
        for (const [value,label] of [['approve_once','Approve once'],['reject','Reject'],['revise','Request revision'],['approve_always','Approve & always allow']]) {
          if (value === 'approve_always' && !canAlwaysAllow(item)) continue;
          const b = document.createElement('button'); b.className = 'btn btn-ghost btn-sm'; b.textContent = label;
          b.onclick = () => {
            if (value === 'approve_always' && !window.confirm(`Always allow this exact scope?\n${permanentScope(item)}\nYou can revoke the saved rule here.`)) return;
            mutate(() => request('POST', `/autonomy/approvals/${encodeURIComponent(item.id)}/decision`, {decision: value, fingerprint: item.fingerprint}));
          };
          card.append(b);
        }
      }
      inbox.append(card);
    }
    text(rules, 'p', 'Rules apply only to this agent. Resume a responsibility to start it; pause or cancel to stop it.');
    for (const rule of policy.rules) {
      const card = document.createElement('article');
      text(card, 'p', `${rule.enabled ? rule.decision : 'revoked'} · ${permanentScope({scope:rule})}${rule.path_prefix ? ' (path prefix)' : ''}`);
      if (rule.enabled) {
        const edit = document.createElement('button'); edit.className = 'btn btn-ghost btn-sm'; edit.textContent = 'Edit exact scope';
        edit.onclick = () => { editing = rule.id; for (const [key,input] of Object.entries(inputs)) input.value = rule[key]; decision.value = rule.decision; editor.scrollIntoView({block:'nearest'}); };
        const revoke = document.createElement('button'); revoke.className = 'btn btn-ghost btn-sm'; revoke.textContent = 'Revoke';
        revoke.onclick = () => mutate(() => request('DELETE', '/autonomy/rules/' + encodeURIComponent(rule.id)));
        card.append(edit, revoke);
      }
      rules.append(card);
    }
  }
  async function refresh(force = false) {
    if (!selectedAgent || modal.classList.contains('hidden')) return;
    const opened = generation;
    if (!isAuthenticated()) {
      if (identityPresent) { inbox.replaceChildren(); rules.replaceChildren(); responsibilities.replaceChildren(); last = ''; cursor = 0; modal.classList.add('hidden'); }
      identityPresent = false; modelLoaded=false; return;
    }
    identityPresent = true;
    if (busy) return;
    try {
      const events = await request('GET', `/autonomy/events?after=${cursor}`);
      const changed = events.cursor !== cursor; cursor = events.cursor;
      if (!force && !changed && last && modal.classList.contains('hidden')) return;
      const [data, policy, work, models, catalog] = await Promise.all([request('GET','/autonomy/approvals'), request('GET','/autonomy/rules'), request('GET','/autonomy/responsibilities'), request('GET','/autonomy/model-settings'), request('GET','/autonomy/runtime-catalog')]);
      if (opened !== generation || modal.classList.contains('hidden')) return;
      const version = JSON.stringify([data, policy, work, models]);
      if (version !== last && (force || !document.activeElement?.closest('form'))) { render(data, policy, work, models, catalog); last = version; }
    } catch (error) { if (!modal.classList.contains('hidden')) status.textContent = error.message; }
  }
  // Connected delivery and authoritative catch-up after sleep/network interruption.
  setInterval(() => refresh(), 3000);
  window.addEventListener('online', () => refresh(true));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(true); });
  document.getElementById('asf-agent-selector')?.addEventListener('change', () => { generation++; modal.classList.add('hidden'); });
}
