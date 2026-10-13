'use client';

import {useEffect,useState} from 'react';
import {Brand} from './brand';

export function SplashScreen({children}:{children:React.ReactNode}) {
  const [phase,setPhase]=useState<'opening'|'leaving'|'ready'>('opening');
  const active=phase!=='ready';

  useEffect(()=>{
    const exit=window.setTimeout(()=>setPhase('leaving'),1600);
    const finish=window.setTimeout(()=>setPhase('ready'),1850);
    return ()=>{window.clearTimeout(exit);window.clearTimeout(finish);};
  },[]);

  useEffect(()=>{
    if(!active)return;
    const previous=document.body.style.overflow;
    document.body.style.overflow='hidden';
    return ()=>{document.body.style.overflow=previous;};
  },[active]);

  return <>
    <div className="startup-content" inert={active} aria-hidden={active||undefined}>{children}</div>
    {active&&<div className={`splash-screen ${phase==='leaving'?'splash-leaving':''}`} role="status" aria-live="polite" aria-label="Reconviora is starting">
      <div className="splash-content">
        <div className="splash-eyebrow">YOUR CONNECTED DATA WORKSPACE</div>
        <div className="splash-logo"><Brand/></div>
        <div className="splash-loader" aria-hidden="true"><span/><span/><span/></div>
        <p>Opening your workspace…</p>
      </div>
      <div className="splash-footer">DATA RECONCILIATION & VISUALIZATION</div>
    </div>}
  </>;
}
