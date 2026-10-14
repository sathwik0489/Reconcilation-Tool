'use client';
import {useCallback,useEffect,useMemo,useRef,useState,type DragEvent} from 'react';
import Link from 'next/link';
import {ReactFlow,ReactFlowProvider,Background,Controls,MiniMap,Handle,Position,MarkerType,useNodesState,useReactFlow,type Node,type Edge,type Connection,type NodeProps} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {Plus,Check,Search,Undo2,Redo2,LayoutGrid,Maximize2,Minimize2,ShieldCheck,Save,Play,Trash2,KeyRound,ChevronDown,ChevronRight,Eye,X,Link2,CircleAlert,TriangleAlert,CircleCheck,LoaderCircle} from 'lucide-react';
import {api} from '@/services/api';
import {InventoryTable} from '@/components/inventory';
import type {User,Inventory} from '@/types';
import {AdvancedRules} from './advanced-rules';
import {arrange,autoMap,emptyDiagram,link,nodeHeight,removeTable,roleOf,unlink,validate,type Branch,type Dataset,type Diagram,type Issue} from './mapping-model';

type SavedMapping={id:string;name:string;mapping_type:string};
type TargetResult={id:string;target_table:string;source_count:number;target_count:number;matched:number;mismatched:number;missing:number;extra:number;duplicates:number;match_percentage:number|null};
type Execution={state:'idle'|'saving'|'running'|'succeeded'|'failed';detail:string;results:TargetResult[]};
type TableNodeData={table:Dataset;role:string;collapsed:boolean;mapped:string[];editable:boolean;toggle:()=>void};
type Menu={x:number;y:number;table?:string;edge?:{source:string;target:string}};
const DRAG_TYPE='application/x-reconviora-table';
const layoutKey=(id:string)=>'reconviora:mapping-layout:'+id;

function useHistory(){
  const [state,setState]=useState<{past:Diagram[];present:Diagram;future:Diagram[]}>({past:[],present:emptyDiagram(),future:[]});
  const commit=useCallback((change:(d:Diagram)=>Diagram)=>setState(s=>{const next=change(s.present);return next===s.present?s:{past:[...s.past.slice(-49),s.present],present:next,future:[]};}),[]);
  const undo=useCallback(()=>setState(s=>s.past.length?{past:s.past.slice(0,-1),present:s.past[s.past.length-1],future:[s.present,...s.future]}:s),[]);
  const redo=useCallback(()=>setState(s=>s.future.length?{past:[...s.past,s.present],present:s.future[0],future:s.future.slice(1)}:s),[]);
  const reset=useCallback((diagram:Diagram)=>setState({past:[],present:diagram,future:[]}),[]);
  return {diagram:state.present,commit,undo,redo,reset,canUndo:state.past.length>0,canRedo:state.future.length>0};
}

function TableNode({data,selected}:NodeProps<Node<TableNodeData>>){
  const t=data.table;
  return <div className={`studio-node role-${data.role}${selected?' is-selected':''}`}>
    <Handle type="target" position={Position.Left} isConnectable={data.editable} aria-label={`Use ${t.name} as a target`}/>
    <header>
      <div><span className="studio-node-connection">{t.connection_name}</span><strong>{t.name}</strong><span className="studio-node-meta">{t.schema_name} · {t.columns.length} columns{data.role!=='unlinked'?` · ${data.role==='both'?'source & target':data.role}`:''}</span></div>
      <button className="nodrag icon-button" onClick={data.toggle} aria-expanded={!data.collapsed} aria-label={`${data.collapsed?'Expand':'Collapse'} columns of ${t.name}`}>{data.collapsed?<ChevronRight size={16}/>:<ChevronDown size={16}/>}</button>
    </header>
    {!data.collapsed&&<ul className="nowheel nodrag">{t.columns.map(c=><li key={c.name} className={data.mapped.includes(c.name)?'is-mapped':''}><span>{c.primary_key&&<KeyRound size={12} aria-label="Primary key"/>}{c.name}</span><small>{c.data_type}</small></li>)}</ul>}
    <Handle type="source" position={Position.Right} isConnectable={data.editable} aria-label={`Use ${t.name} as a source`}/>
  </div>;
}
const nodeTypes={table:TableNode};

