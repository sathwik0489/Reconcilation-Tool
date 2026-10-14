'use client';
import {useEffect,useState,useCallback} from 'react';
import {useRouter} from 'next/navigation';
import {Search,RefreshCw,Layers3,Info} from 'lucide-react';
import {useSession} from '@/hooks/use-session';
import {api,ApiError} from '@/services/api';
import {Shell} from '@/components/shell';
import {Loading,ErrorState} from '@/components/states';
import {InventoryTable} from '@/components/inventory';
import {Dashboard} from './dashboard';
import {Settings} from './settings';
import {Integrations} from './integrations';
import {DataWorkflows} from './data-workflows';
import {MappingStudio} from './mapping-studio';
import type {Summary,Status,Inventory} from '@/types';
const paths:Record<string,string>={connections:'/connections',tables:'/tables',ingestion:'/ingestion/runs',mapping:'/mappings',reconciliation:'/reconciliation/runs',explorer:'/tables',reports:'/reconciliation/runs'};
export function Workspace({section}:{section:string}){
  const {user,setUser,error:sessionError}=useSession();
  const [status,setStatus]=useState<Status|null>(null),[summary,setSummary]=useState<Summary|null>(null),[data,setData]=useState<Inventory|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[page,setPage]=useState(1),[search,setSearch]=useState(''),[query,setQuery]=useState('');
  const router=useRouter();
  const load=useCallback(async()=>{if(!user)return;setBusy(true);setError('');try{
    const result=await api<Status>('/system/status');setStatus(result);
    if(section==='dashboard')setSummary(await api<Summary>('/dashboard/summary'));
    else if(paths[section])setData(await api<Inventory>(`${paths[section]}?page=${page}&page_size=${user.preferences.page_size||25}&search=${encodeURIComponent(query)}`));
  }catch(e){if(e instanceof ApiError&&e.status===401)router.replace('/login');else setError((e as Error).message);}finally{setBusy(false);}},[section,user,page,query,router]);
  useEffect(()=>{void load();},[load]);
  if(sessionError)return <ErrorState message={sessionError} retry={()=>location.reload()}/>;
  if(!user)return <Loading/>;
  const module=status?.modules.find(m=>m.id===section);
  if(section==='integrations'&&!error)return <Shell user={user} section={section}><Integrations user={user}/></Shell>;
  if(section==='connections'&&!error)return <Shell user={user} section={section}><Integrations user={user} databaseOnly/></Shell>;
  if(section==='mapping'&&!error)return <Shell user={user} section={section}><MappingStudio user={user}/></Shell>;
  if(['tables','explorer','reconciliation','reports','ingestion'].includes(section)&&!error)return <Shell user={user} section={section}><DataWorkflows section={section} user={user}/></Shell>;
  return <Shell user={user} section={section}>{error?<ErrorState message={error} retry={load}/>:!status?<Loading/>:section==='dashboard'&&summary?<Dashboard summary={summary} status={status} user={user} refresh={load} busy={busy}/>:section==='settings'?<Settings user={user} onSaved={setUser}/>:module?<><div className="page-heading"><div><div className="eyebrow">METADATA WORKSPACE</div><h1>{module.name}</h1><p>{module.description}</p></div><button className="button secondary" onClick={load} disabled={busy}><RefreshCw size={18} className={busy?'spin':''}/>Refresh</button></div><div className="phase-notice"><Info size={26}/><div><h2>Foundation ready · Workflow planned for Phase {module.phase}</h2><p>This page reads persisted metadata from the backend. Creating connections, ingesting data, mapping, and execution will be implemented in their designated phases.</p></div><span className="badge">PHASE {module.phase}</span></div><section className="card"><div className="card-heading"><div><h2>{section==='explorer'?'Registered datasets':section==='reports'?'Run history':'Metadata inventory'}</h2><p>{data?.total??module.count} records in PostgreSQL</p></div><Layers3 size={23}/></div><form className="search-form" onSubmit={e=>{e.preventDefault();setPage(1);setQuery(search);}}><label className="search-field"><Search size={20}/><input aria-label="Search metadata" placeholder="Search metadata…" value={search} maxLength={100} onChange={e=>setSearch(e.target.value)}/></label><button className="button secondary" disabled={busy}>Search</button></form>{data?<InventoryTable data={data} compact={user.preferences.compact_tables}/>:<Loading/>}<div className="pagination"><span>{data?.total||0} records · Page {page}</span><button className="button secondary" disabled={page===1||busy} onClick={()=>setPage(page-1)}>Previous</button><button className="button secondary" disabled={!data||page*data.page_size>=data.total||busy} onClick={()=>setPage(page+1)}>Next</button></div></section></>:<Loading/>}</Shell>;
}
