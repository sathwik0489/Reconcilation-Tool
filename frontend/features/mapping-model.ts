// Pure mapping-diagram logic for the Mapping Studio: linking tables, layout and validation. No React here.
export type Column={name:string;qualified_name:string;data_type:string;primary_key:boolean};
export type Dataset={id:string;name:string;schema_name:string;classification:string;layer:string|null;integration_id:string;connection_name:string;application:string|null;columns:Column[]};
export type ColumnMap={source:string;target:string;transformation:string};
export type Condition={column:string;operator:string;value:unknown};
export type Branch={source_table_ids:string[];target_table_id:string;columns:ColumnMap[];business_keys:string[];joins:{left:string;right:string}[];source_filters:Condition[];target_filters:Condition[];aggregations:{source:string;target:string;function:string}[];numeric_tolerance:number;timestamp_tolerance_seconds:number;case_sensitive:boolean;nulls_equal:boolean;required_columns:string[]};
export type Position={x:number;y:number};
// One undoable snapshot of the diagram: the rules, where tables sit, and which are collapsed.
export type Diagram={branches:Branch[];placed:Record<string,Position>;collapsed:Record<string,boolean>};
export type Issue={level:'error'|'warning';message:string;target?:string};

export const emptyDiagram=():Diagram=>({branches:[],placed:{},collapsed:{}});
const emptyBranch=():Branch=>({source_table_ids:[],target_table_id:'',columns:[],business_keys:[],joins:[],source_filters:[],target_filters:[],aggregations:[],numeric_tolerance:0,timestamp_tolerance_seconds:0,case_sensitive:true,nulls_equal:true,required_columns:[]});

type Lookup=Map<string,Dataset>;

export function autoMap(tables:Lookup,sourceIds:string[],targetId:string):Pick<Branch,'columns'|'business_keys'>{
  const target=tables.get(targetId);const taken=new Set<string>();const columns:ColumnMap[]=[];
  for(const id of sourceIds)for(const column of tables.get(id)?.columns||[]){
    if(taken.has(column.name)||!target?.columns.some(c=>c.name===column.name))continue;
    taken.add(column.name);columns.push({source:column.qualified_name,target:column.name,transformation:'exact'});
  }
  const keys=target?.columns.filter(c=>c.primary_key&&taken.has(c.name)).map(c=>c.name)||[];
  return {columns,business_keys:keys.length?keys:columns.slice(0,1).map(c=>c.target)};
}

function guessJoin(tables:Lookup,earlier:string[],added:string){
  const right=tables.get(added)?.columns||[];
  const left=earlier.flatMap(id=>tables.get(id)?.columns||[]);
  // Prefer a foreign-key shape (transactions.customer_id = customers.id) over a merely shared name such as id = id.
  const table=tables.get(added)?.name.toLowerCase()||'';const key=right.find(c=>c.primary_key)||right.find(c=>c.name.toLowerCase()==='id');
  const foreign=key&&left.find(l=>[table+'_'+key.name,table.replace(/s$/,'')+'_'+key.name].includes(l.name.toLowerCase()));
  if(foreign&&key)return {left:foreign.qualified_name,right:key.qualified_name};
  const shared=right.filter(r=>left.some(l=>l.name===r.name));
  const pick=shared.find(c=>/id$/i.test(c.name))||shared[0];
  return pick?{left:left.find(l=>l.name===pick.name)!.qualified_name,right:pick.qualified_name}:{left:'',right:''};
}