export function MappingStudio({user}:{user:User}){return <ReactFlowProvider><Studio user={user}/></ReactFlowProvider>;}

function Studio({user}:{user:User}){
  const editable=user.role!=='viewer';
  const {diagram,commit,undo,redo,reset,canUndo,canRedo}=useHistory();
  const [tables,setTables]=useState<Dataset[]|null>(null),[mappings,setMappings]=useState<SavedMapping[]>([]);
  const [name,setName]=useState(''),[mappingId,setMappingId]=useState(''),[saved,setSaved]=useState('');
  const [active,setActive]=useState<string|null>(null),[query,setQuery]=useState(''),[error,setError]=useState('');
  const [checked,setChecked]=useState(false),[showIssues,setShowIssues]=useState(false);
  const [execution,setExecution]=useState<Execution>({state:'idle',detail:'Not run yet',results:[]});
  const [preview,setPreview]=useState<{title:string;data:Inventory}|null>(null),[previewing,setPreviewing]=useState(false);
  const [fullscreen,setFullscreen]=useState(false),[menu,setMenu]=useState<Menu|null>(null),[linkFrom,setLinkFrom]=useState(''),[linkTo,setLinkTo]=useState('');
  const [nodes,setNodes,onNodesChange]=useNodesState<Node<TableNodeData>>([]);
  const flow=useReactFlow();const canvas=useRef<HTMLDivElement>(null);
  const lookup=useMemo(()=>new Map((tables||[]).map(t=>[t.id,t])),[tables]);
  const busy=execution.state==='saving'||execution.state==='running';
  const snapshot=JSON.stringify({name,branches:diagram.branches});
  const dirty=snapshot!==saved;
  const branch=diagram.branches.find(b=>b.target_table_id===active)||null;
  // Validation stays live once requested, so the counts always describe the current diagram.
  const found=useMemo(()=>validate(diagram,lookup,name),[diagram,lookup,name]);
  const issues:Issue[]|null=checked?found:null;

  useEffect(()=>{Promise.all([api<Dataset[]>('/workflows/tables'),api<{items:SavedMapping[]}>('/mappings?page_size=100')]).then(([datasets,maps])=>{setTables(datasets);setMappings(maps.items);}).catch(e=>{setTables([]);setError((e as Error).message);});},[]);

  // Table nodes follow the diagram; React Flow keeps its own measurements and selection between updates.
  useEffect(()=>{setNodes(previous=>Object.entries(diagram.placed).filter(([id])=>lookup.has(id)).map(([id,position])=>{
    const table=lookup.get(id)!;const role=roleOf(diagram,id);
    const mapped=diagram.branches.flatMap(b=>[...(b.target_table_id===id?[...b.columns.map(c=>c.target),...b.aggregations.map(a=>a.target)]:[]),...(b.source_table_ids.includes(id)?[...b.columns,...b.aggregations].filter(c=>c.source.startsWith(table.name+'.')).map(c=>c.source.slice(table.name.length+1)):[])]);
    const data:TableNodeData={table,role,collapsed:!!diagram.collapsed[id],mapped,editable,toggle:()=>commit(d=>({...d,collapsed:{...d.collapsed,[id]:!d.collapsed[id]}}))};
    return {...previous.find(n=>n.id===id),id,type:'table',position,data};
  }));},[diagram,lookup,editable,commit,setNodes]);

  const edges=useMemo<Edge[]>(()=>diagram.branches.flatMap(b=>b.source_table_ids.map((source,i)=>{
    const selected=b.target_table_id===active;
    return {id:source+'->'+b.target_table_id,source,target:b.target_table_id,selected,label:i===0?`${b.columns.length+b.aggregations.length} mapped`:'left join',
      markerEnd:{type:MarkerType.ArrowClosed,color:selected?'#14213D':'#00B8A9'},style:{stroke:selected?'#14213D':'#00B8A9',strokeWidth:selected?3:2},labelStyle:{fontSize:12,fill:'#334155'},labelBgPadding:[6,3] as [number,number],labelBgBorderRadius:6};
  })),[diagram.branches,active]);

  const change=useCallback((patch:Partial<Branch>)=>commit(d=>({...d,branches:d.branches.map(b=>b.target_table_id===active?{...b,...patch}:b)})),[commit,active]);
  const place=useCallback((id:string,position?:{x:number;y:number})=>commit(d=>{
    if(d.placed[id])return d;
    const below=Object.entries(d.placed).reduce((y,[other,p])=>Math.max(y,p.y+nodeHeight(lookup.get(other),!!d.collapsed[other])+28),40);
    return {...d,placed:{...d.placed,[id]:position||{x:40,y:below}}};
  }),[commit,lookup]);
  const connect=useCallback((source:string,target:string)=>{if(!editable||source===target)return;commit(d=>link(d,lookup,source,target));setActive(target);},[commit,lookup,editable]);
  const disconnect=useCallback((source:string,target:string)=>commit(d=>unlink(d,lookup,source,target)),[commit,lookup]);
  const discard=useCallback((id:string)=>{commit(d=>removeTable(d,lookup,id));setActive(a=>a===id?null:a);},[commit,lookup]);

  function drop(e:DragEvent){const id=e.dataTransfer.getData(DRAG_TYPE);if(!id)return;e.preventDefault();place(id,flow.screenToFlowPosition({x:e.clientX,y:e.clientY}));}
  function check(){setChecked(true);setShowIssues(true);return found;}

  async function showRecords(table:Dataset){
    setPreviewing(true);setError('');
    try{const data=await api<{items:Record<string,unknown>[];page:number;page_size:number}>(`/tables/${table.id}/preview`);setPreview({title:`${table.connection_name} · ${table.schema_name}.${table.name} · first ${data.items.length} records`,data:{...data,total:data.items.length}});}
    catch(e){setError((e as Error).message);}finally{setPreviewing(false);}
  }
  async function showCombined(){
    if(!branch)return;setPreviewing(true);setError('');
    try{const data=await api<Inventory>('/mappings/preview',{method:'POST',body:JSON.stringify(branch)});setPreview({title:`Combined source for ${lookup.get(branch.target_table_id)?.name} · ${data.total} records after joins, filters and aggregation (showing ${data.items.length})`,data});}
    catch(e){setError((e as Error).message);}finally{setPreviewing(false);}
  }

  async function save():Promise<string|null>{
    if(check().some(i=>i.level==='error')){setError('Resolve the validation errors before saving.');return null;}
    setError('');setExecution(x=>({...x,state:'saving',detail:'Saving mapping…'}));
    try{
      const row=await api<{id:string;mapping_type:string}>('/mappings',{method:'POST',body:JSON.stringify({name:name.trim(),branches:diagram.branches})});
      try{localStorage.setItem(layoutKey(row.id),JSON.stringify({placed:diagram.placed,collapsed:diagram.collapsed}));}catch{}
      setMappingId(row.id);setSaved(snapshot);setMappings(ms=>[{id:row.id,name:name.trim(),mapping_type:row.mapping_type},...ms]);
      setExecution(x=>({...x,state:'idle',detail:'Saved as a new mapping version'}));
      return row.id;
    }catch(e){setError((e as Error).message);setExecution(x=>({...x,state:'failed',detail:'Save failed'}));return null;}
  }
  async function run(){
    const id=dirty||!mappingId?await save():mappingId;if(!id)return;
    setError('');setExecution({state:'running',detail:'Reading sources and comparing records…',results:[]});
    try{
      const result=await api<{run_id:string}>('/reconciliation/run',{method:'POST',body:JSON.stringify({mapping_id:id})});
      const [details,outcome]=await Promise.all([api<{duration_ms:number|null}>(`/reconciliation/runs/${result.run_id}`),api<{items:TargetResult[]}>(`/reconciliation/runs/${result.run_id}/results`)]);
      setExecution({state:'succeeded',detail:`Completed in ${details.duration_ms??0} ms`,results:outcome.items});
    }catch(e){setError((e as Error).message);setExecution({state:'failed',detail:(e as Error).message,results:[]});}
  }
  async function load(id:string){
    if(!id){setMappingId('');setName('');setSaved('');setActive(null);reset(emptyDiagram());return;}
    setError('');
    try{
      const row=await api<{name:string;branches:Branch[]}>('/mappings/'+id);
      const ids=[...new Set(row.branches.flatMap(b=>[...b.source_table_ids,b.target_table_id]))];
      let layout:Partial<Diagram>={};try{layout=JSON.parse(localStorage.getItem(layoutKey(id))||'{}');}catch{}
      let next:Diagram={branches:row.branches,placed:Object.fromEntries(ids.map(table=>[table,layout.placed?.[table]||{x:0,y:0}])),collapsed:layout.collapsed||{}};
      if(ids.some(table=>!layout.placed?.[table]))next=arrange(next,lookup);
      reset(next);setMappingId(id);setName(row.name);setSaved(JSON.stringify({name:row.name,branches:row.branches}));setActive(row.branches[0]?.target_table_id||null);setChecked(false);setShowIssues(false);
      setTimeout(()=>flow.fitView({padding:.2}),50);
    }catch(e){setError((e as Error).message);}
  }

  useEffect(()=>{function key(e:KeyboardEvent){
    const typing=['INPUT','SELECT','TEXTAREA'].includes((e.target as HTMLElement).tagName);const mod=e.ctrlKey||e.metaKey;
    if(e.key==='Escape'){setMenu(null);setFullscreen(false);}
    else if(mod&&e.key.toLowerCase()==='s'&&editable){e.preventDefault();if(!busy)void save();}
    else if(typing||!editable)return;
    else if(mod&&e.key.toLowerCase()==='z'){e.preventDefault();if(e.shiftKey)redo();else undo();}
    else if(mod&&e.key.toLowerCase()==='y'){e.preventDefault();redo();}
  }window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key);});

  function openMenu(e:{clientX:number;clientY:number;preventDefault:()=>void},item:Omit<Menu,'x'|'y'>){e.preventDefault();const box=canvas.current!.getBoundingClientRect();
    // Keep the menu inside the canvas when opened near its right or bottom edge.
    setMenu({x:Math.max(4,Math.min(e.clientX-box.left,box.width-222)),y:Math.max(4,Math.min(e.clientY-box.top,box.height-140)),...item});}
  const placedTables=Object.keys(diagram.placed).map(id=>lookup.get(id)).filter(Boolean) as Dataset[];
  const visible=(tables||[]).filter(t=>(t.name+' '+t.connection_name+' '+t.schema_name).toLowerCase().includes(query.toLowerCase()));
  const groups=[...new Set(visible.map(t=>t.connection_name))];
  const errors=issues?.filter(i=>i.level==='error').length||0,warnings=issues?.filter(i=>i.level==='warning').length||0;
  const sourceFields=branch?branch.source_table_ids.flatMap(id=>lookup.get(id)?.columns||[]):[];
  const targetFields=branch?lookup.get(branch.target_table_id)?.columns||[]:[];

  return <div className={`studio${fullscreen?' is-fullscreen':''}`} onClick={()=>setMenu(null)}>
    <div className="studio-toolbar">
      <div className="studio-title"><span className="eyebrow">MAPPING STUDIO</span>
        <select aria-label="Saved mapping" value={mappingId} onChange={e=>load(e.target.value)} disabled={busy}><option value="">New mapping</option>{mappings.map(m=><option key={m.id} value={m.id}>{m.name} · {m.mapping_type.replaceAll('_',' ')}</option>)}</select>
        <input aria-label="Mapping name" placeholder="Mapping name" value={name} maxLength={100} onChange={e=>setName(e.target.value)} disabled={!editable}/>
        {dirty&&diagram.branches.length>0&&<span className="badge">Unsaved changes</span>}
      </div>
      <div className="studio-actions">
        {editable&&<><button className="icon-button" onClick={undo} disabled={!canUndo} aria-label="Undo" title="Undo (Ctrl+Z)"><Undo2 size={18}/></button><button className="icon-button" onClick={redo} disabled={!canRedo} aria-label="Redo" title="Redo (Ctrl+Shift+Z)"><Redo2 size={18}/></button></>}
        <button className="icon-button" onClick={()=>{commit(d=>arrange(d,lookup));setTimeout(()=>flow.fitView({padding:.2}),50);}} disabled={!nodes.length} aria-label="Auto-arrange tables" title="Auto-arrange tables"><LayoutGrid size={18}/></button>
        <button className="icon-button" onClick={()=>setFullscreen(!fullscreen)} aria-label={fullscreen?'Exit full screen':'Full screen'} title={fullscreen?'Exit full screen (Esc)':'Full screen'}>{fullscreen?<Minimize2 size={18}/>:<Maximize2 size={18}/>}</button>
        <button className="button secondary" onClick={check}><ShieldCheck size={17}/>Validate</button>
        {editable&&<><button className="button secondary" onClick={save} disabled={busy||!dirty||!diagram.branches.length} title="Save (Ctrl+S)"><Save size={17}/>Save</button><button className="button" onClick={run} disabled={busy||!diagram.branches.length}><Play size={17}/>{execution.state==='running'?'Running…':dirty||!mappingId?'Save & run':'Run'}</button></>}
      </div>
    </div>
    {error&&<p className="notice studio-notice" role="alert">{error}</p>}
    <div className="studio-body">
      <aside className="studio-panel studio-sources" aria-label="Data sources">
        <h2>Data sources</h2>
        <label className="search-field"><Search size={16}/><input aria-label="Search tables" placeholder="Search tables…" value={query} onChange={e=>setQuery(e.target.value)}/></label>
        {tables===null?<p className="muted" role="status">Loading registered tables…</p>:!tables.length?<div className="studio-empty"><p>No registered tables yet. Test a PostgreSQL connection or approve a resource schema to register tables.</p><Link className="button secondary" href="/integrations">Open integrations</Link></div>:!visible.length?<p className="muted">No tables match “{query}”.</p>:
          groups.map(group=><section key={group}><h3>{group}</h3><ul>{visible.filter(t=>t.connection_name===group).map(t=>{const on=!!diagram.placed[t.id];return <li key={t.id} draggable={!on} onDragStart={e=>{e.dataTransfer.setData(DRAG_TYPE,t.id);e.dataTransfer.effectAllowed='copy';}} className={on?'is-placed':''}>
            <div><strong>{t.name}</strong><small>{t.schema_name} · {t.columns.length} columns{t.layer?` · ${t.layer}`:''}</small></div>
            <button className="icon-button" disabled={on} onClick={()=>place(t.id)} aria-label={on?`${t.name} is on the canvas`:`Add ${t.name} to the canvas`}>{on?<Check size={16}/>:<Plus size={16}/>}</button>
          </li>;})}</ul></section>)}
        {!!tables?.length&&<p className="form-help">Drag a table onto the canvas, or use its + button.</p>}
      </aside>
      <div ref={canvas} className="studio-canvas" onDragOver={e=>{if(e.dataTransfer.types.includes(DRAG_TYPE)){e.preventDefault();e.dataTransfer.dropEffect='copy';}}} onDrop={drop}>
        <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} minZoom={.2} fitView fitViewOptions={{padding:.15,maxZoom:1}} deleteKeyCode={editable?['Delete','Backspace']:null}
          nodesConnectable={editable} onConnect={(c:Connection)=>connect(c.source,c.target)}
          onNodeDragStop={(_,__,moved)=>commit(d=>({...d,placed:{...d.placed,...Object.fromEntries(moved.map(n=>[n.id,n.position]))}}))}
          onNodesDelete={removed=>removed.forEach(n=>discard(n.id))} onEdgesDelete={removed=>removed.forEach(e=>disconnect(e.source,e.target))}
          onEdgeClick={(_,edge)=>setActive(edge.target)} onNodeClick={(_,node)=>{if(diagram.branches.some(b=>b.target_table_id===node.id))setActive(node.id);}}
          onNodeContextMenu={(e,node)=>openMenu(e,{table:node.id})} onEdgeContextMenu={(e,edge)=>openMenu(e,{edge:{source:edge.source,target:edge.target}})} onPaneContextMenu={e=>openMenu(e,{})} onPaneClick={()=>setActive(null)}>
          <Background color="#CBD5E1" gap={20}/><Controls showInteractive={false}/><MiniMap pannable zoomable style={{width:150,height:100}} nodeColor={n=>(n.data as TableNodeData).role==='target'?'#14213D':'#00B8A9'} maskColor="rgb(245 247 250 / .7)"/>
        </ReactFlow>
        {!nodes.length&&<div className="studio-hint"><Link2 size={26}/><h3>Build your mapping visually</h3><p>Add a source table and a target table, then drag from the right edge of the source to the left edge of the target.</p></div>}
        {menu&&<div className="studio-menu" role="menu" style={{left:menu.x,top:menu.y}} onClick={e=>e.stopPropagation()}>
          {menu.table&&lookup.get(menu.table)&&<><button role="menuitem" onClick={()=>{void showRecords(lookup.get(menu.table!)!);setMenu(null);}}><Eye size={15}/>Preview records</button><button role="menuitem" onClick={()=>{const id=menu.table!;commit(d=>({...d,collapsed:{...d.collapsed,[id]:!d.collapsed[id]}}));setMenu(null);}}>{diagram.collapsed[menu.table]?<ChevronDown size={15}/>:<ChevronRight size={15}/>}{diagram.collapsed[menu.table]?'Expand columns':'Collapse columns'}</button>{editable&&<button role="menuitem" onClick={()=>{discard(menu.table!);setMenu(null);}}><Trash2 size={15}/>Remove from canvas</button>}</>}
          {menu.edge&&<><button role="menuitem" onClick={()=>{setActive(menu.edge!.target);setMenu(null);}}><Link2 size={15}/>Configure relationship</button>{editable&&<button role="menuitem" onClick={()=>{disconnect(menu.edge!.source,menu.edge!.target);setMenu(null);}}><Trash2 size={15}/>Remove link</button>}</>}
          {!menu.table&&!menu.edge&&<><button role="menuitem" onClick={()=>{commit(d=>arrange(d,lookup));setMenu(null);}}><LayoutGrid size={15}/>Auto-arrange tables</button><button role="menuitem" onClick={()=>{void flow.fitView({padding:.2});setMenu(null);}}><Maximize2 size={15}/>Fit to view</button></>}
        </div>}
      </div>
      <aside className="studio-panel studio-config" aria-label="Configuration">
        {branch?<>
          <div className="studio-config-head"><div><span className="eyebrow">RELATIONSHIP</span><h2>{branch.source_table_ids.map(id=>lookup.get(id)?.name).join(' + ')} → {lookup.get(branch.target_table_id)?.name}</h2></div><button className="icon-button" onClick={()=>setActive(null)} aria-label="Close configuration"><X size={18}/></button></div>
          <fieldset disabled={!editable}>
          <p className="form-help">{branch.source_table_ids.length>1?'Join type: left join. Every record of the first source is kept; additional sources are matched on the join columns below.':'One source compared with this target. Link another source to the same target to join them.'}</p>
          <h3>Column mappings</h3>
          {branch.columns.map((c,i)=><div className="studio-map-row" key={i}>
            <select aria-label={`Source column ${i+1}`} value={c.source} onChange={e=>change({columns:branch.columns.map((x,j)=>i===j?{...x,source:e.target.value}:x)})}><option value="">Source column</option>{sourceFields.map(f=><option key={f.qualified_name}>{f.qualified_name}</option>)}</select>
            <select aria-label={`Target column ${i+1}`} value={c.target} onChange={e=>change({columns:branch.columns.map((x,j)=>i===j?{...x,target:e.target.value}:x)})}><option value="">Target column</option>{targetFields.map(f=><option key={f.name}>{f.name}</option>)}</select>
            <select aria-label={`Transformation ${i+1}`} value={c.transformation} onChange={e=>change({columns:branch.columns.map((x,j)=>i===j?{...x,transformation:e.target.value}:x)})}>{['exact','trim','lower','upper'].map(t=><option key={t}>{t}</option>)}</select>
            <button className="icon-button" onClick={()=>{const columns=branch.columns.filter((_,j)=>i!==j);change({columns,business_keys:branch.business_keys.filter(k=>columns.some(x=>x.target===k)||branch.aggregations.some(a=>a.target===k))});}} aria-label={`Remove column mapping ${i+1}`}><Trash2 size={15}/></button>
          </div>)}
          <div className="action-row"><button className="button secondary" onClick={()=>change({columns:[...branch.columns,{source:'',target:'',transformation:'exact'}]})}><Plus size={15}/>Map column</button><button className="button secondary" onClick={()=>change(autoMap(lookup,branch.source_table_ids,branch.target_table_id))}>Auto-map matching names</button></div>
          <h3>Business keys</h3>
          <div className="schema-list">{[...new Set([...branch.columns.map(c=>c.target),...branch.aggregations.map(a=>a.target)])].filter(Boolean).map(key=><label className="checkbox-label" key={key}><input type="checkbox" checked={branch.business_keys.includes(key)} onChange={e=>change({business_keys:e.target.checked?[...branch.business_keys,key]:branch.business_keys.filter(k=>k!==key)})}/>{key}</label>)}</div>
          <h3>Reconciliation rules</h3>
          <div className="studio-rules"><label>Numeric tolerance<input type="number" min={0} step="any" value={branch.numeric_tolerance} onChange={e=>change({numeric_tolerance:Number(e.target.value)})}/></label><label>Timestamp tolerance (s)<input type="number" min={0} max={86400} value={branch.timestamp_tolerance_seconds} onChange={e=>change({timestamp_tolerance_seconds:Number(e.target.value)})}/></label></div>
          <label className="checkbox-label"><input type="checkbox" checked={branch.case_sensitive} onChange={e=>change({case_sensitive:e.target.checked})}/>Case-sensitive matching</label>
          <label className="checkbox-label"><input type="checkbox" checked={branch.nulls_equal} onChange={e=>change({nulls_equal:e.target.checked})}/>Two null values match</label>
          <AdvancedRules sources={sourceFields.map(f=>f.qualified_name)} targets={targetFields.map(f=>f.name)} joins={branch.joins} sourceFilters={branch.source_filters} targetFilters={branch.target_filters} aggregations={branch.aggregations} required={branch.required_columns} update={patch=>change(patch as Partial<Branch>)}/>
          <div className="action-row"><button className="button secondary" onClick={showCombined} disabled={previewing||!branch.columns.length||!branch.business_keys.length}><Eye size={15}/>Preview combined source</button><button className="button secondary" onClick={()=>{commit(d=>({...d,branches:d.branches.filter(b=>b!==branch)}));setActive(null);}}><Trash2 size={15}/>Remove relationship</button></div>
          </fieldset>
        </>:<>
          <span className="eyebrow">CONFIGURATION</span><h2>Select a relationship</h2>
          <p className="form-help">Select a link or a target table on the canvas to configure joins, column mappings, filters, aggregations and rules.</p>
          {editable&&placedTables.length>1&&<><h3>Link tables</h3><p className="form-help">The same action as drawing a line on the canvas, for touch and keyboard.</p>
            <label>Source<select aria-label="Link source table" value={linkFrom} onChange={e=>setLinkFrom(e.target.value)}><option value="">Choose a source</option>{placedTables.map(t=><option key={t.id} value={t.id}>{t.connection_name} · {t.name}</option>)}</select></label>
            <label>Target<select aria-label="Link target table" value={linkTo} onChange={e=>setLinkTo(e.target.value)}><option value="">Choose a target</option>{placedTables.filter(t=>t.id!==linkFrom).map(t=><option key={t.id} value={t.id}>{t.connection_name} · {t.name}</option>)}</select></label>
            <div className="action-row"><button className="button" disabled={!linkFrom||!linkTo} onClick={()=>{connect(linkFrom,linkTo);setLinkFrom('');setLinkTo('');}}><Link2 size={15}/>Link tables</button></div></>}
          {diagram.branches.length>0&&<><h3>Relationships</h3><ul className="studio-relationships">{diagram.branches.map(b=><li key={b.target_table_id}><button onClick={()=>setActive(b.target_table_id)}>{b.source_table_ids.map(id=>lookup.get(id)?.name||'missing table').join(' + ')} → {lookup.get(b.target_table_id)?.name||'missing table'}<small>{b.columns.length+b.aggregations.length} mapped · keys: {b.business_keys.join(', ')||'none'}</small></button></li>)}</ul></>}
        </>}
      </aside>
    </div>
    {preview&&<section className="studio-preview" aria-label="Data preview"><div className="card-heading"><h3>{preview.title}</h3><button className="icon-button" onClick={()=>setPreview(null)} aria-label="Close preview"><X size={18}/></button></div><InventoryTable data={preview.data} compact/></section>}
    {showIssues&&issues&&<section className="studio-preview" aria-label="Validation results"><div className="card-heading"><h3>Validation results</h3><button className="icon-button" onClick={()=>setShowIssues(false)} aria-label="Close validation results"><X size={18}/></button></div>
      {issues.length?<ul className="studio-issues">{issues.map((issue,i)=><li key={i} className={issue.level}>{issue.level==='error'?<CircleAlert size={16}/>:<TriangleAlert size={16}/>}<span><strong>{issue.level==='error'?'Error':'Warning'}</strong> · {issue.message}</span>{issue.target&&diagram.branches.some(b=>b.target_table_id===issue.target)&&<button className="button secondary" onClick={()=>setActive(issue.target!)}>Open</button>}</li>)}</ul>:<p className="studio-ok"><CircleCheck size={16}/>No problems found. Duplicate keys and many-to-many joins are checked against the data when the mapping runs.</p>}
    </section>}
    <footer className="studio-status" role="status">
      <span className={`studio-state ${execution.state}`}>{busy?<LoaderCircle size={15} className="spin"/>:execution.state==='succeeded'?<CircleCheck size={15}/>:execution.state==='failed'?<CircleAlert size={15}/>:null}<strong>Execution</strong>{execution.detail}</span>
      <span><strong>Records</strong>{execution.results.length?execution.results.map(r=>`${r.target_table}: ${r.source_count} source / ${r.target_count} target · ${r.matched} matched · ${r.mismatched} mismatched · ${r.missing} missing · ${r.extra} extra`).join('  |  '):'No run in this session'}</span>
      <span><strong>Validation</strong>{issues===null?'Not validated':<button className="studio-link" onClick={()=>setShowIssues(true)}>{errors} errors · {warnings} warnings</button>}</span>
      {execution.state==='succeeded'&&<Link className="studio-link" href="/reconciliation">Open detailed results</Link>}
    </footer>
  </div>;
}
