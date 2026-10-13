'use client';
import {useMemo} from 'react';
import {useReactTable,getCoreRowModel,flexRender,type ColumnDef} from '@tanstack/react-table';
import {Database} from 'lucide-react';
import type {Inventory} from '@/types';
export function InventoryTable({data,compact=false}:{data:Inventory;compact?:boolean}) {
  const columns=useMemo<ColumnDef<Record<string,unknown>>[]>(()=>Object.keys(data.items[0]||{}).filter(k=>k!=='id').map(key=>({accessorKey:key,header:key.replaceAll('_',' '),cell:ctx=>{const value=ctx.getValue();return value===null?'—':key.endsWith('_at')?new Date(String(value)).toLocaleString():String(value);}})),[data.items]);
  const table=useReactTable({data:data.items,columns,getCoreRowModel:getCoreRowModel()});
  if(!data.items.length)return <div className="empty-state"><span className="empty-icon"><Database size={30}/></span><h3>No metadata yet</h3><p>Records will appear here as this workflow is implemented.</p></div>;
  return <div className={`table-scroll ${compact?'compact':''}`}><table><thead>{table.getHeaderGroups().map(g=><tr key={g.id}>{g.headers.map(h=><th key={h.id}>{flexRender(h.column.columnDef.header,h.getContext())}</th>)}</tr>)}</thead><tbody>{table.getRowModel().rows.map(r=><tr key={r.id}>{r.getVisibleCells().map(c=><td key={c.id}>{flexRender(c.column.columnDef.cell,c.getContext())}</td>)}</tr>)}</tbody></table></div>;
}