/** Make `sourceId` a source of the branch that targets `targetId`, creating that branch if needed. */
export function link(diagram:Diagram,tables:Lookup,sourceId:string,targetId:string):Diagram{
  if(sourceId===targetId)return diagram;
  const existing=diagram.branches.find(b=>b.target_table_id===targetId);
  if(!existing)return {...diagram,branches:[...diagram.branches,{...emptyBranch(),source_table_ids:[sourceId],target_table_id:targetId,...autoMap(tables,[sourceId],targetId)}]};
  if(existing.source_table_ids.includes(sourceId))return diagram;
  // The added source fills target columns that are still unmapped; existing mappings are left alone.
  const mapped=new Set([...existing.columns.map(c=>c.target),...existing.aggregations.map(a=>a.target)]);
  const extra=autoMap(tables,[sourceId],targetId).columns.filter(c=>!mapped.has(c.target));
  const updated={...existing,source_table_ids:[...existing.source_table_ids,sourceId],columns:[...existing.columns,...extra],joins:[...existing.joins,guessJoin(tables,existing.source_table_ids,sourceId)]};
  return {...diagram,branches:diagram.branches.map(b=>b===existing?updated:b)};
}

/** Remove one source from a branch along with every rule that referred to its columns. */
export function unlink(diagram:Diagram,tables:Lookup,sourceId:string,targetId:string):Diagram{
  const prefix=(tables.get(sourceId)?.name||'\u0000')+'.';
  const branches=diagram.branches.flatMap(b=>{
    if(b.target_table_id!==targetId||!b.source_table_ids.includes(sourceId))return [b];
    const index=b.source_table_ids.indexOf(sourceId);const sources=b.source_table_ids.filter(id=>id!==sourceId);
    if(!sources.length)return [];
    const joins=b.joins.filter((_,i)=>i!==Math.max(index-1,0)).map(j=>j.left.startsWith(prefix)?{...j,left:''}:j);
    const columns=b.columns.filter(c=>!c.source.startsWith(prefix));const aggregations=b.aggregations.filter(a=>!a.source.startsWith(prefix));
    const targets=new Set([...columns.map(c=>c.target),...aggregations.map(a=>a.target)]);
    return [{...b,source_table_ids:sources,joins,columns,aggregations,business_keys:b.business_keys.filter(k=>targets.has(k)),source_filters:b.source_filters.filter(f=>!f.column.startsWith(prefix))}];
  });
  return {...diagram,branches};
}

export function removeTable(diagram:Diagram,tables:Lookup,id:string):Diagram{
  let next:Diagram={...diagram,branches:diagram.branches.filter(b=>b.target_table_id!==id)};
  for(const branch of diagram.branches)if(branch.source_table_ids.includes(id)&&branch.target_table_id!==id)next=unlink(next,tables,id,branch.target_table_id);
  const {[id]:_removed,...placed}=next.placed;const {[id]:_state,...collapsed}=next.collapsed;
  return {...next,placed,collapsed};
}

export function roleOf(diagram:Diagram,id:string):'source'|'target'|'both'|'unlinked'{
  const source=diagram.branches.some(b=>b.source_table_ids.includes(id)),target=diagram.branches.some(b=>b.target_table_id===id);
  return source&&target?'both':source?'source':target?'target':'unlinked';
}

// Matches the node styles: header, then up to nine 26px column rows before the list scrolls.
export function nodeHeight(table:Dataset|undefined,collapsed:boolean){return 78+(collapsed?0:Math.min(table?.columns.length||0,9)*26+10);}

/** Sources in a left column, targets on the right, unlinked tables below the sources. */
export function arrange(diagram:Diagram,tables:Lookup):Diagram{
  const ids=Object.keys(diagram.placed);const placed:Record<string,Position>={};
  const columns:Record<string,string[]>={left:[],right:[]};
  for(const id of ids){const role=roleOf(diagram,id);columns[role==='target'||role==='both'?'right':'left'].push(id);}
  columns.left.sort((a,b)=>Number(roleOf(diagram,a)==='unlinked')-Number(roleOf(diagram,b)==='unlinked'));
  for(const [side,x] of [['left',40],['right',400]] as const){let y=40;for(const id of columns[side]){placed[id]={x,y};y+=nodeHeight(tables.get(id),!!diagram.collapsed[id])+28;}}
  return {...diagram,placed};
}

