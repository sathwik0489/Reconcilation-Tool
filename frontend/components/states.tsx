import {LoaderCircle,Info} from 'lucide-react';
export function Loading(){return <div className="screen-state" role="status"><LoaderCircle className="spin" size={30}/><p>Loading your workspace…</p></div>;}
export function ErrorState({message,retry}:{message:string;retry?:()=>void}){return <div className="screen-state" role="alert"><Info size={30}/><p>{message}</p>{retry&&<button className="button" onClick={retry}>Try again</button>}</div>;}
