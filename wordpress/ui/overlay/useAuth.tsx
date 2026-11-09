import { useState,useEffect,createContext,useContext,ReactNode,useCallback } from 'react';
import api from '../api/client';
import { User } from '../types';
export interface AccessInfo {passwordRequired:boolean;passwordSet:boolean;source:string;hint:string;}
interface AuthContextType {user:User|null;loading:boolean;access:AccessInfo;login:(password:string)=>Promise<true|any>;loginAsAdmin:()=>Promise<true|any>;logout:()=>Promise<void>;isAuthenticated:boolean;passwordRequired:boolean;noteAccess:(required:boolean)=>void;refreshAccess:()=>Promise<void>;}
const DEFAULT_ACCESS:AccessInfo={passwordRequired:true,passwordSet:false,source:'native',hint:'Use the application operator password configured in WordPress Settings → Sender Suite.'};
const AuthContext=createContext<AuthContextType>({user:null,loading:true,access:DEFAULT_ACCESS,login:async()=>false,loginAsAdmin:async()=>false,logout:async()=>{},isAuthenticated:false,passwordRequired:true,noteAccess:()=>{},refreshAccess:async()=>{}});
export function AuthProvider({children}:{children:ReactNode}){
 const [user,setUser]=useState<User|null>(null);const [loading,setLoading]=useState(true);const [access,setAccess]=useState(DEFAULT_ACCESS);
 const refreshAccess=useCallback(async()=>{try{const {data}=await api.get('/auth/access');setAccess({passwordRequired:!!data.password_required,passwordSet:!!data.password_set,source:data.source||'native',hint:data.hint||DEFAULT_ACCESS.hint});}catch{/* Fail closed; never infer passwordless access from a network error. */}},[]);
 const checkAuth=useCallback(async()=>{try{const {data}=await api.get<User>('/auth/me');setUser(data);}catch{setUser(null);}finally{setLoading(false);}},[]);
 useEffect(()=>{refreshAccess();checkAuth();},[refreshAccess,checkAuth]);
 // Remove any password retained by an older copied frontend. New sessions are HttpOnly-cookie only.
 useEffect(()=>{try{window.sessionStorage.removeItem('sendsms.lastPassword');}catch{}},[]);
 const login=useCallback(async(password:string):Promise<true|any>=>{try{const {data}=await api.post('/auth/admin',{password});if(!data?.success)return new Error(data?.message||'Sign-in rejected');await checkAuth();await refreshAccess();return true;}catch(error){return error||new Error('Sign-in failed');}},[checkAuth,refreshAccess]);
 const loginAsAdmin=useCallback(()=>login(''),[login]);
 const logout=async()=>{try{await api.post('/auth/logout');}finally{setUser(null);}};
 return <AuthContext.Provider value={{user,loading,access,login,loginAsAdmin,logout,isAuthenticated:!!user,passwordRequired:access.passwordRequired,noteAccess:()=>{void refreshAccess();},refreshAccess}}>{children}</AuthContext.Provider>;
}
export const useAuth=()=>useContext(AuthContext);