function family(type:string){
  const t=type.toUpperCase();
  if(/INT|NUMERIC|DECIMAL|FLOAT|DOUBLE|REAL/.test(t))return 'number';
  if(/TIMESTAMP|DATE|TIME/.test(t))return 'time';
  if(/BOOL/.test(t))return 'boolean';
  return 'text';
}

/** The same structural rules the server enforces on save, plus type warnings, so problems show before saving. */
export function validate(diagram:Diagram,tables:Lookup,name:string):Issue[]{
  const issues:Issue[]=[];const add=(level:Issue['level'],message:string,target?:string)=>issues.push({level,message,target});
  if(!name.trim())add('error','Name the mapping before saving.');
  if(!diagram.branches.length)add('error','Connect a source table to a target table.');
  if(diagram.branches.length>10)add('error','A mapping supports at most 10 targets.');
  if(diagram.branches.length>1&&(diagram.branches.some(b=>b.source_table_ids.length!==1)||new Set(diagram.branches.map(b=>b.source_table_ids[0])).size!==1))
    add('error','With several targets, every target must use the same single source table.');
  for(const b of diagram.branches){
    const target=tables.get(b.target_table_id);const label=target?.name||'a target';const at=b.target_table_id;
    if(!target){add('error','A target table is no longer available.',at);continue;}
    const sources=b.source_table_ids.map(id=>tables.get(id));
    if(sources.some(s=>!s)){add('error',`${label}: a source table is no longer available.`,at);continue;}
    if(b.source_table_ids.length>5)add('error',`${label}: at most 5 source tables can be joined.`,at);
    const names=sources.map(s=>s!.name);
    if(new Set(names).size!==names.length)add('error',`${label}: joined source tables must have different names.`,at);
    const sourceColumns=new Map(sources.flatMap(s=>s!.columns.map(c=>[c.qualified_name,c] as const)));
    const targetColumns=new Map(target.columns.map(c=>[c.name,c] as const));
    if(!b.columns.length)add('error',`${label}: map at least one column.`,at);
    for(const c of b.columns){
      const source=sourceColumns.get(c.source),destination=targetColumns.get(c.target);
      if(!source||!destination){add('error',`${label}: a column mapping is incomplete.`,at);continue;}
      const from=family(source.data_type),to=family(destination.data_type);
      if(from!==to)add('warning',`${label}: ${c.source} (${source.data_type}) is compared with ${c.target} (${destination.data_type}).`,at);
      if(c.transformation!=='exact'&&from!=='text')add('error',`${label}: ${c.transformation} applies to text, but ${c.source} is ${source.data_type}.`,at);
    }
    for(const a of b.aggregations)if(!sourceColumns.has(a.source)||!targetColumns.has(a.target))add('error',`${label}: an aggregation is incomplete.`,at);
    const targets=[...b.columns.map(c=>c.target),...b.aggregations.map(a=>a.target)].filter(Boolean);
    if(new Set(targets).size!==targets.length)add('error',`${label}: each target column can be mapped or aggregated only once.`,at);
    if(!b.business_keys.length)add('error',`${label}: choose at least one business key.`,at);
    else if(b.business_keys.some(k=>!targets.includes(k)))add('error',`${label}: business keys must be mapped target columns.`,at);
    if(b.joins.length!==b.source_table_ids.length-1)add('error',`${label}: add one join for each additional source table.`,at);
    b.joins.forEach((j,i)=>{
      const joined=names[i+1],earlier=names.slice(0,i+1);
      if(!sourceColumns.has(j.left)||!sourceColumns.has(j.right))add('error',`${label}: choose both columns for join ${i+1}.`,at);
      else if(!j.right.startsWith(joined+'.')||!earlier.some(n=>j.left.startsWith(n+'.')))add('error',`${label}: join ${i+1} must connect ${joined||'the added table'} (right) to an earlier source (left).`,at);
    });
    if(b.source_filters.some(f=>!sourceColumns.has(f.column))||b.target_filters.some(f=>!targetColumns.has(f.column)))add('error',`${label}: a filter has no column selected.`,at);
  }
  return issues;
}
