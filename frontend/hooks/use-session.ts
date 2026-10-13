'use client';
import {useEffect,useState} from 'react';
import {useRouter} from 'next/navigation';
import {api,ApiError} from '@/services/api';
import type {User} from '@/types';
export function useSession() {
  const [user,setUser] = useState<User|null>(null);
  const [error,setError] = useState('');
  const router = useRouter();
  useEffect(()=> {let active = true; api<User>('/auth/me').then(u=>{if(active)setUser(u);}).catch(e=>{
    if(!active)return; if(e instanceof ApiError && e.status===401) router.replace('/login'); else setError(e.message);
  }); return ()=>{active=false;};},[router]);
  return {user,setUser,error};
}
