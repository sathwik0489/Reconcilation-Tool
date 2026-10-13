import {notFound} from 'next/navigation';
import {Workspace} from '@/features/workspace';
const sections=['dashboard','integrations','connections','ingestion','tables','mapping','reconciliation','explorer','reports','settings'];
export default async function Page({params}:{params:Promise<{section:string}>}){const {section}=await params;if(!sections.includes(section))notFound();return <Workspace key={section} section={section}/>;}
