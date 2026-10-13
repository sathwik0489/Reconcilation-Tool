'use client';
import Link from 'next/link';
import {useState} from 'react';
import {useRouter} from 'next/navigation';
import {LayoutDashboard,Database,Upload,Table2,Workflow,ScanLine,Search,ChartNoAxesCombined,Settings,ArrowUpRight,PanelLeftClose,Menu,LogOut,Layers3,Plug,Bell} from 'lucide-react';
import {api} from '@/services/api';
import type {User} from '@/types';
import {Brand} from './brand';
export const navigation = [
  {id:'dashboard',label:'Dashboard',icon:LayoutDashboard},{id:'integrations',label:'Integrations',icon:Plug},{id:'connections',label:'Data Connections',icon:Database},
  {id:'ingestion',label:'Data Ingestion',icon:Upload},{id:'tables',label:'Table Management',icon:Table2},
  {id:'mapping',label:'Visual Table Mapping',icon:Workflow},{id:'reconciliation',label:'Reconciliation',icon:ScanLine},
  {id:'explorer',label:'Data Explorer',icon:Search},{id:'reports',label:'Reports & Visualization',icon:ChartNoAxesCombined},
  {id:'settings',label:'Settings',icon:Settings}
];
export function Shell({user,section,children}:{user:User;section:string;children:React.ReactNode}) {
  const [open,setOpen]=useState(false);
  const [error,setError]=useState('');
  const [signingOut,setSigningOut]=useState(false);
  const [search,setSearch]=useState('');
  const [results,setResults]=useState<{label:string;href:string}[]|null>(null);
  const [notifications,setNotifications]=useState<{action:string;created_at:string}[]|null>(null);
  const router=useRouter();
  async function logout(){setSigningOut(true);try{await api('/auth/logout',{method:'POST'});router.replace('/login');}catch(e){setError((e as Error).message);setSigningOut(false);}}
  async function globalSearch(e:React.FormEvent){e.preventDefault();setNotifications(null);try{setResults(await api(`/search?q=${encodeURIComponent(search)}`));}catch(e){setError((e as Error).message);}}
  async function showNotifications(){setResults(null);if(notifications){setNotifications(null);return;}try{setNotifications(await api('/notifications'));}catch(e){setError((e as Error).message);}}
  return <div className="app-shell">
    <aside className={`sidebar ${open?'is-open':''}`}>
      <Link href="/dashboard" className="brand" aria-label="Reconviora dashboard"><Brand/></Link>
      <div className="workspace-label"><span className="workspace-dot"/>Metadata workspace<ArrowUpRight size={16}/></div>
      <div className="nav-caption">WORKSPACE</div>
      <nav aria-label="Main navigation">{navigation.map(({id,label,icon:Icon})=><Link onClick={()=>setOpen(false)} key={id} href={`/${id}`} className={section===id?'nav-item selected':'nav-item'} aria-current={section===id?'page':undefined}><Icon size={21}/><span>{label}</span></Link>)}</nav>
      <div className="sidebar-foot"><div className="foundation-pill">CONNECTED DATA WORKSPACE</div><p>Your data. A clearer picture.</p><div className="profile"><span className="avatar">{user.name.slice(0,2).toUpperCase()}</span><div><strong>{user.name}</strong><span>{user.role}</span></div><button className="icon-button" onClick={logout} disabled={signingOut} aria-label="Sign out"><LogOut size={20}/></button></div>{error&&<p role="alert">{error}</p>}</div>
    </aside>
    {open&&<button className="sidebar-backdrop" aria-label="Close navigation" onClick={()=>setOpen(false)}/>}
    <div className="main-wrap"><header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-menu" aria-label={open?'Close navigation':'Open navigation'} onClick={()=>setOpen(!open)}>{open?<PanelLeftClose size={23}/>:<Menu size={23}/>}</button><span>Workspace</span><span>/</span><strong>{navigation.find(n=>n.id===section)?.label}</strong></div><div className="topbar-tools"><form className="header-search" onSubmit={globalSearch}><Search size={16}/><input aria-label="Global search" placeholder="Search workspace…" value={search} onChange={e=>setSearch(e.target.value)} maxLength={100}/></form><button className="icon-button" onClick={showNotifications} aria-label="Notifications"><Bell size={20}/></button><Link className="header-profile" href="/settings"><span className="avatar">{user.name.slice(0,2).toUpperCase()}</span><span>{user.name}</span></Link></div>{results&&<div className="popover"><button className="button secondary" onClick={()=>setResults(null)}>Close search</button>{results.length?results.map((r,i)=><Link key={i} href={r.href} onClick={()=>setResults(null)}>{r.label}</Link>):<p>No matching metadata.</p>}</div>}{notifications&&<div className="popover"><h3>Workspace activity</h3>{notifications.length?notifications.map((n,i)=><p key={i}>{n.action} · {new Date(n.created_at).toLocaleString()}</p>):<p>No recent notifications.</p>}</div>}</header>
    <main id="main-content">{children}</main><footer className="footer"><span>RECONVIORA / CONNECTED DATA</span><span>Connect. Validate. Reconcile.</span></footer></div>
  </div>;
}
