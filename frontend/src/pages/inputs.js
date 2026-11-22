import {feedback, field} from '../components/common/fields.js';
import {empty, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {CLEANUP} from '../constants/enums.js';
import {handler, poll} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, link, p} from '../utils/dom.js';
import {date} from '../utils/format.js';

function cleaningNotice(selected, refresh) {
  const notice = el('p',{role:'status'},'清理进行中，完成后会自动展示。');
  const original = location.hash;
  poll(()=>workspace.getDataset(selected.id).catch(()=>null), item=>item, ()=>location.hash===original)
    .then(done=>{if (done && notice.isConnected && location.hash===original) refresh();
      else if (notice.isConnected) notice.textContent='仍在清理，请点击刷新检查。';});
  return el('div',{class:'empty'}, pill('清理中','cleaning'),
    p('计量室正在纠正该批次的责任班组，原始行、历史班报与下载文件清理完成后恢复查看，其他批次照用。'),
    notice, button('刷新',{class:'quiet',onClick:refresh}));
}

function correctionsPanel(selected, info, refresh) {
  const message = feedback();
  const submit = button('登记纠正并清理', {type:'submit', class:'primary', 'data-testid':'submit-correction'});
  const form = session.canWrite && el('form',{class:'fields',onSubmit:handler(submit,message,async event=>{
    const data = new FormData(event.target);
    await workspace.correct(selected.id,{corrections:[{old_name:data.get('old_name'),new_name:data.get('new_name')}]});
    await refresh();
  })}, field({key:'old_name',label:'外协姓名（原责任班组）',type:'string',max_length:60}),
    field({key:'new_name',label:'正确班组',type:'string',max_length:60}), submit, message);
  return panel('班组纠正',
    p('计量室给出批次内外协姓名对应的正确班组。登记后后台清理原始行、历史班报与下载文件，清理期间该批次暂停查看。','subtle'),
    info?.cleanup && el('p',{},pill(CLEANUP[info.cleanup.state] || info.cleanup.state, info.cleanup.state)),
    info?.corrections?.length ? table([
      {key:'old_name',label:'外协姓名'}, {key:'new_name',label:'正确班组'},
      {key:'author',label:'登记人'}, {label:'登记时间',render:row=>date(row.created_at)},
    ], info.corrections, {caption:'已登记的班组纠正'}) : p('尚未登记纠正。','subtle'),
    form);
}

export async function inputs(route, refresh) {
  const {spec,datasets} = workspace;
  const selected = datasets.find(item => item.id === route.id) || datasets[0];
  const error = feedback();
  const sample = button('下载示例 CSV', {class:'quiet',onClick:event=>handler(sample,error,()=>workspace.download('/template',spec.sample_name))(event)});
  let detail = null, info = null;
  if (selected) {
    info = await workspace.corrections(selected.id);
    if (!selected.cleaning) {
      try { detail = await workspace.getDataset(selected.id,route.offset); }
      catch (problem) { if (problem.status !== 409) throw problem; }
    }
  }
  const columns = spec.fields.map(field => ({key:field.key,label:field.label}));
  const preview = !selected ? empty('没有可用的批次。请导入示例或业务 CSV。')
    : panel('批次明细', el('div',{class:'actions'},
      el('label',{for:'dataset-id',class:'sr'}, '选择数据批次'),
      el('select',{id:'dataset-id','aria-label':'选择数据批次',onChange:event=>{location.hash=`#inputs/${event.target.value}`;}},
        datasets.map(item=>el('option',{value:item.id,selected:item.id === selected.id}, `${item.name}${item.cleaning?'（清理中）':''}`))),
      detail && link('使用此批次运行',`#run/${detail.id}`,{class:'quiet'})),
      !detail ? cleaningNotice(selected, refresh) : [
      p(`${detail.filename} · ${detail.row_count} 行 · ${date(detail.created_at)}`,'filename'),
      table(columns,detail.rows,{caption:'批次明细',pageSize:16}),
      el('div',{class:'pagination'},
        route.offset > 0 && link('上一批明细',`#inputs/${detail.id}/${Math.max(0,route.offset-16)}`,{class:'quiet'}),
        p(`${detail.rows.length ? route.offset+1 : route.offset}–${route.offset+detail.rows.length} / ${detail.row_count}`),
        route.offset+16 < detail.row_count && link('下一批明细',`#inputs/${detail.id}/${route.offset+16}`,{class:'quiet'}))]);
  const submit = button('校验并导入', {type:'submit',class:'primary'});
  const upload = session.canWrite && el('form',{class:'fields',onSubmit:handler(submit,error,async event=>{
    const data = new FormData(event.target);
    const file = data.get('source');
    if (!file?.size) throw new Error('请选择一个 CSV 文件');
    if (file.size > 2_000_000) throw new Error('CSV 最多 2 MB');
    const item = await workspace.upload({name:data.get('batch_name'),filename:file.name,csv_text:await file.text()});
    const target = `#inputs/${item.id}`;
    if (location.hash === target) await refresh();
    else location.hash = target;
  })},field({key:'batch_name',label:'批次名称',type:'string',max_length:120}),
    el('label',{class:'field',for:'source'}, el('span',{},'业务 CSV'),el('input',{id:'source',type:'file',name:'source',accept:'.csv,text/csv',required:true})), submit);
  return el('div',{class:'two-col'},el('div',{class:'wide-panel'},preview),el('aside',{class:'sidebar-panel'},
    panel('数据导入',p('导入后保留原批次和校验指纹，可用同一份数据复跑。','subtle'),sample,upload,error),
    selected && correctionsPanel(selected, info, refresh),
    panel('字段说明',el('dl',{class:'dictionary'},spec.fields.map(item=>el('div',{},el('dt',{},item.label),
      el('dd',{},item.key,item.required === false ? ' · 可空' : ' · 必填',item.options ? ` · ${item.options.join(' / ')}` : '')))))));
}
