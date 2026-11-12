import axios from 'axios';
import { nativeApiUrl,loginNative } from '../native/runtime';
const api=axios.create({withCredentials:true,headers:{'Content-Type':'application/json'},timeout:60000});
api.interceptors.request.use(config=>{
  config.url=nativeApiUrl(config.url||'/');
  config.baseURL=undefined;
  // The browser supplies a multipart boundary; never retain the JSON default for uploads.
  if(config.data instanceof FormData)config.headers.delete('Content-Type');
  return config;
});
api.interceptors.response.use(response=>response,error=>{
  const data=error.response?.data;
  if(Array.isArray(data?.detail))data.detail=data.detail.map((e:any)=>`${Array.isArray(e.loc)?e.loc.filter((p:any)=>p!=='body').join('.')+': ':''}${e.msg||'Invalid value'}`).join('; ');
  if(data?.code==='rest_no_route')data.detail='This workflow is not implemented in the WordPress migration yet. No action was performed.';
  if(data&&!data.detail&&typeof data.message==='string')data.detail=data.message;
  // No automatic mutation or GET retries. An ambiguous submission must retain its key.
  if(error.response?.status===401&&!String(error.config?.url||'').includes('/auth/'))loginNative();
  return Promise.reject(error);
});
export default api;
