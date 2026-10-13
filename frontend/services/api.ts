export class ApiError extends Error {
  constructor(message:string, public status:number) {super(message);}
}
export async function api<T>(path:string, options:RequestInit = {}):Promise<T> {
  const response = await fetch(`/api${path}`, {...options, credentials:'same-origin', cache:'no-store', headers:{'Content-Type':'application/json',...options.headers}});
  if (!response.ok) {
    if(response.status===401 && typeof window!=='undefined' && location.pathname!=='/login') location.assign('/login');
    const body = await response.json().catch(()=>({detail:'The service is unavailable. Please retry.'}));
    const message=typeof body.detail==='string'?body.detail:Array.isArray(body.detail)?body.detail.slice(0,3).map((e:{loc?:unknown[];msg?:string})=>`${e.loc?.slice(1).join(' → ')||'Configuration'}: ${(e.msg||'Invalid value').replace(/^Value error, /,'')}`).join('; '):'Please check the form fields.';
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}
