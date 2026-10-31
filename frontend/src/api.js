const API_BASE = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';
const TOKEN_KEY = 'sahaay-access-token';
export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = token => token ? localStorage.setItem(TOKEN_KEY, token) : localStorage.removeItem(TOKEN_KEY);
export async function api(path, options={}) {
  const headers = new Headers(options.headers || {});
  if (!(options.body instanceof FormData)) headers.set('Content-Type','application/json');
  const token=getToken(); if(token) headers.set('Authorization',`Bearer ${token}`);
  const response=await fetch(`${API_BASE}${path}`,{...options,headers});
  const data=await response.json().catch(()=>({detail:'Unexpected server response'}));
  if(!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}
export async function registerApi(name,email,password){const result=await api('/api/auth/register',{method:'POST',body:JSON.stringify({name,email,password,role:'patient'})});setToken(result.access_token);return result.user;}
export async function loginApi(email,password){const result=await api('/api/auth/login',{method:'POST',body:JSON.stringify({email,password})});setToken(result.access_token);return result.user;}
export async function meApi(){return api('/api/auth/me');}
export async function logoutApi(){setToken(null);}
